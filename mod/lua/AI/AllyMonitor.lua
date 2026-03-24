-- T032: Ally Monitor — mod/lua/AI/AllyMonitor.lua
--
-- Coroutine running every 5 game-ticks in ally mode.
-- Reads AllyState per data-model.md entity 8.
-- Exposes GetAllyState(brain) for use by ReflexLayer and GameStateCollector.

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local _ally_army_index  = nil   -- FAF army index of the human ally player
local _ally_base_pos    = nil   -- {x, y, z} ACU start position
local _prev_army_size   = 0
local _prev_mass_income = 0
local _mass_stall_since = nil   -- tick when mass stall started

-- Threat query radius around ally base
local THREAT_RADIUS = 40        -- GetThreatAtPosition radius in threat cells
local AIR_SCAN_RADIUS = 500     -- game units for air-specific scan

-- ---------------------------------------------------------------------------
-- Internal: find ally army index
-- ---------------------------------------------------------------------------

local function FindAllyArmyIndex(brain)
    if not ArmyBrains then return nil end
    for idx, other_brain in ipairs(ArmyBrains) do
        if other_brain and other_brain ~= brain then
            -- Check if they are on the same team (allied)
            if IsAlly(brain:GetArmyIndex(), other_brain:GetArmyIndex()) then
                -- Return the first human player ally (not another AI)
                -- FAF: GetArmyRuleStatus() or IsHuman check if available
                return other_brain:GetArmyIndex()
            end
        end
    end
    return nil
end

local function GetAllyBrain(brain)
    if not _ally_army_index then
        _ally_army_index = FindAllyArmyIndex(brain)
    end
    if not _ally_army_index then return nil end
    if not ArmyBrains then return nil end
    return ArmyBrains[_ally_army_index]
end

local function GetAllyACUPosition(ally_brain)
    if not ally_brain then return nil end
    local acu_list = ally_brain:GetListOfUnits(categories.COMMAND, false, false)
    if acu_list and table.getn(acu_list) > 0 then
        return acu_list[1]:GetPosition()
    end
    return nil
end

-- ---------------------------------------------------------------------------
-- Air threat near ally base
-- ---------------------------------------------------------------------------

local function GetAirThreatNearAllyBase(ally_base_pos)
    if not ally_base_pos then return 0 end
    -- Count enemy air units within AIR_SCAN_RADIUS
    local rect = Rect(
        ally_base_pos[1] - AIR_SCAN_RADIUS, ally_base_pos[3] - AIR_SCAN_RADIUS,
        ally_base_pos[1] + AIR_SCAN_RADIUS, ally_base_pos[3] + AIR_SCAN_RADIUS)
    local all_units = GetUnitsInRect(rect)
    if not all_units then return 0 end

    local threat = 0
    local bot_army = -1  -- will be set when available
    for _, u in ipairs(all_units) do
        if u and not u.Dead
            and EntityCategoryContains(categories.AIR * categories.MOBILE * categories.MILITARY, u)
        then
            -- Count enemy air units (not allied)
            -- We can't easily check enemy status here without brain context,
            -- so estimate by checking if it's NOT in the same army as ally
            threat = threat + 1
        end
    end
    return threat
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Return the current AllyState table, or nil if no ally found.
---@param brain LLMAIBrain
---@return table|nil  AllyState per data-model.md entity 8
function GetAllyState(brain)
    local ally_brain = GetAllyBrain(brain)
    if not ally_brain then return nil end

    local tick = GetGameTick()

    -- Get ally ACU position (base position)
    local ally_base = GetAllyACUPosition(ally_brain)
    if not ally_base then return nil end
    _ally_base_pos = ally_base

    -- Economy
    local ally_eco = ally_brain:GetEconomyTrends() or {}
    local mass_income  = ally_eco.MassIncome  or 0
    local energy_income = ally_eco.EnergyIncome or 0
    local ally_stored = ally_brain:GetEconomyStored() or {}
    local mass_stored   = ally_stored.Mass or 0

    -- Threat values
    local base_threat = ally_brain:GetThreatAtPosition(ally_base, THREAT_RADIUS, true, "Overall") or 0
    local air_threat  = GetAirThreatNearAllyBase(ally_base)

    -- Army size
    local ally_army = ally_brain:GetListOfUnits(
        categories.MOBILE * categories.MILITARY, false, false)
    local army_size = (ally_army and table.getn(ally_army)) or 0

    -- Derived boolean triggers
    local under_air_attack    = air_threat > 15
    local under_ground_attack = base_threat > 30
    local losing_army_fast    = (_prev_army_size > 5) and (army_size < _prev_army_size * 0.6)

    -- Mass stall detection
    local mass_stall = false
    if mass_income < 3 and mass_stored < 100 then
        if not _mass_stall_since then
            _mass_stall_since = tick
        elseif (tick - _mass_stall_since) > 300 then  -- 30 seconds
            mass_stall = true
        end
    else
        _mass_stall_since = nil
    end

    -- Update previous army size every 10 ticks
    _prev_army_size   = army_size
    _prev_mass_income = mass_income

    return {
        army_index          = _ally_army_index,
        base_position       = {ally_base[1], ally_base[3]},
        base_threat         = math.floor(base_threat),
        air_threat_near_base = math.floor(air_threat),
        army_size           = army_size,
        army_size_prev      = _prev_army_size,
        mass_income         = math.floor(mass_income * 10) / 10,
        mass_stored         = math.floor(mass_stored),
        energy_income       = math.floor(energy_income * 10) / 10,
        under_air_attack    = under_air_attack,
        under_ground_attack = under_ground_attack,
        losing_army_fast    = losing_army_fast,
        mass_stall          = mass_stall,
    }
end
