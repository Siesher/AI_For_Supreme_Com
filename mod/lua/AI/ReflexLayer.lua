-- T033: Reflex Layer — mod/lua/AI/ReflexLayer.lua
--
-- EvaluateAndAct(brain, allyState) — called every 5 ticks in ally mode.
-- Evaluates four triggers from AllyState (data-model.md entity 9):
--   1. air_threat_near_base > 15  -> SCRAMBLE_INTERCEPTORS
--   2. base_threat > 30           -> SEND_GROUND_SUPPORT
--   3. losing_army_fast           -> SEND_ARMY_SUPPORT
--   4. ally mass stall + own surplus > 300 -> SHARE_MASS
--
-- Each trigger has a 20-tick cooldown per trigger type.
-- After acting, sends a snapshot to Python for LLM chat commentary.
--
-- T047: Difficulty affects cooldown (easy=40 ticks, normal=20, hard=5).

local LLMBridge = import("/mods/supcom-llm-ai-bot/lua/AI/LLMBridge.lua")

-- ---------------------------------------------------------------------------
-- Cooldown tracking (per trigger type)
-- ---------------------------------------------------------------------------

local _cooldowns = {
    SCRAMBLE_INTERCEPTORS = 0,
    SEND_GROUND_SUPPORT   = 0,
    SEND_ARMY_SUPPORT     = 0,
    SHARE_MASS            = 0,
}

local COOLDOWN_NORMAL = 20   -- ticks
local COOLDOWN_EASY   = 40
local COOLDOWN_HARD   = 5

local function GetCooldown(brain)
    local diff = brain.LLMConfig and brain.LLMConfig.difficulty or "normal"
    if diff == "easy"  then return COOLDOWN_EASY  end
    if diff == "hard"  then return COOLDOWN_HARD  end
    return COOLDOWN_NORMAL
end

local function CanFire(trigger_name, current_tick, cooldown)
    return current_tick >= (_cooldowns[trigger_name] + cooldown)
end

local function SetCooldown(trigger_name, current_tick)
    _cooldowns[trigger_name] = current_tick
end

-- ---------------------------------------------------------------------------
-- Trigger actions
-- ---------------------------------------------------------------------------

local function ScrambleInterceptors(brain, ally_base_pos)
    local interceptors = brain:GetListOfUnits(
        categories.AIR * categories.MOBILE * categories.ANTIAIR, false, false)
    if not interceptors or table.getn(interceptors) == 0 then
        LOG("[ReflexLayer] SCRAMBLE_INTERCEPTORS: no interceptors available")
        return false
    end

    local target_pos = {ally_base_pos[1], 0, ally_base_pos[2]}
    local deployed = 0
    for _, unit in ipairs(interceptors) do
        if not unit.Dead then
            IssuePatrol({unit}, target_pos)
            deployed = deployed + 1
        end
    end
    LOG(string.format("[ReflexLayer] SCRAMBLE_INTERCEPTORS: dispatched %d interceptors", deployed))
    return deployed > 0
end

local function SendGroundSupport(brain, ally_base_pos)
    -- Find idle or nearest land platoon and redirect to ally base
    local land_units = brain:GetListOfUnits(
        categories.LAND * categories.MOBILE * categories.MILITARY, false, false)
    if not land_units or table.getn(land_units) == 0 then
        LOG("[ReflexLayer] SEND_GROUND_SUPPORT: no land units available")
        return false
    end

    -- Pick units near the front (closest to ally base)
    local target_pos = {ally_base_pos[1], 0, ally_base_pos[2]}
    local nearby = {}
    for _, unit in ipairs(land_units) do
        if not unit.Dead then
            local d = VDist3(unit:GetPosition(), target_pos)
            table.insert(nearby, {unit = unit, dist = d})
        end
    end
    table.sort(nearby, function(a, b) return a.dist < b.dist end)

    -- Send the closest 10 units (or all if fewer)
    local to_send = {}
    for i = 1, math.min(10, table.getn(nearby)) do
        table.insert(to_send, nearby[i].unit)
    end

    if table.getn(to_send) == 0 then return false end
    IssueMoveToPosition(to_send, target_pos)
    LOG(string.format("[ReflexLayer] SEND_GROUND_SUPPORT: sent %d units to ally base", table.getn(to_send)))
    return true
end

local function SendArmySupport(brain, ally_base_pos)
    -- Redirect 40% of own attack army to ally
    local land_units = brain:GetListOfUnits(
        categories.LAND * categories.MOBILE * categories.MILITARY, false, false)
    if not land_units or table.getn(land_units) == 0 then return false end

    local send_count = math.max(1, math.floor(table.getn(land_units) * 0.4))
    local to_send    = {}
    for i = 1, send_count do
        if not land_units[i].Dead then
            table.insert(to_send, land_units[i])
        end
    end

    if table.getn(to_send) == 0 then return false end
    local target_pos = {ally_base_pos[1], 0, ally_base_pos[2]}
    IssueMoveToPosition(to_send, target_pos)
    LOG(string.format("[ReflexLayer] SEND_ARMY_SUPPORT: sent %d/%d units to ally",
        table.getn(to_send), table.getn(land_units)))
    return true
end

local function ShareMass(brain)
    -- Use FAF's GiveResourcesToPlayer to share mass
    -- Requires knowing the ally army index
    local ally_monitor = import("/mods/supcom-llm-ai-bot/lua/AI/AllyMonitor.lua")
    -- Shares 300 mass
    local ok, err = pcall(GiveResourcesToPlayer, brain:GetArmyIndex(),
                          ally_monitor._ally_army_index or 1, "MASS", 300)
    if ok then
        LOG("[ReflexLayer] SHARE_MASS: shared 300 mass with ally")
        return true
    end
    LOG("[ReflexLayer] SHARE_MASS: failed - " .. tostring(err))
    return false
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Evaluate ally state and fire reflexes as needed.
---@param brain LLMAIBrain
---@param ally_state table  AllyState
function EvaluateAndAct(brain, ally_state)
    if not ally_state then return end

    local tick      = GetGameTick()
    local cooldown  = GetCooldown(brain)
    local base_pos  = ally_state.base_position  -- {x, z}
    local acted     = false
    local event_name = nil

    -- Difficulty easy: skip combat reflexes (T047)
    local diff = brain.LLMConfig and brain.LLMConfig.difficulty or "normal"
    local skip_combat = (diff == "easy")

    -- 1. Air threat -> Scramble interceptors
    if not skip_combat
       and ally_state.under_air_attack
       and CanFire("SCRAMBLE_INTERCEPTORS", tick, cooldown)
    then
        if ScrambleInterceptors(brain, base_pos) then
            SetCooldown("SCRAMBLE_INTERCEPTORS", tick)
            acted      = true
            event_name = "ally_air_threat"
        end
    end

    -- 2. Ground threat -> Send ground support
    if not skip_combat
       and ally_state.under_ground_attack
       and CanFire("SEND_GROUND_SUPPORT", tick, cooldown)
    then
        if SendGroundSupport(brain, base_pos) then
            SetCooldown("SEND_GROUND_SUPPORT", tick)
            acted      = true
            event_name = event_name or "ally_under_attack"
        end
    end

    -- 3. Ally losing army fast -> Send army support
    if not skip_combat
       and ally_state.losing_army_fast
       and CanFire("SEND_ARMY_SUPPORT", tick, cooldown)
    then
        if SendArmySupport(brain, base_pos) then
            SetCooldown("SEND_ARMY_SUPPORT", tick)
            acted      = true
            event_name = event_name or "ally_under_attack"
        end
    end

    -- 4. Ally mass stall + own surplus -> Share mass
    if ally_state.mass_stall
       and CanFire("SHARE_MASS", tick, cooldown)
    then
        -- Check own mass surplus
        local eco = brain:GetEconomyStored() or {}
        local own_mass = eco.Mass or 0
        if own_mass > 300 then
            if ShareMass(brain) then
                SetCooldown("SHARE_MASS", tick)
                acted      = true
                event_name = event_name or "ally_economy_stall"
            end
        end
    end

    -- After any reflex action, send snapshot to Python for LLM chat commentary
    if acted and event_name then
        LLMBridge.SendSnapshot(brain, event_name, ally_state, {})
    end
end
