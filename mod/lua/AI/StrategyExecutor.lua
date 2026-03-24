-- T023: Strategy Executor — mod/lua/AI/StrategyExecutor.lua
--
-- Apply(brain, decision) translates a StrategicDecision table into game commands:
--   1. Set the active build template in BuildOrderUEF
--   2. Issue factory queue commands
--   3. Assign platoon attack direction
--   4. Set retreat threshold on brain state
--
-- TickBuild(brain) — called every 20s from StrategyUpdateThread.
--   Checks if the ACU is idle, and if so issues the next build from the template.
--   Tracks position in the template via brain._build_index.

local BuildOrderUEF = import("/mods/supcom-llm-ai-bot/lua/AI/BuildOrderUEF.lua")

-- ---------------------------------------------------------------------------
-- Map direction strings to actual positions
-- ---------------------------------------------------------------------------

local function DirectionToMapPosition(brain, direction)
    if not direction or direction == "none" or direction == "null" then
        return nil
    end

    local map_w, map_h = GetMapSize()
    local mid_x = map_w / 2
    local mid_z = map_h / 2

    local dir_positions = {
        north         = {mid_x, 0, 10},
        south         = {mid_x, 0, map_h - 10},
        east          = {map_w - 10, 0, mid_z},
        west          = {10, 0, mid_z},
        nearest_enemy = nil,
    }

    if direction == "nearest_enemy" then
        local acu_list = brain:GetListOfUnits(categories.COMMAND, false, false)
        local base_pos = (acu_list and table.getn(acu_list) > 0) and acu_list[1]:GetPosition() or {mid_x, 0, mid_z}
        local best_pos = nil
        local best_dist = 9999
        if ArmyBrains then
            for _, eb in ipairs(ArmyBrains) do
                if eb and eb ~= brain and IsEnemy(brain:GetArmyIndex(), eb:GetArmyIndex()) then
                    local enemy_acu = eb:GetListOfUnits(categories.COMMAND, false, false)
                    if enemy_acu and table.getn(enemy_acu) > 0 then
                        local ep   = enemy_acu[1]:GetPosition()
                        local dist = VDist3(base_pos, ep)
                        if dist < best_dist then
                            best_dist = dist
                            best_pos  = ep
                        end
                    end
                end
            end
        end
        return best_pos
    end

    return dir_positions[direction]
end

-- ---------------------------------------------------------------------------
-- Factory queue management
-- ---------------------------------------------------------------------------

local function SetFactoryQueues(brain, strategy_name)
    local queue_units = BuildOrderUEF.GetFactoryQueue(strategy_name)
    if not queue_units or table.getn(queue_units) == 0 then return end

    local factories = brain:GetListOfUnits(categories.FACTORY, false, false)
    if not factories then return end

    for _, factory in ipairs(factories) do
        if factory and not factory.Dead then
            factory:SetHoldBuild(false)
            for _, bp_id in ipairs(queue_units) do
                IssueBuildFactory({factory}, bp_id, 1)
            end
        end
    end
end

-- ---------------------------------------------------------------------------
-- ACU build order — called by TickBuild every 20s
-- Issues the next structure from the template when ACU is idle.
-- brain._build_index tracks the current position in the template.
-- ---------------------------------------------------------------------------

local function AdvanceACUBuild(brain, strategy_name)
    local template = BuildOrderUEF.GetTemplate(strategy_name)
    if not template then return end
    local t_len = table.getn(template)
    if t_len == 0 then return end

    -- Initialise index on first call or after strategy change
    if not brain._build_index then brain._build_index = 1 end

    -- After primary template is done, switch to expansion loop
    local using_expansion = false
    local bp_id
    if brain._build_index > t_len then
        local expansion = BuildOrderUEF.GetExpansionLoop()
        if not expansion or table.getn(expansion) == 0 then return end
        -- Loop within expansion template
        if not brain._expansion_index then brain._expansion_index = 1 end
        local exp_len = table.getn(expansion)
        bp_id = expansion[brain._expansion_index]
        using_expansion = true
    else
        bp_id = template[brain._build_index]
    end

    if not bp_id then return end

    -- Find ACU
    local acu_list = brain:GetListOfUnits(categories.COMMAND, false, false)
    if not acu_list or table.getn(acu_list) == 0 then return end
    local acu = acu_list[1]
    if not acu or acu.Dead then return end

    -- Only act when ACU is idle
    local queue = acu:GetCommandQueue()
    if queue and table.getn(queue) > 0 then return end

    local acu_pos = acu:GetPosition()

    if using_expansion then
        local exp_len = table.getn(BuildOrderUEF.GetExpansionLoop())
        LOG(string.format("[StrategyExecutor] Expansion %d/%d: %s",
            brain._expansion_index, exp_len, tostring(bp_id)))
    else
        LOG(string.format("[StrategyExecutor] Build %d/%d: %s",
            brain._build_index, t_len, tostring(bp_id)))
    end

    local ok, err = pcall(IssueBuildMobile, {acu}, acu_pos, bp_id, {})
    if ok then
        if using_expansion then
            brain._expansion_index = brain._expansion_index + 1
            local exp_len = table.getn(BuildOrderUEF.GetExpansionLoop())
            if brain._expansion_index > exp_len then
                brain._expansion_index = 1  -- loop
            end
        else
            brain._build_index = brain._build_index + 1
        end
    else
        -- Skip this blueprint if it fails (e.g. no valid placement)
        LOG("[StrategyExecutor] IssueBuildMobile FAILED: " .. tostring(err) .. " — skipping")
        if using_expansion then
            brain._expansion_index = (brain._expansion_index or 1) + 1
            local exp_len = table.getn(BuildOrderUEF.GetExpansionLoop())
            if brain._expansion_index > exp_len then
                brain._expansion_index = 1
            end
        else
            brain._build_index = brain._build_index + 1
        end
    end
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Called every fast_interval from StrategyUpdateThread.
---Advances the ACU's build order if it is idle.
---@param brain LLMAIBrain
function TickBuild(brain)
    AdvanceACUBuild(brain, brain.CurrentStrategy or "balanced")
    -- Also refresh factory queues in case new factories were built
    SetFactoryQueues(brain, brain.CurrentStrategy or "balanced")
end

---Apply a StrategicDecision to the game brain.
---@param brain LLMAIBrain
---@param decision table  StrategicDecision per data-model.md
function Apply(brain, decision)
    if not decision or type(decision) ~= "table" then
        LOG("[StrategyExecutor] Invalid decision (nil or non-table)")
        return
    end

    local strategy   = decision.strategy or "balanced"
    local attack_dir = decision.attack_direction
    local retreat_rt = decision.retreat_threshold or 0.3

    LOG(string.format("[StrategyExecutor] Applying: strategy=%s dir=%s retreat=%.2f",
        strategy, tostring(attack_dir), retreat_rt))

    -- Reset build index when strategy changes so the new template starts fresh
    if brain.CurrentStrategy ~= strategy then
        brain._build_index = 1
        LOG("[StrategyExecutor] Strategy changed → reset build index")
    end

    -- 1. Update brain state
    brain.CurrentStrategy  = strategy
    brain.RetreatThreshold = retreat_rt

    -- 2. Set attack direction on brain (used by TacticalMicro)
    brain.AttackDirection = attack_dir

    -- 3. Set factory queues for the new strategy
    SetFactoryQueues(brain, strategy)

    -- 4. Issue first build if ACU is idle (immediate response)
    AdvanceACUBuild(brain, strategy)

    -- 5. Army composition: heavy land → already handled by factory queue
    local comp = decision.army_composition
    if comp and type(comp) == "table" and (comp.land or 0) > 0.6 then
        SetFactoryQueues(brain, strategy)
    end
end
