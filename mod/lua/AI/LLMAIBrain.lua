-- LLM AI Brain — mod/lua/AI/LLMAIBrain.lua
--
-- Extends FAF's BaseAIBrain so that the standard AI managers (factory,
-- engineer, platoon, structure) handle all execution.  Our LLM layer is an
-- additional strategic overlay:
--
--   Base AI (inherited)  — builds, expands, attacks, defends autonomously
--   + LLM Strategy       — periodic LLM queries adjust priorities
--   + ChatHandler        — responds to player chat (LLM or offline)
--   + EventTrigger       — detects critical events → immediate LLM query
--   + ReflexLayer        — instant ally-mode reactions (no LLM)
--
-- When the LLM bridge is offline, the bot plays as a standard FAF AI plus
-- reflex / chat layers — already much better than the hardcoded build order.

-- -------------------------------------------------------------------------
-- Import base brain from FAF
-- base-ai.lua exports the class as "AIBrain" (not "BaseAIBrain")
-- -------------------------------------------------------------------------
local BaseBrain = import("/lua/aibrains/base-ai.lua").AIBrain

local LLMBridge = import("/mods/supcom-llm-ai-bot/lua/AI/LLMBridge.lua")
local FileIPC   = import("/mods/supcom-llm-ai-bot/lua/AI/FileIPC.lua")
local ObservationHandlers = import("/mods/supcom-llm-ai-bot/lua/AI/ObservationHandlers.lua")
local BuildOrderUEF = import("/mods/supcom-llm-ai-bot/lua/AI/BuildOrderUEF.lua")
local EconomyManager = import("/mods/supcom-llm-ai-bot/lua/AI/EconomyManager.lua")

-- -------------------------------------------------------------------------
-- Offline chat responder — canned replies when LLM is unavailable
-- -------------------------------------------------------------------------
local function OfflineChat(msg)
    if not msg then return nil end
    local lower = string.lower(msg)

    if string.find(lower, "привет") or string.find(lower, "hello")
       or string.find(lower, "hi") or string.find(lower, "хай") then
        return "Привет! Работаю в автономном режиме."
    end
    if string.find(lower, "помог") or string.find(lower, "помощ")
       or string.find(lower, "help") then
        return "Помогаю как могу. Строю и защищаю базу."
    end
    if string.find(lower, "атак") or string.find(lower, "attack")
       or string.find(lower, "rush") then
        return "Понял, атакую."
    end
    if string.find(lower, "защит") or string.find(lower, "defend")
       or string.find(lower, "оборон") then
        return "Понял, перехожу в оборону."
    end
    if string.find(lower, "стоишь") or string.find(lower, "idle")
       or string.find(lower, "ничего") then
        return "Работаю. LLM-сервер не подключён — автономный режим."
    end
    if string.find(lower, "делаешь") or string.find(lower, "делаете")
       or string.find(lower, "doing") or string.find(lower, "what") then
        return "Строю базу, развиваю экономику, готовлю армию. Автономный режим."
    end
    if string.find(lower, "строишь") or string.find(lower, "строите")
       or string.find(lower, "building") or string.find(lower, "build") then
        return "Строю базу и фабрики. Ставлю экстракторы и генераторы."
    end
    if string.find(lower, "план") or string.find(lower, "plan")
       or string.find(lower, "стратег") or string.find(lower, "strateg") then
        return "Стратегия: развитие экономики, потом наступление."
    end
    if string.find(lower, "масс") or string.find(lower, "mass")
       or string.find(lower, "ресурс") or string.find(lower, "resource") then
        return "Работаю над экономикой. Ставлю экстракторы."
    end
    if string.find(lower, "враг") or string.find(lower, "enemy")
       or string.find(lower, "противник") then
        return "Отслеживаю угрозы. Готовлю оборону."
    end
    if string.find(lower, "готов") or string.find(lower, "ready") then
        return "Почти готов. Наращиваю армию."
    end

    return "Понял. Продолжаю работу."
end

-- =========================================================================
---@class LLMAIBrain : BaseAIBrain
-- =========================================================================
LLMAIBrain = Class(BaseBrain) {

    -- ---------------------------------------------------------------------
    -- OnCreateAI — called before any units exist
    -- ---------------------------------------------------------------------
    OnCreateAI = function(self, planName, ...)
        -- Let the base brain set up everything: plans, managers, etc.
        BaseBrain.OnCreateAI(self, planName)

        -- LLM config (overridden by Python handshake after connect)
        self.LLMConfig = {
            mode               = "opponent",
            difficulty         = "normal",
            playstyle          = "balanced",
            poll_interval_fast = 20,
            poll_interval_deep = 60,
        }

        -- Runtime state for event triggers
        self.LastArmySize      = 0
        self.LastArmyCheckTick = 0
        self.EconomyStallTick  = nil
        self.PendingChat       = {}

        LOG("[LLMAIBrain] Created (plan=" .. tostring(planName) .. ")")
    end,

    -- ---------------------------------------------------------------------
    -- OnBeginSession — units and resources exist; base AI starts threads
    -- ---------------------------------------------------------------------
    OnBeginSession = function(self)
        -- Base AI starts InitialAIThread → EvaluateAIThread + ExecuteAIThread
        BaseBrain.OnBeginSession(self)

        -- Fork our additional threads on top of the base AI
        self:ForkThread(self.LLMBridgeThread)
        self:ForkThread(self.ChatHandlerThread)
        self:ForkThread(self.EventTriggerThread)
        self:ForkThread(self.EconomyThread)

        -- Reflex layer for ally mode
        if self.LLMConfig.mode == "ally" then
            self:ForkThread(self.ReflexThread)
        end

        LOG("[LLMAIBrain] Session started — LLM threads forked on top of base AI")
    end,

    -- ---------------------------------------------------------------------
    -- EconomyThread — grows the economy the base AI neglects: idle engineers
    -- build mass extractors on open markers (the fix for the ~0.9 mass/s stall),
    -- plus mass/energy stall detection. Runs every 3s on top of the base AI.
    -- ---------------------------------------------------------------------
    EconomyThread = function(self)
        WaitSeconds(8)  -- let the base AI place the initial structures first
        LOG("[LLMAIBrain] EconomyThread started (engineer mex expansion)")
        while not self.Dead do
            pcall(EconomyManager.Tick, self)
            WaitSeconds(3)
        end
    end,

    -- ---------------------------------------------------------------------
    -- LLMBridgeThread — periodic LLM communication
    -- Sends game state snapshots, receives strategic commands.
    -- When offline, the base AI runs autonomously (which is already good).
    -- ---------------------------------------------------------------------
    LLMBridgeThread = function(self)
        WaitSeconds(10)  -- let base AI stabilise

        local fast_interval  = self.LLMConfig.poll_interval_fast or 20
        local deep_interval  = self.LLMConfig.poll_interval_deep or 60
        local ticks_since_deep = 0

        while not self.Dead do
            -- Retry DLL connection (late injection path)
            LLMBridge.RetryConnect()

            if LLMBridge.IsConnected() then
                local trigger = (ticks_since_deep * fast_interval >= deep_interval)
                                and "deep_periodic" or "periodic"

                LLMBridge.SendSnapshot(self, trigger, nil, self.PendingChat)
                self.PendingChat = {}

                if trigger == "deep_periodic" then
                    ticks_since_deep = 0
                else
                    ticks_since_deep = ticks_since_deep + 1
                end

                -- ReAct loop: poll for messages until timeout or command received
                -- Python may send observation_request / action_execute before the
                -- final command. We handle each one and reply immediately.
                local react_timeout = 30  -- seconds
                local react_start   = GetGameTimeSeconds()
                local got_command    = false

                while not got_command and not self.Dead do
                    local elapsed = GetGameTimeSeconds() - react_start
                    if elapsed >= react_timeout then
                        LOG("[LLMAIBrain] ReAct timeout after " .. elapsed .. "s")
                        break
                    end

                    local msg = LLMBridge.PollCommand()
                    if msg then
                        local msg_type = msg.type or "command"

                        if msg_type == "observation_request" then
                            self:HandleObservationRequest(msg)
                        elseif msg_type == "action_execute" then
                            self:HandleActionExecute(msg)
                        elseif msg_type == "command" then
                            -- Final batch command from the ReAct loop
                            self:ApplyLLMDecision(msg)
                            got_command = true
                        else
                            LOG("[LLMAIBrain] Unknown msg type: " .. tostring(msg_type))
                        end
                    else
                        WaitTicks(2)  -- 200ms poll interval
                    end
                end

            elseif FileIPC.IsAvailable() then
                -- File IPC fallback: snapshot via file, command via file
                local trigger = (ticks_since_deep * fast_interval >= deep_interval)
                                and "deep_periodic" or "periodic"

                LOG("[LLMAIBrain] FileIPC: sending snapshot (trigger=" .. trigger .. ")")
                FileIPC.WriteSnapshot(self, trigger, nil, self.PendingChat)
                self.PendingChat = {}

                if trigger == "deep_periodic" then
                    ticks_since_deep = 0
                else
                    ticks_since_deep = ticks_since_deep + 1
                end

                -- Short poll for command (Python writes it via UI relay).
                -- Max 5 ticks (~500ms real time) — do NOT block the sim thread
                -- for 30s if Python is offline. Next cycle will poll again.
                local POLL_MAX_TICKS = 5
                for i = 1, POLL_MAX_TICKS do
                    if self.Dead then break end
                    local cmd = FileIPC.ReadCommand()
                    if cmd then
                        LOG("[LLMAIBrain] FileIPC: command received")
                        self:ApplyLLMDecision(cmd)
                        break
                    end
                    WaitTicks(1)  -- 100ms
                end
            end

            -- Adaptive polling: Python may override the next wait interval
            local next_wait = self._poll_override or fast_interval
            self._poll_override = nil
            WaitSeconds(next_wait)
        end
    end,

    -- ---------------------------------------------------------------------
    -- HandleObservationRequest — execute an observation tool and reply
    -- ---------------------------------------------------------------------
    HandleObservationRequest = function(self, msg)
        local tool_name  = msg.tool or ""
        local request_id = msg.request_id or ""
        local args       = msg.args or {}

        LOG("[LLMAIBrain] Observation: " .. tool_name .. " (id=" .. request_id .. ")")

        local handler = ObservationHandlers[tool_name]
        local result
        if handler then
            local ok, res = pcall(handler, self, args)
            if ok then
                result = res
            else
                result = {success = false, error = tostring(res)}
            end
        else
            result = {success = false, error = "unknown observation tool: " .. tool_name}
        end

        -- Send result back via pipe
        local response = {
            type       = "observation_result",
            request_id = request_id,
            tool       = tool_name,
            success    = result.success,
            data       = result.data,
            error      = result.error,
        }
        LLMBridge.SendRaw(response)
    end,

    -- ---------------------------------------------------------------------
    -- HandleActionExecute — execute an action tool and reply with result
    -- ---------------------------------------------------------------------
    HandleActionExecute = function(self, msg)
        local tool_name  = msg.tool or ""
        local request_id = msg.request_id or ""
        local args       = msg.args or {}

        LOG("[LLMAIBrain] Action execute: " .. tool_name .. " (id=" .. request_id .. ")")

        local result = self:ExecuteToolWithResult(tool_name, args)

        local response = {
            type       = "action_result",
            request_id = request_id,
            tool       = tool_name,
            success    = result.success,
            data       = result.data,
            error      = result.error,
        }
        LLMBridge.SendRaw(response)
    end,

    -- ---------------------------------------------------------------------
    -- ExecuteToolWithResult — run a tool and return structured result
    -- ---------------------------------------------------------------------
    ExecuteToolWithResult = function(self, name, args)
        if name == "attack" then
            return self:ToolAttack(args)
        elseif name == "defend" then
            return self:ToolDefend(args)
        elseif name == "scout" then
            return self:ToolScout(args)
        elseif name == "set_strategy" then
            return self:ToolSetStrategy(args)
        elseif name == "build_units" then
            return self:ToolBuildUnits(args)
        elseif name == "reclaim" then
            return self:ToolReclaim(args)
        elseif name == "chat" then
            local msg_text = args.message
            if msg_text then self:SendChat(msg_text) end
            return {success = true, data = {message = msg_text or ""}}
        elseif name == "noop" then
            return {success = true, data = {reasoning = args.reasoning or ""}}
        else
            return {success = false, error = "unknown tool: " .. tostring(name)}
        end
    end,

    -- ---------------------------------------------------------------------
    -- ApplyLLMDecision — dispatch tool calls from the LLM agent
    --
    -- The LLM returns a list of tool_calls, each with name + args.
    -- Base AI handles building/economy/platoons autonomously.
    -- Tools add strategic direction on top.
    -- ---------------------------------------------------------------------
    ApplyLLMDecision = function(self, cmd)
        if not cmd then return end

        -- Adaptive polling: Python sends recommended next interval
        if cmd.poll_interval_override then
            self._poll_override = cmd.poll_interval_override
            LOG("[LLMAIBrain] Poll interval override: " .. tostring(cmd.poll_interval_override) .. "s")
        end

        local tool_calls = cmd.tool_calls
        if not tool_calls or table.getn(tool_calls) == 0 then
            LOG("[LLMAIBrain] No tool calls in LLM decision")
            return
        end

        -- Dispatch each tool call
        for _, call in ipairs(tool_calls) do
            local name = call.name
            local args = call.args or {}

            LOG("[LLMAIBrain] Tool: " .. tostring(name) ..
                " args=" .. tostring(args and "yes" or "nil"))

            if name == "attack" then
                self:ToolAttack(args)
            elseif name == "defend" then
                self:ToolDefend(args)
            elseif name == "scout" then
                self:ToolScout(args)
            elseif name == "set_strategy" then
                self:ToolSetStrategy(args)
            elseif name == "build_units" then
                self:ToolBuildUnits(args)
            elseif name == "reclaim" then
                self:ToolReclaim(args)
            elseif name == "chat" then
                local msg = args.message
                if msg then self:SendChat(msg) end
            elseif name == "noop" then
                LOG("[LLMAIBrain] Noop: " .. tostring(args.reasoning or ""))
            else
                LOG("[LLMAIBrain] Unknown tool: " .. tostring(name))
            end
        end
    end,

    -- =====================================================================
    -- Tool implementations
    -- =====================================================================

    -- Helper: find enemy ACU position
    _FindEnemyPos = function(self)
        if not ArmyBrains then return nil end
        for _, eb in ipairs(ArmyBrains) do
            if eb and eb ~= self
               and IsEnemy(self:GetArmyIndex(), eb:GetArmyIndex())
            then
                local ok, ecl = pcall(eb.GetListOfUnits, eb,
                    categories.COMMAND, false, false)
                if ok and ecl and table.getn(ecl) > 0
                   and not ecl[1].Dead
                then
                    return ecl[1]:GetPosition()
                end
            end
        end
        return nil
    end,

    -- Helper: find our base (ACU) position
    _FindBasePos = function(self)
        local ok, acu_list = pcall(self.GetListOfUnits, self,
            categories.COMMAND, false, false)
        if ok and acu_list and table.getn(acu_list) > 0
           and not acu_list[1].Dead
        then
            return acu_list[1]:GetPosition()
        end
        return nil
    end,

    -- Helper: get idle combat units from ArmyPool by category
    _GetPoolUnits = function(self, cat_filter)
        local pool = self:GetPlatoonUniquelyNamed('ArmyPool')
        if not pool then return {} end
        local pool_units = pool:GetPlatoonUnits()
        if not pool_units then return {} end

        local result = {}
        for _, u in ipairs(pool_units) do
            if not u.Dead and EntityCategoryContains(cat_filter, u) then
                table.insert(result, u)
            end
        end
        return result
    end,

    -- -----------------------------------------------------------------
    -- Tool: attack — send idle combat units to attack the enemy
    -- -----------------------------------------------------------------
    ToolAttack = function(self, args)
        local enemy_pos = self:_FindEnemyPos()
        if not enemy_pos then
            LOG("[LLMAIBrain] attack: no enemy found")
            return {success = false, error = "no enemy found"}
        end

        local unit_type  = args.unit_type  or "land"
        local force_size = args.force_size or "medium"

        local combat_cats
        if unit_type == "air" then
            combat_cats = categories.MOBILE * categories.AIR
                          - categories.ENGINEER - categories.COMMAND
        elseif unit_type == "all" then
            combat_cats = categories.MOBILE - categories.ENGINEER - categories.COMMAND
        else
            combat_cats = categories.MOBILE * categories.LAND
                          - categories.ENGINEER - categories.COMMAND
        end

        local units = self:_GetPoolUnits(combat_cats)
        local total = table.getn(units)

        local limit = total
        if force_size == "small" then
            limit = math.min(10, total)
        elseif force_size == "medium" then
            limit = math.min(20, total)
        end

        if limit < 3 then
            LOG("[LLMAIBrain] attack: only " .. total .. " idle units, need 3+")
            return {success = false, error = "not enough units", data = {available = total}}
        end

        local attack_units = {}
        for i = 1, limit do
            table.insert(attack_units, units[i])
        end

        local platoon = self:MakePlatoon('LLMAttack', 'AttackForceAI')
        self:AssignUnitsToPlatoon(platoon, attack_units, 'Attack', 'GrowthFormation')

        LOG("[LLMAIBrain] attack: " .. limit .. "/" .. total ..
            " " .. unit_type .. " units -> enemy")
        return {success = true, data = {units_sent = limit, unit_type = unit_type}}
    end,

    -- -----------------------------------------------------------------
    -- Tool: defend — rally idle units to patrol base
    -- -----------------------------------------------------------------
    ToolDefend = function(self, args)
        local base_pos = self:_FindBasePos()
        if not base_pos then
            return {success = false, error = "no base found"}
        end

        local radius = args.radius or 80
        local combat_cats = categories.MOBILE * categories.LAND
                            - categories.ENGINEER - categories.COMMAND
        local units = self:_GetPoolUnits(combat_cats)
        local count = table.getn(units)
        if count < 2 then
            return {success = false, error = "not enough units", data = {available = count}}
        end

        local platoon = self:MakePlatoon('LLMDefend', 'PatrolBaseVectorsAI')
        self:AssignUnitsToPlatoon(platoon, units, 'Attack', 'GrowthFormation')
        platoon:SetPlatoonData({
            LocationType = 'MAIN',
            Position     = base_pos,
            Radius       = radius,
        })

        LOG("[LLMAIBrain] defend: " .. count .. " units, radius=" .. radius)
        return {success = true, data = {units_assigned = count, radius = radius}}
    end,

    -- -----------------------------------------------------------------
    -- Tool: scout — send a fast unit to explore
    -- -----------------------------------------------------------------
    ToolScout = function(self, args)
        local direction = args.direction or "enemy_base"
        local base_pos  = self:_FindBasePos()
        if not base_pos then return end

        -- Pick target position based on direction
        local target
        if direction == "enemy_base" then
            target = self:_FindEnemyPos()
        end

        if not target then
            -- Use map-relative offsets
            local map_size = ScenarioInfo.size[1] or 512
            local offsets = {
                north = {base_pos[1], 0, base_pos[3] - map_size * 0.4},
                south = {base_pos[1], 0, base_pos[3] + map_size * 0.4},
                east  = {base_pos[1] + map_size * 0.4, 0, base_pos[3]},
                west  = {base_pos[1] - map_size * 0.4, 0, base_pos[3]},
            }
            target = offsets[direction] or offsets.north
        end

        -- Find a fast land or air scout in pool
        local scout_cats = categories.MOBILE * (categories.SCOUT + categories.AIR)
                           - categories.ENGINEER - categories.COMMAND
        local scouts = self:_GetPoolUnits(scout_cats)
        if table.getn(scouts) == 0 then
            -- Fallback: any fast land unit
            scout_cats = categories.MOBILE * categories.LAND
                         - categories.ENGINEER - categories.COMMAND
            scouts = self:_GetPoolUnits(scout_cats)
        end
        if table.getn(scouts) == 0 then
            return {success = false, error = "no scout units available"}
        end

        local unit = scouts[1]
        local platoon = self:MakePlatoon('LLMScout', 'HuntAI')
        self:AssignUnitsToPlatoon(platoon, {unit}, 'Scout', 'None')

        LOG("[LLMAIBrain] scout: " .. direction)
        return {success = true, data = {direction = direction}}
    end,

    -- -----------------------------------------------------------------
    -- Tool: set_strategy — store strategic posture
    -- -----------------------------------------------------------------
    ToolSetStrategy = function(self, args)
        local strategy = args.strategy or "balanced"
        local previous = self.CurrentStrategy or "balanced"
        self.LLMStrategy     = strategy
        self.CurrentStrategy = strategy
        LOG("[LLMAIBrain] strategy: " .. strategy ..
            " (" .. tostring(args.reasoning or "") .. ")")
        return {success = true, data = {strategy = strategy, previous = previous}}
    end,

    -- -----------------------------------------------------------------
    -- Tool: build_units — adjust factory build priorities
    -- (stores preference — base AI picks up changes via builders)
    -- -----------------------------------------------------------------
    ToolBuildUnits = function(self, args)
        local priority = args.priority or "land"
        local urgency  = args.urgency  or "normal"
        self.LLMBuildPriority = priority
        self.LLMBuildUrgency  = urgency

        -- Map the LLM's priority to a verified BuildOrderUEF factory queue and
        -- queue those units into every live factory NOW (one-shot per call).
        -- Previously this only stored a preference no code read (a no-op), so the
        -- LLM's single most-used action did nothing. anti_air -> air (interceptors
        -- provide AA); engineers -> dedicated engineer queue.
        local key_map = {
            land = "land_rush", air = "air", anti_air = "air",
            navy = "naval", engineers = "engineers",
        }
        local queue_key = key_map[priority] or "balanced"
        local reps = (urgency == "high") and 2 or 1

        local queue = BuildOrderUEF.GetFactoryQueue(queue_key)
        local issued = 0
        if queue and table.getn(queue) > 0 then
            local facs = self:GetListOfUnits(categories.FACTORY, false, false)
            if facs then
                for _, fac in ipairs(facs) do
                    if fac and not fac.Dead then
                        for _, bp in ipairs(queue) do
                            if pcall(IssueBuildFactory, {fac}, bp, reps) then
                                issued = issued + 1
                            end
                        end
                    end
                end
            end
        end

        LOG("[LLMAIBrain] build_units: " .. priority .. " urgency=" .. urgency
            .. " queue=" .. queue_key .. " issued=" .. tostring(issued))
        return {success = true, data = {priority = priority, urgency = urgency, issued = issued}}
    end,

    -- -----------------------------------------------------------------
    -- Tool: reclaim — send idle engineers to reclaim nearby wrecks
    -- -----------------------------------------------------------------
    ToolReclaim = function(self, args)
        local area = args.area or "near_base"
        local base_pos = self:_FindBasePos()
        if not base_pos then
            return {success = false, error = "no base found"}
        end

        local eng_cats = categories.ENGINEER - categories.COMMAND
        local engineers = self:_GetPoolUnits(eng_cats)
        if table.getn(engineers) == 0 then
            return {success = false, error = "no idle engineers"}
        end

        local count = math.min(3, table.getn(engineers))
        local reclaim_radius = 80
        if area == "battlefield" then
            reclaim_radius = 200
        elseif area == "expansion" then
            reclaim_radius = 300
        end

        for i = 1, count do
            local eng = engineers[i]
            if not eng.Dead then
                local ok_cmd = pcall(IssueReclaim, {eng}, base_pos, reclaim_radius)
                if not ok_cmd then
                    pcall(IssueMove, {eng}, base_pos)
                end
            end
        end

        LOG("[LLMAIBrain] reclaim: " .. count .. " engineers, area=" .. area)
        return {success = true, data = {engineers_sent = count, area = area}}
    end,

    -- ---------------------------------------------------------------------
    -- EventTriggerThread — detects critical events, sends immediate
    -- snapshot to LLM without waiting for the periodic timer.
    -- ---------------------------------------------------------------------
    EventTriggerThread = function(self)
        WaitSeconds(15)  -- wait for base AI baseline

        local ARMY_LOSS_THRESHOLD    = 0.5
        local ENEMY_DETECT_DISTANCE  = 300
        local ECONOMY_STALL_INCOME   = 3.0
        local ECONOMY_STALL_DURATION = 60
        local COOLDOWN_TICKS         = 200

        local last_phase = "early"
        local last_trigger_tick = {
            army_lost      = 0,
            enemy_detected = 0,
            economy_stall  = 0,
            phase_change   = 0,
        }

        while not self.Dead do
            WaitTicks(5)
            local tick = GetGameTick()

            -- 1. Army loss detection
            local ok1, land_units = pcall(self.GetListOfUnits, self,
                categories.LAND * categories.MOBILE - categories.ENGINEER - categories.COMMAND,
                false, false)
            local army_size = (ok1 and land_units and table.getn(land_units)) or 0

            if self.LastArmySize > 5
               and army_size < self.LastArmySize * (1 - ARMY_LOSS_THRESHOLD)
               and (tick - last_trigger_tick.army_lost) > COOLDOWN_TICKS
            then
                LOG("[LLMAIBrain] EVENT: army_lost")
                if LLMBridge.IsConnected() then
                    LLMBridge.SendSnapshot(self, "army_lost", nil, self.PendingChat)
                    self.PendingChat = {}
                elseif FileIPC.IsAvailable() then
                    FileIPC.WriteSnapshot(self, "army_lost", nil, self.PendingChat)
                    self.PendingChat = {}
                end
                last_trigger_tick.army_lost = tick
            end

            if (tick - self.LastArmyCheckTick) > 100 then
                self.LastArmySize      = army_size
                self.LastArmyCheckTick = tick
            end

            -- 2. Enemy near-base detection
            if (tick - last_trigger_tick.enemy_detected) > COOLDOWN_TICKS then
                local ok2, acu_list = pcall(self.GetListOfUnits, self,
                    categories.COMMAND, false, false)
                if ok2 and acu_list and table.getn(acu_list) > 0 then
                    local base_pos = acu_list[1]:GetPosition()
                    if ArmyBrains then
                        for _, eb in ipairs(ArmyBrains) do
                            if eb and eb ~= self
                               and IsEnemy(self:GetArmyIndex(), eb:GetArmyIndex())
                            then
                                local ok3, ecl = pcall(eb.GetListOfUnits, eb,
                                    categories.COMMAND, false, false)
                                if ok3 and ecl and table.getn(ecl) > 0
                                   and not ecl[1].Dead
                                then
                                    local dist = VDist3(base_pos, ecl[1]:GetPosition())
                                    if dist < ENEMY_DETECT_DISTANCE then
                                        LOG("[LLMAIBrain] EVENT: enemy_detected dist=" ..
                                            math.floor(dist))
                                        if LLMBridge.IsConnected() then
                                            LLMBridge.SendSnapshot(self, "enemy_detected",
                                                nil, self.PendingChat)
                                            self.PendingChat = {}
                                        elseif FileIPC.IsAvailable() then
                                            FileIPC.WriteSnapshot(self, "enemy_detected",
                                                nil, self.PendingChat)
                                            self.PendingChat = {}
                                        end
                                        last_trigger_tick.enemy_detected = tick
                                        break
                                    end
                                end
                            end
                        end
                    end
                end
            end

            -- 3. Economy stall detection
            if (tick - last_trigger_tick.economy_stall) > COOLDOWN_TICKS then
                local mass_income = self:GetEconomyIncome('MASS') or 0
                if mass_income < ECONOMY_STALL_INCOME then
                    if not self.EconomyStallTick then
                        self.EconomyStallTick = tick
                    elseif (tick - self.EconomyStallTick) > (ECONOMY_STALL_DURATION * 10) then
                        LOG("[LLMAIBrain] EVENT: economy_stall")
                        if LLMBridge.IsConnected() then
                            LLMBridge.SendSnapshot(self, "economy_stall",
                                nil, self.PendingChat)
                            self.PendingChat = {}
                        elseif FileIPC.IsAvailable() then
                            FileIPC.WriteSnapshot(self, "economy_stall",
                                nil, self.PendingChat)
                            self.PendingChat = {}
                        end
                        last_trigger_tick.economy_stall = tick
                        self.EconomyStallTick = nil
                    end
                else
                    self.EconomyStallTick = nil
                end
            end

            -- 4. Phase change detection
            if (tick - last_trigger_tick.phase_change) > COOLDOWN_TICKS then
                local ok4, t2f = pcall(self.GetListOfUnits, self,
                    categories.TECH2 * categories.FACTORY, false, false)
                local ok5, t3u = pcall(self.GetListOfUnits, self,
                    categories.TECH3 * categories.MOBILE, false, false)

                local current_phase
                if ok5 and t3u and table.getn(t3u) > 0 then
                    current_phase = "late"
                elseif ok4 and t2f and table.getn(t2f) > 0 then
                    current_phase = "mid"
                else
                    current_phase = "early"
                end

                if current_phase ~= last_phase then
                    LOG("[LLMAIBrain] EVENT: phase_change " ..
                        last_phase .. " -> " .. current_phase)
                    if LLMBridge.IsConnected() then
                        LLMBridge.SendSnapshot(self, "phase_change",
                            nil, self.PendingChat)
                        self.PendingChat = {}
                    elseif FileIPC.IsAvailable() then
                        FileIPC.WriteSnapshot(self, "phase_change",
                            nil, self.PendingChat)
                        self.PendingChat = {}
                    end
                    last_trigger_tick.phase_change = tick
                    last_phase = current_phase
                end
            end
        end
    end,

    -- ---------------------------------------------------------------------
    -- ChatHandlerThread — reads player messages from SimCallback global,
    -- generates responses (LLM or offline canned replies).
    --
    -- Flow: UI chat.lua hook → SimCallback('LLMPlayerChat') →
    --       _G.LLMPendingPlayerChat → this thread reads it.
    -- ---------------------------------------------------------------------
    ChatHandlerThread = function(self)
        WaitSeconds(5)
        LOG("[LLMAIBrain] ChatHandlerThread started")

        local CHAT_COOLDOWN = 5  -- seconds between responses
        local last_response_time = 0
        local my_nickname = self.Nickname or ""

        while not self.Dead do
            -- Read messages deposited by the SimCallback hook
            local pending = rawget(_G, 'LLMPendingPlayerChat')
            if pending and table.getn(pending) > 0 then
                -- Consume all pending messages
                rawset(_G, 'LLMPendingPlayerChat', {})

                local game_time = GetGameTimeSeconds()

                for _, data in ipairs(pending) do
                    local text   = data.text or ""
                    local sender = data.sender or "?"

                    -- Skip our own messages (double safety against feedback loop)
                    if sender == my_nickname then
                        -- ignore
                    elseif text ~= "" and (game_time - last_response_time) > CHAT_COOLDOWN then
                        LOG("[LLMAIBrain] Chat from " .. sender .. ": " .. text)

                        -- Add to LLM context for next snapshot
                        table.insert(self.PendingChat, text)

                        -- Generate response
                        if LLMBridge.IsConnected() or FileIPC.IsAvailable() then
                            -- LLM will respond via next snapshot cycle (pipe or file)
                        else
                            local reply = OfflineChat(text)
                            if reply then
                                self:SendChat(reply)
                                last_response_time = game_time
                            end
                        end
                    end
                end
            end

            WaitSeconds(2)
        end
    end,

    -- ---------------------------------------------------------------------
    -- ReflexThread — ally mode instant reactions (no LLM)
    -- ---------------------------------------------------------------------
    ReflexThread = function(self)
        local AllyMonitor = import("/mods/supcom-llm-ai-bot/lua/AI/AllyMonitor.lua")
        local ReflexLayer = import("/mods/supcom-llm-ai-bot/lua/AI/ReflexLayer.lua")
        WaitSeconds(5)

        while not self.Dead do
            local ok_r, allyState = pcall(AllyMonitor.GetAllyState, self)
            if ok_r and allyState then
                pcall(ReflexLayer.EvaluateAndAct, self, allyState)
            end
            WaitTicks(5)
        end
    end,

    -- ---------------------------------------------------------------------
    -- SendChat — uses FAF's official SyncAIChat to display in game chat
    -- SIM → Sync.AIChat → UI picks up and shows in chat window
    -- ---------------------------------------------------------------------
    SendChat = function(self, text)
        if not text or text == "" then return end
        LOG("[LLMAIBrain] CHAT: " .. text)

        local SyncAIChat = import('/lua/simsyncutils.lua').SyncAIChat
        SyncAIChat({
            group  = 'allies',
            text   = text,
            sender = self.Nickname or "LLM Bot",
        })
    end,

    -- ---------------------------------------------------------------------
    -- Save / Load
    -- ---------------------------------------------------------------------
    OnSave = function(self, saveTable)
        BaseBrain.OnSave(self, saveTable)
        if LLMBridge.IsConnected() then
            local save_name = saveTable and saveTable.SaveName or "default"
            LLMBridge.SendRaw('{"type":"save","data":{"save_name":"' ..
                save_name .. '"}}')
        end
    end,

    OnPostLoad = function(self)
        BaseBrain.OnPostLoad(self)
        if LLMBridge.IsConnected() then
            LLMBridge.SendRaw('{"type":"load","data":{"save_name":"default"}}')
        end
    end,
}
