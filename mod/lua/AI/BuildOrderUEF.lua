-- T021: UEF Build Order Templates — mod/lua/AI/BuildOrderUEF.lua
--
-- Build templates keyed by strategy name.
-- Each template is an ordered list of unit/structure blueprint IDs.
-- Used by StrategyExecutor to issue factory queue commands.
--
-- IMPORTANT: Only T1 structures that the ACU can build at game start.
-- T2 upgrades (ueb1104, ueb0102, etc.) require T2 engineers / upgraded
-- factories and cannot be issued via IssueBuildMobile from a T1 ACU.
--
-- Blueprint ID naming convention (SupCom:FA UEF):
--   ueb0101 = T1 Land Factory   ueb0201 = T1 Air Factory   ueb1701 = T1 Naval Factory
--   ueb1103 = T1 Mass Extractor
--   ueb1101 = T1 Power Generator
--   ueb2101 = T1 Point Defence
--   ueb5101 = T1 Wall Section
--   ueb3101 = T1 Radar
--   uel0106 = T1 Tank (Mech Marine)
--   uel0101 = T1 Engineer

-- ---------------------------------------------------------------------------
-- Template definitions (T1 only — ACU buildable)
-- ---------------------------------------------------------------------------

---@type table<string, string[]>
local Templates = {}

-- land_rush: Mass T1 attack within 3-4 minutes.
-- Build multiple land factories → flood T1 tanks → attack immediately.
Templates["land_rush"] = {
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory #1
    "ueb1101",  -- T1 Power Generator
    "ueb0101",  -- T1 Land Factory #2
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory #3
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory #4
    "ueb0101",  -- T1 Land Factory #5
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb3101",  -- T1 Radar
}

-- tech_up: Build economy foundation for T2 transition.
-- Engineers from factories will handle T2 upgrades.
Templates["tech_up"] = {
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb0101",  -- T1 Land Factory #2
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb3101",  -- T1 Radar
    "ueb1101",  -- T1 Power Generator
    "ueb1101",  -- T1 Power Generator
}

-- turtle: Fortified base defense before attacking.
Templates["turtle"] = {
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory
    "ueb2101",  -- T1 Point Defence
    "ueb1103",  -- T1 Mass Extractor
    "ueb2101",  -- T1 Point Defence
    "ueb1101",  -- T1 Power Generator
    "ueb2101",  -- T1 Point Defence
    "ueb1103",  -- T1 Mass Extractor
    "ueb2101",  -- T1 Point Defence
    "ueb1101",  -- T1 Power Generator
    "ueb0101",  -- T1 Land Factory #2
    "ueb3101",  -- T1 Radar
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
}

-- balanced: Mixed economy + military, moderate expansion.
Templates["balanced"] = {
    "ueb1101",  -- T1 Power Generator (first — no deposit needed)
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory
    "ueb1103",  -- T1 Mass Extractor
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb0201",  -- T1 Air Factory
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory #2
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb3101",  -- T1 Radar
    "ueb1101",  -- T1 Power Generator
    "ueb0101",  -- T1 Land Factory #3
}

-- air: Prioritise air power for map control.
Templates["air"] = {
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb0201",  -- T1 Air Factory #1
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb0201",  -- T1 Air Factory #2
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb0201",  -- T1 Air Factory #3
    "ueb1101",  -- T1 Power Generator
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb3101",  -- T1 Radar
    "ueb0101",  -- T1 Land Factory (some land defense)
}

-- naval: Expand to water, build frigates.
Templates["naval"] = {
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory (land defense)
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb3101",  -- T1 Radar
}

-- Expansion loop: after main template is done, keep building economy + military.
-- StrategyExecutor loops through this after the primary template is exhausted.
local ExpansionLoop = {
    "ueb1103",  -- T1 Mass Extractor
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
    "ueb0101",  -- T1 Land Factory
    "ueb1101",  -- T1 Power Generator
    "ueb1103",  -- T1 Mass Extractor
}

-- ---------------------------------------------------------------------------
-- Unit spam queues per strategy (sent to each factory)
-- ---------------------------------------------------------------------------

---@type table<string, string[]>
local FactoryQueue = {
    land_rush = { "uel0106", "uel0106", "uel0106", "uel0101" }, -- 3x tank, 1x engineer
    tech_up   = { "uel0106", "uel0106", "uel0101" },            -- tank + engineer
    turtle    = { "uel0106", "uel0106", "uel0101" },            -- tank + engineer
    balanced  = { "uel0106", "uel0106", "uel0101" },            -- Mixed
    air       = { "uea0101", "uea0101", "uea0101" },            -- Interceptors
    naval     = { "ues0103", "ues0103" },                       -- Frigates
    engineers = { "uel0101", "uel0101" },                       -- T1 engineers (build_units priority=engineers)
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

---Return the expansion loop template (used after main template is exhausted).
---@return string[]
function GetExpansionLoop()
    return ExpansionLoop
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
