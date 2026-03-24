-- T025/T026: LLM AI Brain (full wiring) — mod/lua/AI/LLMAIBrain.lua
--
-- Inherits from FAF AIBrain base class.
-- Three-layer response architecture:
--   Layer 1 — Reflex (pure Lua, AllyMonitor + ReflexLayer, <1s, no LLM)
--   Layer 2 — Fast LLM 4B (~3-5s, via Python router, routine/ally events)
--   Layer 3 — Deep LLM 8B (~7-10s, complex strategic decisions)
--
-- Threads:
--   StrategyUpdateThread — fast (20s) + deep (60s) LLM polling loops  [T025]
--   EventTriggerThread   — army/economy/enemy event triggers           [T026]
--   TacticalMicroThread  — unit micro (every 5 ticks)                  [T024]
--   ChatHandlerThread    — chat send/receive                           [T027/T029]
--   ReflexThread         — ally mode: instant reflex reactions         [T034]

local AIBrain         = import("/lua/aibrain.lua").AIBrain
local LLMBridge       = import("/mods/supcom-llm-ai-bot/lua/AI/LLMBridge.lua")
local StrategyExecutor= import("/mods/supcom-llm-ai-bot/lua/AI/StrategyExecutor.lua")
local TacticalMicro   = import("/mods/supcom-llm-ai-bot/lua/AI/TacticalMicro.lua")
local EconomyManager  = import("/mods/supcom-llm-ai-bot/lua/AI/EconomyManager.lua")

-- ---------------------------------------------------------------------------
-- Offline chat responder — used when LLM bridge is not connected.
-- Returns a canned reply string, or nil for messages we don't recognise.
-- ---------------------------------------------------------------------------
local function OfflineChat(msg)
    if not msg then return nil end
    local lower = string.lower(msg)

    if string.find(lower, "стоишь") or string.find(lower, "стой") or
       string.find(lower, "standing") or string.find(lower, "idle") or
       string.find(lower, "nothing") or string.find(lower, "ничего") then
        return "Строю базу. LLM-сервер не подключён — работаю в автономном режиме."
    end

    if string.find(lower, "привет") or string.find(lower, "hello") or
       string.find(lower, "hi") or string.find(lower, "хай") then
        return "Привет! Работаю в автономном режиме (LLM не подключён)."
    end

    if string.find(lower, "помог") or string.find(lower, "помощ") or
       string.find(lower, "help") then
        return "Помогаю. Строю и защищаю базу."
    end

    if string.find(lower, "атак") or string.find(lower, "attack") or
       string.find(lower, "rush") then
        return "Переключаюсь на атаку."
    end

    if string.find(lower, "защит") or string.find(lower, "defend") or
       string.find(lower, "оборон") then
        return "Переключаюсь на оборону."
    end

    -- Acknowledge any unrecognised message so the player knows the bot is alive
    return "Понял. Продолжаю [автономный режим]."
end

---@class LLMAIBrain : AIBrain
LLMAIBrain = Class(AIBrain) {

    -- -----------------------------------------------------------------------
    -- OnCreateAI
    -- -----------------------------------------------------------------------
    OnCreateAI = function(self, planName)
        AIBrain.OnCreateAI(self, planName)

        -- Config — overridden by Python handshake message after connect
        self.LLMConfig = {
            mode               = "opponent",
            difficulty         = "normal",
            playstyle          = "balanced",
            poll_interval_fast = 20,
            poll_interval_deep = 60,
        }

        -- Runtime state
        self.CurrentStrategy  = "balanced"
        self.AttackDirection  = nil
        self.RetreatThreshold = 0.3
        self.LastArmySize     = 0
        self.LastArmyCheckTick = 0
        self.EnemyDetectedCooldown = 0
        self.EconomyStallTick  = nil

        -- Chat buffer (from ChatHandler, populated in T027)
        self.PendingChat = {}

        -- Fork threads
        self:ForkThread(self.StrategyUpdateThread)
        self:ForkThread(self.EventTriggerThread)
        self:ForkThread(self.TacticalMicroThread)
        self:ForkThread(self.ChatHandlerThread)

        if self.LLMConfig.mode == "ally" then
            self:ForkThread(self.ReflexThread)
        end
    end,

    -- -----------------------------------------------------------------------
    -- T025: StrategyUpdateThread — fast (20s) + deep (60s) polling
    -- -----------------------------------------------------------------------
    StrategyUpdateThread = function(self)
        WaitSeconds(5)  -- let game initialise

        -- Apply an immediate fallback decision so the bot acts from the start,
        -- even if the bridge server / LLM is not yet connected.
        StrategyExecutor.Apply(self, {
            strategy           = self.LLMConfig.playstyle or "balanced",
            attack_direction   = "none",
            retreat_threshold  = 0.3,
            army_composition   = {land = 0.7, air = 0.2, navy = 0.1},
            chat_message       = nil,
        })
        LOG("[LLMAIBrain] Applied initial fallback strategy: " ..
            (self.LLMConfig.playstyle or "balanced"))

        local fast_interval  = self.LLMConfig.poll_interval_fast or 20
        local deep_interval  = self.LLMConfig.poll_interval_deep or 60
        local ticks_since_deep = 0

        while not self.Dead do
            -- Re-check for DLL registration in case injection happened after
            -- LLMBridge.lua was first imported (late-injection path).
            LLMBridge.RetryConnect()

            -- Send fast snapshot
            if LLMBridge.IsConnected() then
                local trigger = (ticks_since_deep * fast_interval >= deep_interval)
                                and "deep_periodic" or "periodic"
                LLMBridge.SendSnapshot(self, trigger, nil, self.PendingChat)
                self.PendingChat = {}  -- consume pending chat

                if trigger == "deep_periodic" then
                    ticks_since_deep = 0
                else
                    ticks_since_deep = ticks_since_deep + 1
                end
            end

            -- Poll for a command response (non-blocking)
            local cmd = LLMBridge.PollCommand()
            if cmd then
                StrategyExecutor.Apply(self, cmd)
                -- Send in-game chat if the LLM included a message (T029)
                if cmd.chat_message and self.SendChat then
                    self:SendChat(cmd.chat_message)
                end
            end

            -- Economy manager tick (assigns idle engineers, detects stalls)
            EconomyManager.Tick(self)

            -- Advance ACU build order if ACU became idle since last loop
            StrategyExecutor.TickBuild(self)

            WaitSeconds(fast_interval)
        end
    end,

    -- -----------------------------------------------------------------------
    -- T026: EventTriggerThread — critical event detection
    -- Runs every 5 ticks; sends immediate snapshot on key events without
    -- waiting for the 20s fast timer.
    -- -----------------------------------------------------------------------
    EventTriggerThread = function(self)
        WaitSeconds(10)  -- wait for strategy baseline

        local ARMY_LOSS_THRESHOLD     = 0.5   -- 50% army lost → trigger
        local ENEMY_DETECT_DISTANCE   = 300    -- units; enemy ACU within this → trigger
        local ECONOMY_STALL_INCOME    = 3.0   -- mass/sec threshold
        local ECONOMY_STALL_DURATION  = 60    -- seconds

        -- Phase tracking for phase_change trigger
        local last_phase = "early"

        local COOLDOWN_TICKS = 200  -- ~20s minimum between event triggers

        local last_trigger_tick = {
            army_lost       = 0,
            enemy_detected  = 0,
            economy_stall   = 0,
            phase_change    = 0,
        }

        while not self.Dead do
            WaitTicks(5)
            local tick = GetGameTick()

            -- 1. Army loss detection
            local land_units = self:GetListOfUnits(
                categories.LAND * categories.MOBILE - categories.ENGINEER - categories.COMMAND, false, false)
            local army_size = (land_units and table.getn(land_units)) or 0

            if self.LastArmySize > 5
               and army_size < self.LastArmySize * (1 - ARMY_LOSS_THRESHOLD)
               and (tick - last_trigger_tick.army_lost) > COOLDOWN_TICKS
            then
                LOG("[LLMAIBrain] EVENT: army_lost")
                LLMBridge.SendSnapshot(self, "army_lost", nil, self.PendingChat)
                self.PendingChat = {}
                last_trigger_tick.army_lost = tick
            end

            -- Update army size every ~100 ticks
            if (tick - self.LastArmyCheckTick) > 100 then
                self.LastArmySize      = army_size
                self.LastArmyCheckTick = tick
            end

            -- 2. Enemy near-base detection
            if (tick - last_trigger_tick.enemy_detected) > COOLDOWN_TICKS then
                local acu_list = self:GetListOfUnits(categories.COMMAND, false, false)
                if acu_list and table.getn(acu_list) > 0 then
                    local base_pos = acu_list[1]:GetPosition()
                    if ArmyBrains then
                        for _, eb in ipairs(ArmyBrains) do
                            if eb and eb ~= self
                               and IsEnemy(self:GetArmyIndex(), eb:GetArmyIndex())
                            then
                                local enemy_acu = eb:GetListOfUnits(categories.COMMAND, false, false)
                                if enemy_acu and table.getn(enemy_acu) > 0 then
                                    local dist = VDist3(base_pos, enemy_acu[1]:GetPosition())
                                    if dist < ENEMY_DETECT_DISTANCE then
                                        LOG("[LLMAIBrain] EVENT: enemy_detected dist=" .. math.floor(dist))
                                        LLMBridge.SendSnapshot(self, "enemy_detected", nil, self.PendingChat)
                                        self.PendingChat = {}
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
                        LLMBridge.SendSnapshot(self, "economy_stall", nil, self.PendingChat)
                        self.PendingChat = {}
                        last_trigger_tick.economy_stall = tick
                        self.EconomyStallTick = nil
                    end
                else
                    self.EconomyStallTick = nil
                end
            end

            -- 4. Phase change detection
            if (tick - last_trigger_tick.phase_change) > COOLDOWN_TICKS then
                local t2_fac = self:GetListOfUnits(
                    categories.TECH2 * categories.FACTORY, false, false)
                local t3_units = self:GetListOfUnits(
                    categories.TECH3 * categories.MOBILE, false, false)

                local current_phase
                if t3_units and table.getn(t3_units) > 0 then
                    current_phase = "late"
                elseif t2_fac and table.getn(t2_fac) > 0 then
                    current_phase = "mid"
                else
                    current_phase = "early"
                end

                if current_phase ~= last_phase then
                    LOG("[LLMAIBrain] EVENT: phase_change " .. last_phase .. " -> " .. current_phase)
                    LLMBridge.SendSnapshot(self, "phase_change", nil, self.PendingChat)
                    self.PendingChat = {}
                    last_trigger_tick.phase_change = tick
                    last_phase = current_phase
                end
            end
        end
    end,

    -- -----------------------------------------------------------------------
    -- TacticalMicroThread — delegates to TacticalMicro module
    -- -----------------------------------------------------------------------
    TacticalMicroThread = function(self)
        TacticalMicro.TacticalMicroThread(self)
    end,

    -- -----------------------------------------------------------------------
    -- ChatHandlerThread — captures player chat and generates responses
    -- -----------------------------------------------------------------------
    ChatHandlerThread = function(self)
        local ChatHandler = import("/mods/supcom-llm-ai-bot/lua/AI/ChatHandler.lua")
        WaitSeconds(3)

        -- Install the SimCallbacks hook now that sim layer is initialised
        ChatHandler.EnsureHooked()

        while not self.Dead do
            -- Retry hook if it failed on first attempt (SimCallbacks may appear later)
            ChatHandler.EnsureHooked()

            -- Drain player messages captured by the SimCallbacks hook
            local msgs = ChatHandler.GetPendingMessages()
            if msgs and table.getn(msgs) > 0 then
                for _, msg in ipairs(msgs) do
                    -- Add to PendingChat so the next snapshot carries the context
                    table.insert(self.PendingChat, msg)

                    -- Generate an offline response when LLM is not connected
                    if not LLMBridge.IsConnected() then
                        local reply = OfflineChat(msg)
                        if reply then
                            self:SendChat(reply)
                        end
                    end
                end
            end

            WaitSeconds(1)
        end
    end,

    -- -----------------------------------------------------------------------
    -- ReflexThread — ally mode instant reaction (wired in T034)
    -- -----------------------------------------------------------------------
    ReflexThread = function(self)
        local AllyMonitor = import("/mods/supcom-llm-ai-bot/lua/AI/AllyMonitor.lua")
        local ReflexLayer = import("/mods/supcom-llm-ai-bot/lua/AI/ReflexLayer.lua")
        WaitSeconds(5)

        while not self.Dead do
            local allyState = AllyMonitor.GetAllyState(self)
            if allyState then
                ReflexLayer.EvaluateAndAct(self, allyState)
            end
            WaitTicks(5)
        end
    end,

    -- -----------------------------------------------------------------------
    -- SendChat — pushes a message from sim to UI via the Sync table.
    -- The UI hook (gamemain hook) picks up Sync.LLMBotChat and displays it
    -- in the game chat box.
    -- -----------------------------------------------------------------------
    SendChat = function(self, text)
        if not text or text == "" then return end
        LOG("[LLMAIBrain] CHAT: " .. text)

        -- Monotone ID lets the UI detect each new message even if the text
        -- repeats; the counter lives on the brain instance.
        self._chat_id = (self._chat_id or 0) + 1

        local sync = rawget(_G, "Sync")
        if sync then
            rawset(sync, "LLMBotChat", {
                text = text,
                from = "AI Bot",
                id   = self._chat_id,
            })
        end
    end,

    -- -----------------------------------------------------------------------
    -- OnSave / OnPostLoad (T051)
    -- -----------------------------------------------------------------------
    OnSave = function(self, saveTable)
        AIBrain.OnSave(self, saveTable)
        -- Notify Python to persist context
        if LLMBridge.IsConnected() then
            local save_name = saveTable and saveTable.SaveName or "default"
            LLMBridge.SendRaw('{"type":"save","data":{"save_name":"' .. save_name .. '"}}')
        end
    end,

    OnPostLoad = function(self)
        AIBrain.OnPostLoad(self)
        if LLMBridge.IsConnected() then
            LLMBridge.SendRaw('{"type":"load","data":{"save_name":"default"}}')
        end
    end,
}
