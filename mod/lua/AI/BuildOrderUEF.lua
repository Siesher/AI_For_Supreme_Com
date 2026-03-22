-- T021: UEF Build Order Templates — mod/lua/AI/BuildOrderUEF.lua
--
-- Build templates keyed by strategy name.
-- Each template is an ordered list of unit/structure blueprint IDs.
-- Used by StrategyExecutor to issue factory queue commands.
--
-- Blueprint ID naming convention (SupCom:FA UEF):
--   ueb0101 = T1 Land Factory   ueb0102 = T2 Land Factory   ueb0301 = T3 Land Factory
--   uel0101 = T1 Engineer       uel0201 = T2 Engineer
--   uel0106 = T1 Tank (Mech Marine)
--   uel0201 = T2 Tank (Pillar)   uel0202 = T2 Mobile Shield (Parashield)
--   ueb1103 = T1 Mass Extractor  ueb1104 = T2 Mass Extractor
--   ueb1101 = T1 Power Generator ueb1201 = T2 Power Generator
--   ueb4201 = T2 Point Defence   ueb4202 = T2 Shield Generator
--   ueb2302 = T2 Artillery (Arty)

-- ---------------------------------------------------------------------------
-- Template definitions
-- ---------------------------------------------------------------------------

---@type table<string, string[]>
local Templates = {}

-- land_rush: Mass T1 attack within 3-4 minutes.
-- Build 5 land factories → flood T1 tanks → attack immediately.
Templates["land_rush"] = {
    "ueb1103",  -- T1 Mass Extractor (×2 from first engineer)
    "ueb1101",  -- T1 Power Generator
    "ueb0101",  -- T1 Land Factory #1
    "ueb1103",  -- Mass Extractor
    "ueb0101",  -- T1 Land Factory #2
    "ueb1101",  -- Power Generator
    "ueb0101",  -- T1 Land Factory #3
    "ueb1103",  -- Mass Extractor
    "ueb0101",  -- T1 Land Factory #4
    "ueb0101",  -- T1 Land Factory #5
    -- Factory queue unit: Mech Marine (T1 tank)
    -- Factories are set to spam: uel0106
}

-- tech_up: Transition to T2 for medium-term dominance.
Templates["tech_up"] = {
    "ueb1103",  -- Mass Extractor
    "ueb1103",  -- Mass Extractor
    "ueb1101",  -- Power Generator
    "ueb0101",  -- T1 Land Factory
    "ueb1104",  -- T2 Mass Extractor upgrade (from T1 MEX)
    "ueb0102",  -- T2 Land Factory (upgrade from T1 factory)
    "ueb1201",  -- T2 Power Generator
    "ueb1104",  -- More T2 MEXes
    "ueb0102",  -- Second T2 Land Factory
    "ueb1201",  -- Power Generator
    -- Factory queue unit: Pillar T2 tank (uel0201)
}

-- turtle: Fortified base defense before attacking.
Templates["turtle"] = {
    "ueb1103",  -- Mass Extractor
    "ueb1101",  -- Power Generator
    "ueb0101",  -- T1 Land Factory
    "ueb4201",  -- T2 Point Defence (×4 ring around base)
    "ueb4201",
    "ueb4201",
    "ueb4201",
    "ueb4202",  -- T2 Shield Generator
    "ueb4202",  -- Second shield
    "ueb2302",  -- T2 Artillery (Arty)
    "ueb0102",  -- T2 Factory for T2 units
    -- Factory queue unit: Parashield (uel0202) + Pillar mix
}

-- balanced: Mixed economy + military, moderate expansion.
-- Start with Power Generator (can be built anywhere near ACU) so the ACU
-- acts immediately at spawn, before walking to a mass deposit.
Templates["balanced"] = {
    "ueb1101",  -- T1 Power Generator  (first — no deposit needed)
    "ueb1103",  -- Mass Extractor
    "ueb0101",  -- T1 Land Factory
    "ueb1103",  -- More MEXes
    "ueb1103",
    "ueb1101",  -- Power
    "ueb0101",  -- T1 Air Factory (second factory)
    "ueb1104",  -- Upgrade to T2 MEX
    "ueb0102",  -- T2 Land Factory upgrade
    "ueb1201",  -- T2 Power Generator
    -- Mix of Mech Marine + Pillar from factories
}

-- air: Prioritise air power for map control.
Templates["air"] = {
    "ueb1103",  -- Mass Extractor
    "ueb1101",  -- Power Generator
    "ueb0201",  -- T1 Air Factory
    "ueb1103",  -- MEX
    "ueb1101",  -- Power
    "ueb0201",  -- Second Air Factory
    "ueb1201",  -- T2 Power (air is energy-hungry)
    "ueb1201",
    "ueb0202",  -- T2 Air Factory
    "ueb1104",  -- T2 MEX
    -- Factory queue unit: T1 Interceptor (uea0101) + T2 Gunship (uea0203)
}

-- naval: Expand to water, destroyers for sea control.
Templates["naval"] = {
    "ueb1103",
    "ueb1101",
    "ueb1701",  -- T1 Naval Factory
    "ueb1103",
    "ueb1101",
    "ueb1701",  -- Second Naval Factory
    "ueb1702",  -- T2 Naval Factory upgrade
    "ueb1104",
    "ueb1201",
    -- Factory queue: Frigate (ues0103) → Destroyer (ues0201)
}

-- ---------------------------------------------------------------------------
-- Unit spam queues per strategy (sent to each factory)
-- ---------------------------------------------------------------------------

---@type table<string, string[]>
local FactoryQueue = {
    land_rush = { "uel0106", "uel0106", "uel0106", "uel0101" }, -- 3× tank, 1× engineer
    tech_up   = { "uel0201", "uel0202", "uel0101" },            -- Pillar + shield + eng
    turtle    = { "uel0202", "uel0201", "uel0101" },            -- Shield-heavy
    balanced  = { "uel0106", "uel0201", "uel0101" },            -- Mixed
    air       = { "uea0101", "uea0203", "uea0101" },            -- Interceptor + Gunship
    naval     = { "ues0103", "ues0201" },                       -- Frigate + Destroyer
}

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Return the build template for the given strategy name.
---@param strategy_name string
---@return string[]  ordered list of blueprint IDs
function GetTemplate(strategy_name)
    return Templates[strategy_name] or Templates["balanced"]
end

---Return the factory spam queue for the given strategy.
---@param strategy_name string
---@return string[]  unit blueprint IDs to queue in each factory
function GetFactoryQueue(strategy_name)
    return FactoryQueue[strategy_name] or FactoryQueue["balanced"]
end

---List all available strategy names.
---@return string[]
function GetStrategyNames()
    local names = {}
    for k, _ in pairs(Templates) do
        table.insert(names, k)
    end
    return names
end
