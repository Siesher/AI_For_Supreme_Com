-- ObservationHandlers.lua
--
-- Read-only observation tools for the LLM agent ReAct loop.
-- Each handler receives (brain, args) and returns a result table.
-- Results are sent back to Python via the named pipe.

local ObservationHandlers = {}

-- -----------------------------------------------------------------------
-- get_enemy_army — count enemy units by type
-- -----------------------------------------------------------------------
function ObservationHandlers.get_enemy_army(brain, args)
    local land  = 0
    local air   = 0
    local navy  = 0
    local total = 0
    local has_experimentals = false

    if ArmyBrains then
        for _, eb in ipairs(ArmyBrains) do
            if eb and eb ~= brain
               and IsEnemy(brain:GetArmyIndex(), eb:GetArmyIndex())
            then
                local ok_l, land_u = pcall(eb.GetListOfUnits, eb,
                    categories.MOBILE * categories.LAND - categories.ENGINEER - categories.COMMAND,
                    false, false)
                if ok_l and land_u then
                    land = land + table.getn(land_u)
                end

                local ok_a, air_u = pcall(eb.GetListOfUnits, eb,
                    categories.MOBILE * categories.AIR,
                    false, false)
                if ok_a and air_u then
                    air = air + table.getn(air_u)
                end

                local ok_n, nav_u = pcall(eb.GetListOfUnits, eb,
                    categories.MOBILE * categories.NAVAL,
                    false, false)
                if ok_n and nav_u then
                    navy = navy + table.getn(nav_u)
                end

                local ok_e, exp_u = pcall(eb.GetListOfUnits, eb,
                    categories.EXPERIMENTAL,
                    false, false)
                if ok_e and exp_u and table.getn(exp_u) > 0 then
                    has_experimentals = true
                end
            end
        end
    end

    total = land + air + navy
    return {
        success = true,
        data = {
            land  = land,
            air   = air,
            navy  = navy,
            total = total,
            has_experimentals = has_experimentals,
        },
    }
end

-- -----------------------------------------------------------------------
-- get_threat_at — threat level at a map position
-- -----------------------------------------------------------------------
function ObservationHandlers.get_threat_at(brain, args)
    local position_name = args.position or "own_base"
    local pos = nil
    local radius = 100

    if position_name == "own_base" then
        local ok, acu_list = pcall(brain.GetListOfUnits, brain,
            categories.COMMAND, false, false)
        if ok and acu_list and table.getn(acu_list) > 0 and not acu_list[1].Dead then
            pos = acu_list[1]:GetPosition()
        end
    elseif position_name == "enemy_base" then
        if ArmyBrains then
            for _, eb in ipairs(ArmyBrains) do
                if eb and eb ~= brain
                   and IsEnemy(brain:GetArmyIndex(), eb:GetArmyIndex())
                then
                    local ok, ecl = pcall(eb.GetListOfUnits, eb,
                        categories.COMMAND, false, false)
                    if ok and ecl and table.getn(ecl) > 0 and not ecl[1].Dead then
                        pos = ecl[1]:GetPosition()
                        break
                    end
                end
            end
        end
    else
        -- Directional positions: north/south/east/west/center
        local map_size = ScenarioInfo.size[1] or 512
        local half = map_size / 2
        local offsets = {
            north  = {half, 0, map_size * 0.15},
            south  = {half, 0, map_size * 0.85},
            east   = {map_size * 0.85, 0, half},
            west   = {map_size * 0.15, 0, half},
            center = {half, 0, half},
        }
        pos = offsets[position_name]
    end

    if not pos then
        return {success = false, error = "cannot resolve position: " .. tostring(position_name)}
    end

    local overall = brain:GetThreatAtPosition(pos, radius, true, 'Overall') or 0
    local land_t  = brain:GetThreatAtPosition(pos, radius, true, 'Land') or 0
    local air_t   = brain:GetThreatAtPosition(pos, radius, true, 'Air') or 0
    local struct  = brain:GetThreatAtPosition(pos, radius, true, 'Structures') or 0

    return {
        success = true,
        data = {
            overall    = overall,
            land       = land_t,
            air        = air_t,
            structures = struct,
        },
    }
end

-- -----------------------------------------------------------------------
-- get_mass_points — mass extraction point status
-- -----------------------------------------------------------------------
function ObservationHandlers.get_mass_points(brain, args)
    local total       = 0
    local controlled  = 0
    local uncontrolled = 0
    local contested   = 0

    -- Get all mass markers from the scenario
    local markers = ScenarioUtils.GetMarkers()
    if not markers then
        return {success = true, data = {total = 0, controlled = 0, uncontrolled = 0, contested = 0}}
    end

    local my_army = brain:GetArmyIndex()
    for _, marker in markers do
        if marker.type == 'Mass' then
            total = total + 1
            local pos = marker.position
            if pos then
                -- Check who has extractors near this point
                local my_threat  = brain:GetThreatAtPosition(pos, 20, false, 'Economy') or 0
                local en_threat  = brain:GetThreatAtPosition(pos, 20, true,  'Economy') or 0

                if my_threat > 0 and en_threat == 0 then
                    controlled = controlled + 1
                elseif my_threat == 0 and en_threat == 0 then
                    uncontrolled = uncontrolled + 1
                else
                    contested = contested + 1
                end
            end
        end
    end

    return {
        success = true,
        data = {
            total       = total,
            controlled  = controlled,
            uncontrolled = uncontrolled,
            contested   = contested,
        },
    }
end

-- -----------------------------------------------------------------------
-- get_my_factories — factory status by tech level
-- -----------------------------------------------------------------------
function ObservationHandlers.get_my_factories(brain, args)
    local result = {
        t1_land = 0, t2_land = 0, t3_land = 0,
        t1_air  = 0, t2_air  = 0, t3_air  = 0,
        idle    = 0, building = 0,
    }

    local ok, facs = pcall(brain.GetListOfUnits, brain,
        categories.FACTORY, false, false)
    if not ok or not facs then
        return {success = true, data = result}
    end

    for _, fac in ipairs(facs) do
        if not fac.Dead then
            local is_land = EntityCategoryContains(categories.LAND, fac)
            local is_air  = EntityCategoryContains(categories.AIR, fac)

            if EntityCategoryContains(categories.TECH3, fac) then
                if is_land then result.t3_land = result.t3_land + 1
                elseif is_air then result.t3_air = result.t3_air + 1 end
            elseif EntityCategoryContains(categories.TECH2, fac) then
                if is_land then result.t2_land = result.t2_land + 1
                elseif is_air then result.t2_air = result.t2_air + 1 end
            else
                if is_land then result.t1_land = result.t1_land + 1
                elseif is_air then result.t1_air = result.t1_air + 1 end
            end

            if fac:IsIdleState() then
                result.idle = result.idle + 1
            else
                result.building = result.building + 1
            end
        end
    end

    return {success = true, data = result}
end

-- -----------------------------------------------------------------------
-- get_map_control — overall map control percentage + zone breakdown
-- -----------------------------------------------------------------------
function ObservationHandlers.get_map_control(brain, args)
    local map_size = ScenarioInfo.size[1] or 512
    local half = map_size / 2
    local sample_radius = 80

    -- Sample 4 quadrants + center
    local zones = {
        {name = "north", pos = {half, 0, map_size * 0.2}},
        {name = "south", pos = {half, 0, map_size * 0.8}},
        {name = "east",  pos = {map_size * 0.8, 0, half}},
        {name = "west",  pos = {map_size * 0.2, 0, half}},
        {name = "center", pos = {half, 0, half}},
    }

    local my_score = 0
    local en_score = 0
    local zone_results = {}

    for _, zone in ipairs(zones) do
        local my_t = brain:GetThreatAtPosition(zone.pos, sample_radius, false, 'Overall') or 0
        local en_t = brain:GetThreatAtPosition(zone.pos, sample_radius, true,  'Overall') or 0

        local control = "contested"
        if my_t > en_t * 1.5 then
            control = "own"
            my_score = my_score + 1
        elseif en_t > my_t * 1.5 then
            control = "enemy"
            en_score = en_score + 1
        end

        table.insert(zone_results, {name = zone.name, control = control})
    end

    local total = my_score + en_score
    local pct = 50
    if total > 0 then
        pct = math.floor(my_score / total * 100)
    end

    return {
        success = true,
        data = {
            pct   = pct,
            zones = zone_results,
        },
    }
end

return ObservationHandlers
