-- T022: Economy Manager — mod/lua/AI/EconomyManager.lua
--
-- Tick() called every 30 game-ticks from LLMAIBrain.
-- Detects stalls, assigns idle engineers, exposes economy summary.

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local _stall_start_tick   = nil   -- tick when mass stall began
local _e_stall_start_tick = nil   -- tick when energy stall began
local _mass_stall_notified = false
local _energy_stall_notified = false

local MASS_INCOME_STALL_THRESHOLD   = 3.0   -- mass/sec — below = stall
local ENERGY_RATIO_STALL_THRESHOLD  = 0.05  -- stored/max < 5% = stall
local STALL_DURATION_TICKS          = 300   -- 30s at 10 ticks/sec
local IDLE_ENGINEER_SCAN_RADIUS     = 100   -- game units

-- ---------------------------------------------------------------------------
-- Internal helpers
-- ---------------------------------------------------------------------------

local function IsEngineerIdle(unit)
    -- An engineer is idle if it has no orders
    local queue = unit:GetCommandQueue()
    return not queue or table.getn(queue) == 0
end

local function FindReclaimNear(pos, radius)
    -- Return first reclaimable wreck/prop within radius
    local props = GetReclaimablesInRect(
        Rect(pos[1] - radius, pos[3] - radius, pos[1] + radius, pos[3] + radius))
    if props and table.getn(props) > 0 then
        return props[1]
    end
    return nil
end

local function AssignIdleEngineers(brain)
    -- Regular engineers only — do NOT touch the ACU (COMMAND).
    -- The ACU's orders are managed by StrategyExecutor; AssignIdleEngineers
    -- would otherwise interrupt build sequences every 20 seconds.
    local engineers = brain:GetListOfUnits(
        categories.ENGINEER - categories.COMMAND, false, false)
    if not engineers or table.getn(engineers) == 0 then return 0 end

    local assigned = 0
    for _, eng in ipairs(engineers) do
        if eng and not eng:IsDead() and IsEngineerIdle(eng) then
            local pos = eng:GetPosition()

            -- 1. Try to reclaim nearby wreckage
            local reclaim_target = FindReclaimNear(pos, IDLE_ENGINEER_SCAN_RADIUS)
            if reclaim_target then
                IssueReclaim({eng}, reclaim_target)
                assigned = assigned + 1
            else
                -- 2. Try to assist a nearby factory
                local factories = brain:GetListOfUnits(
                    categories.FACTORY, false, false)
                if factories and table.getn(factories) > 0 then
                    -- Assist the closest factory
                    local closest_factory = nil
                    local closest_dist    = 9999
                    for _, fac in ipairs(factories) do
                        if fac and not fac:IsDead() then
                            local d = VDist3(pos, fac:GetPosition())
                            if d < closest_dist then
                                closest_dist    = d
                                closest_factory = fac
                            end
                        end
                    end
                    if closest_factory and closest_dist < 200 then
                        IssueGuard({eng}, closest_factory)
                        assigned = assigned + 1
                    end
                end
            end
        end
    end
    return assigned
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Called every 30 ticks from LLMAIBrain.
---Detects stalls, assigns idle engineers.
---@param brain LLMAIBrain
---@return table  stall status {mass_stall: bool, energy_stall: bool}
function Tick(brain)
    local tick         = GetGameTick()
    local mass_income  = brain:GetEconomyIncome('MASS')          or 0
    local energy_ratio = brain:GetEconomyStoredRatio('ENERGY')   or 1.0

    -- Mass stall detection
    local mass_stall = false
    if mass_income < MASS_INCOME_STALL_THRESHOLD then
        if not _stall_start_tick then
            _stall_start_tick = tick
        elseif (tick - _stall_start_tick) >= STALL_DURATION_TICKS then
            mass_stall = true
        end
    else
        _stall_start_tick = nil
        _mass_stall_notified = false
    end

    -- Energy stall detection
    local energy_stall = false
    if energy_ratio < ENERGY_RATIO_STALL_THRESHOLD then
        if not _e_stall_start_tick then
            _e_stall_start_tick = tick
        elseif (tick - _e_stall_start_tick) >= STALL_DURATION_TICKS then
            energy_stall = true
        end
    else
        _e_stall_start_tick = nil
        _energy_stall_notified = false
    end

    -- Assign idle engineers every tick call
    AssignIdleEngineers(brain)

    return {
        mass_stall   = mass_stall,
        energy_stall = energy_stall,
    }
end

---Return economy summary table for GameStateCollector.
---@param brain LLMAIBrain
---@return table
function GetEconomySummary(brain)
    local mass_income   = brain:GetEconomyIncome('MASS')         or 0
    local energy_income = brain:GetEconomyIncome('ENERGY')       or 0
    local mass_stored   = brain:GetEconomyStored('MASS')         or 0
    local energy_stored = brain:GetEconomyStored('ENERGY')       or 0
    local mass_ratio    = brain:GetEconomyStoredRatio('MASS')    or 0
    local energy_ratio  = brain:GetEconomyStoredRatio('ENERGY')  or 0

    local mass_max   = (mass_ratio   > 0) and (mass_stored   / mass_ratio)   or 1000
    local energy_max = (energy_ratio > 0) and (energy_stored / energy_ratio) or 8000

    return {
        mass_income        = math.floor(mass_income   * 10) / 10,
        mass_stored        = math.floor(mass_stored),
        mass_storage_max   = math.floor(mass_max),
        energy_income      = math.floor(energy_income * 10) / 10,
        energy_stored      = math.floor(energy_stored),
        energy_storage_max = math.floor(energy_max),
        mass_stall         = (_stall_start_tick ~= nil),
        energy_stall       = (_e_stall_start_tick ~= nil),
    }
end
