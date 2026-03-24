-- T024: Tactical Micro -- mod/lua/AI/TacticalMicro.lua
--
-- TacticalMicroThread(brain) coroutine.
-- Runs every 5 game-ticks; handles unit-level micro independent of the LLM.
--
-- Responsibilities:
--   1. Issue attack-move orders toward the current attack direction.
--   2. Retreat units below retreat_threshold HP.
--   3. Focus-fire on priority targets: ACU -> experimental -> arty -> other.

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- Map cardinal direction names to bearing vectors (approximate).
-- Actual game north = negative Z in SupCom coordinate system.
local DIRECTION_VECTORS = {
    north    = { 0,  0, -1},
    south    = { 0,  0,  1},
    east     = { 1,  0,  0},
    west     = {-1,  0,  0},
    none     = nil,
}

-- Initialized lazily on first call (categories not available at import time)
local _focus_fire_priority = nil

local function GetBestTarget(unit, enemy_units)
    if not _focus_fire_priority then
        _focus_fire_priority = {
            categories.COMMAND,
            categories.EXPERIMENTAL,
            categories.ARTILLERY,
            categories.DEFENSE * categories.STRUCTURE,
            categories.MOBILE,
        }
    end
    for _, prio_cat in ipairs(_focus_fire_priority) do
        local candidates = EntityCategoryFilterSet(prio_cat, enemy_units)
        if candidates and table.getn(candidates) > 0 then
            local pos      = unit:GetPosition()
            local closest  = nil
            local min_dist = 9999
            for _, t in ipairs(candidates) do
                if t and not t.Dead then
                    local d = VDist3(pos, t:GetPosition())
                    if d < min_dist then
                        min_dist = d
                        closest  = t
                    end
                end
            end
            if closest then return closest end
        end
    end
    return nil
end

local function DirectionToPosition(origin, direction_name, distance)
    local vec = DIRECTION_VECTORS[direction_name]
    if not vec then return nil end
    return {
        origin[1] + vec[1] * distance,
        origin[2],
        origin[3] + vec[3] * distance,
    }
end

local function GetUnitHPFraction(unit)
    local max_hp = unit:GetMaxHealth()
    if not max_hp or max_hp <= 0 then return 1.0 end
    return unit:GetHealth() / max_hp
end

-- ---------------------------------------------------------------------------
-- Main thread
-- ---------------------------------------------------------------------------

function TacticalMicroThread(brain)
    local ATTACK_DISTANCE = 800
    local SCAN_RADIUS     = 60

    while true do
        WaitTicks(5)

        if not brain or brain.Dead then break end

        local attack_dir    = brain.AttackDirection
        local retreat_thr   = brain.RetreatThreshold or 0.3
        local acu_list      = brain:GetListOfUnits(categories.COMMAND, false, false)
        local base_pos      = (acu_list and table.getn(acu_list) > 0) and acu_list[1]:GetPosition() or {0, 0, 0}

        local attack_pos = nil
        if attack_dir and attack_dir ~= "none" then
            attack_pos = DirectionToPosition(base_pos, attack_dir, ATTACK_DISTANCE)
        end

        local land_units = brain:GetListOfUnits(
            categories.LAND * categories.MOBILE - categories.ENGINEER - categories.COMMAND, false, false)

        if land_units then
            local attack_group  = {}
            local retreat_group = {}

            for _, unit in ipairs(land_units) do
                if unit and not unit.Dead then
                    local hp_frac = GetUnitHPFraction(unit)
                    if hp_frac < retreat_thr then
                        table.insert(retreat_group, unit)
                    else
                        table.insert(attack_group, unit)
                    end
                end
            end

            if table.getn(retreat_group) > 0 then
                IssueMoveToPosition(retreat_group, base_pos)
            end

            if table.getn(attack_group) > 0 then
                local focus_fired = false
                local leader_pos = attack_group[1]:GetPosition()
                local nearby_enemies = GetUnitsInRect(
                    Rect(leader_pos[1] - SCAN_RADIUS, leader_pos[3] - SCAN_RADIUS,
                         leader_pos[1] + SCAN_RADIUS, leader_pos[3] + SCAN_RADIUS))
                if nearby_enemies and table.getn(nearby_enemies) > 0 then
                    local enemy_units = {}
                    for _, u in ipairs(nearby_enemies) do
                        if u and not u.Dead
                            and IsEnemy(brain:GetArmyIndex(), u:GetArmyIndex())
                        then
                            table.insert(enemy_units, u)
                        end
                    end
                    if table.getn(enemy_units) > 0 then
                        local best = GetBestTarget(attack_group[1], enemy_units)
                        if best then
                            IssueAttack(attack_group, best)
                            focus_fired = true
                        end
                    end
                end

                if not focus_fired and attack_pos then
                    IssueAggressiveMove(attack_group, attack_pos)
                end
            end
        end
    end
end

-- ---------------------------------------------------------------------------
-- Air micro -- patrol interceptors near own base
-- ---------------------------------------------------------------------------

function AirMicroThread(brain)
    local PATROL_RADIUS = 300

    while true do
        WaitTicks(10)

        if not brain or brain.Dead then break end

        local interceptors = brain:GetListOfUnits(
            categories.AIR * categories.MOBILE * categories.ANTIAIR, false, false)

        if interceptors and table.getn(interceptors) > 0 then
            local acu_list = brain:GetListOfUnits(categories.COMMAND, false, false)
            local base_pos = (acu_list and table.getn(acu_list) > 0) and acu_list[1]:GetPosition() or {0, 0, 0}

            for _, unit in ipairs(interceptors) do
                if unit and not unit.Dead then
                    local queue = unit:GetCommandQueue()
                    if not queue or table.getn(queue) == 0 then
                        local patrol_pos = {
                            base_pos[1] + math.random(-PATROL_RADIUS, PATROL_RADIUS),
                            base_pos[2],
                            base_pos[3] + math.random(-PATROL_RADIUS, PATROL_RADIUS),
                        }
                        IssuePatrol({unit}, patrol_pos)
                    end
                end
            end
        end
    end
end
