-- T015: Game State Collector — mod/lua/AI/GameStateCollector.lua
--
-- Assembles the full GameStateSnapshot JSON per data-model.md entity 1.
-- Called by LLMAIBrain.StrategyUpdateThread before each LLM query.

-- Import dkjson (bundled with FAF); fall back to our bundled encoder if needed
local json_ok, dkjson = pcall(import, "/lua/modules/dkjson.lua")
if not json_ok then
    json_ok, dkjson = pcall(import, "/mods/supcom-llm-ai-bot/lua/AI/JSON.lua")
end
if not json_ok then
    dkjson = nil
    LOG("[GameStateCollector] WARNING: no JSON encoder available")
end

-- ---------------------------------------------------------------------------
-- Phase detection
-- ---------------------------------------------------------------------------
local function DetectPhase(brain)
    local units = brain:GetListOfUnits(categories.TECH2 * categories.FACTORY, false, false)
    if units and table.getn(units) > 0 then
        local t3 = brain:GetListOfUnits(categories.TECH3 * categories.MOBILE, false, false)
        if t3 and table.getn(t3) > 0 then
            return "late"
        end
        return "mid"
    end
    return "early"
end

-- ---------------------------------------------------------------------------
-- Economy summary
-- ---------------------------------------------------------------------------
local function GetEconomy(brain)
    local mass_income   = brain:GetEconomyIncome('MASS')        or 0
    local energy_income = brain:GetEconomyIncome('ENERGY')      or 0
    local mass_stored   = brain:GetEconomyStored('MASS')        or 0
    local energy_stored = brain:GetEconomyStored('ENERGY')      or 0
    local mass_ratio    = brain:GetEconomyStoredRatio('MASS')   or 0
    local energy_ratio  = brain:GetEconomyStoredRatio('ENERGY') or 0

    -- Estimate max storage capacity from ratio
    local mass_max   = (mass_ratio   > 0) and (mass_stored   / mass_ratio)   or 1000
    local energy_max = (energy_ratio > 0) and (energy_stored / energy_ratio) or 8000

    return {
        mass_income      = math.floor(mass_income   * 10) / 10,
        mass_stored      = math.floor(mass_stored),
        mass_storage_max = math.floor(mass_max),
        energy_income    = math.floor(energy_income * 10) / 10,
        energy_stored    = math.floor(energy_stored),
        energy_storage_max = math.floor(energy_max),
    }
end

-- ---------------------------------------------------------------------------
-- Unit counts by category
-- ---------------------------------------------------------------------------
local function GetUnitCounts(brain)
    local function count(cats)
        local list = brain:GetListOfUnits(cats, false, false)
        return (list and table.getn(list)) or 0
    end

    -- MILITARY category does not exist in SupCom:FA.
    -- Use LAND/AIR/NAVAL * MOBILE minus engineers/ACUs to get combat units.
    local combat = categories.MOBILE - categories.ENGINEER - categories.COMMAND
    return {
        factories     = count(categories.FACTORY),
        engineers     = count(categories.ENGINEER - categories.COMMAND),
        land_military = count(categories.LAND  * combat),
        air_military  = count(categories.AIR   * combat),
        navy_military = count(categories.NAVAL * combat),
        t1            = count(categories.TECH1 * combat),
        t2            = count(categories.TECH2 * combat),
        t3            = count(categories.TECH3 * combat),
        experimentals = count(categories.EXPERIMENTAL),
    }
end

-- ---------------------------------------------------------------------------
-- Threat assessment
-- ---------------------------------------------------------------------------
-- Enemy units within this world-unit radius of the ACU count as "at my base".
local NEAR_BASE_RADIUS = 100

local function GetThreats(brain)
    -- Find ACU to get base position
    local acu_list = brain:GetListOfUnits(categories.COMMAND, false, false)
    local base_pos = {0, 0, 0}
    if acu_list and table.getn(acu_list) > 0 then
        base_pos = acu_list[1]:GetPosition()
    end

    -- near_base = count of ENEMY mobile COMBAT units within NEAR_BASE_RADIUS of
    -- the ACU. We count real units (not GetThreatAtPosition "Overall", which at the
    -- base is dominated by the bot's OWN units/structures and reads ~1000+ even when
    -- no enemy is near). nearest_enemy_distance tracks the nearest enemy MOBILE unit
    -- (a raider at the doorstep), not just the enemy ACU.
    local enemy_near  = 0
    local enemy_total = 0
    local nearest_dist = 9999

    local enemy_brains = ArmyBrains
    if enemy_brains then
        for _, eb in ipairs(enemy_brains) do
            if eb and eb ~= brain and IsEnemy(brain:GetArmyIndex(), eb:GetArmyIndex()) then
                local ok_u, units = pcall(eb.GetListOfUnits, eb,
                    categories.MOBILE - categories.ENGINEER - categories.COMMAND, false, false)
                if ok_u and units then
                    enemy_total = enemy_total + table.getn(units)
                    for _, u in ipairs(units) do
                        if u and not u.Dead then
                            -- u:GetPosition() is a userdata method; calling it via
                            -- pcall is robust (type(u.GetPosition)=="function" is NOT
                            -- reliable on engine userdata and silently skipped every
                            -- unit, leaving near_base=0 / dist=9999 always).
                            local ok_p, p = pcall(u.GetPosition, u)
                            if ok_p and p then
                                local d = VDist3(base_pos, p)
                                if d < nearest_dist then nearest_dist = d end
                                if d < NEAR_BASE_RADIUS then enemy_near = enemy_near + 1 end
                            end
                        end
                    end
                end
            end
        end
    end

    return {
        near_base                = enemy_near,   -- enemy units AT my base (0 when safe)
        nearest_enemy_distance   = math.floor(nearest_dist),
        enemy_army_size_estimate = enemy_total,  -- raw enemy mobile-unit count
    }
end

-- ---------------------------------------------------------------------------
-- Map control estimate (percentage of mass points controlled)
-- ---------------------------------------------------------------------------
local function GetMapControl(brain)
    -- Defensive: any error returns neutral 50%
    local ok, result = pcall(function()
        local su_ok, SU = pcall(import, "/lua/sim/scenarioutilities.lua")
        if not su_ok or not SU or not SU.GetMarkers then return 50 end

        local markers = SU.GetMarkers()
        if not markers then return 50 end

        local army_idx = brain:GetArmyIndex()
        local total, controlled = 0, 0

        -- Use pairs — markers is a hash table keyed by marker name
        for _, marker in pairs(markers) do
            if type(marker) == "table" and marker.type == 'Mass' and marker.position then
                total = total + 1
                local pos = marker.position
                local ur_ok, units = pcall(GetUnitsInRect,
                    Rect(pos[1] - 5, pos[3] - 5, pos[1] + 5, pos[3] + 5))
                if ur_ok and units then
                    for _, u in ipairs(units) do
                        if u and not u.Dead and type(u.GetArmyIndex) == "function" then
                            if u:GetArmyIndex() == army_idx then
                                controlled = controlled + 1
                                break
                            end
                        end
                    end
                end
            end
        end

        if total == 0 then return 50 end
        return math.floor((controlled / total) * 100)
    end)

    if ok and type(result) == "number" then return result end
    return 50
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

-- Sanitize: force value to a clean Lua number or default.
-- Guards against userdata, NaN, nil, non-numeric types returned by the engine.
local function N(v, default)
    if type(v) ~= "number" then return default or 0 end
    if v ~= v then return default or 0 end  -- NaN
    if v == math.huge or v == -math.huge then return default or 0 end
    return v
end

---Assemble a complete GameStateSnapshot for the given brain.
---@param brain LLMAIBrain
---@param trigger_event string  e.g. "periodic" | "army_lost" | ...
---@param ally_state table|nil  AllyState (ally mode only)
---@param chat_messages table   array of recent player chat strings
---@return string  JSON-encoded GameStateSnapshot
function Collect(brain, trigger_event, ally_state, chat_messages)
    local tick          = GetGameTick()
    local game_time_s   = math.floor(tick / 10)

    local snapshot = {
        tick           = tick,
        game_time_s    = game_time_s,
        phase          = DetectPhase(brain),
        faction        = "UEF",
        mode           = brain.LLMConfig and brain.LLMConfig.mode or "opponent",
        trigger_event  = trigger_event or "periodic",
        economy        = GetEconomy(brain),
        units          = GetUnitCounts(brain),
        threats        = GetThreats(brain),
        map_control_pct = GetMapControl(brain),
        player_chat    = chat_messages or {},
        current_strategy = brain.CurrentStrategy or "balanced",
        ally           = ally_state,
    }

    -- Encode as JSON
    if not dkjson then
        return '{"error":"no_encoder","tick":' .. tick .. '}'
    end
    local ok, result = pcall(dkjson.encode, snapshot)
    if ok then
        return result
    else
        LOG("[GameStateCollector] JSON encode failed: " .. tostring(result))
        return '{"error":"encode_failed","tick":' .. tick .. '}'
    end
end
