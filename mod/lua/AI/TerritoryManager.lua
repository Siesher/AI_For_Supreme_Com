-- T038: Territory Manager — mod/lua/AI/TerritoryManager.lua
--
-- In ally mode, restricts the bot's build zone to its own half of the map
-- to avoid blocking the ally player's expansion.
--
-- Called from LLMAIBrain.OnCreateAI in ally-mode branch.
-- Exposes IsInBotTerritory(position) for StrategyExecutor.

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local _bot_start_pos    = nil   -- {x, z} — bot ACU start position
local _player_start_pos = nil   -- {x, z} — player ACU start position
local _map_w            = nil
local _map_h            = nil
local _midpoint         = nil   -- midpoint between bot and player starts

-- ---------------------------------------------------------------------------
-- Initialisation (call once from LLMAIBrain.OnCreateAI in ally mode)
-- ---------------------------------------------------------------------------

---Initialise territory split from ACU start positions.
---@param brain LLMAIBrain
function Init(brain)
    _map_w, _map_h = GetMapSize()

    -- Bot start position
    local bot_acu = brain:GetListOfUnits(categories.COMMAND, false, false)
    if bot_acu and table.getn(bot_acu) > 0 then
        local p = bot_acu[1]:GetPosition()
        _bot_start_pos = {p[1], p[3]}
    else
        _bot_start_pos = {_map_w / 4, _map_h / 4}
    end

    -- Player start position — from ArmyBrains ally
    if ArmyBrains then
        for _, other in ipairs(ArmyBrains) do
            if other and other ~= brain
               and IsAlly(brain:GetArmyIndex(), other:GetArmyIndex())
            then
                local other_acu = other:GetListOfUnits(categories.COMMAND, false, false)
                if other_acu and table.getn(other_acu) > 0 then
                    local pp = other_acu[1]:GetPosition()
                    _player_start_pos = {pp[1], pp[3]}
                end
                break
            end
        end
    end

    if not _player_start_pos then
        -- Fallback: assume player is on the opposite quadrant
        _player_start_pos = {_map_w - _bot_start_pos[1], _map_h - _bot_start_pos[2]}
    end

    -- Midpoint between bot and player (defines the split line)
    _midpoint = {
        (_bot_start_pos[1] + _player_start_pos[1]) / 2,
        (_bot_start_pos[2] + _player_start_pos[2]) / 2,
    }

    LOG(string.format("[TerritoryManager] Bot start: (%.0f, %.0f) | Player start: (%.0f, %.0f) | Mid: (%.0f, %.0f)",
        _bot_start_pos[1], _bot_start_pos[2],
        _player_start_pos[1], _player_start_pos[2],
        _midpoint[1], _midpoint[2]))
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Returns true if the given position is within the bot's territory.
---Used by StrategyExecutor/BuildOrderUEF to restrict build locations.
---@param pos table  {x, y, z} or {x, z}
---@return boolean
function IsInBotTerritory(pos)
    if not _bot_start_pos or not _midpoint then return true end  -- not initialised

    local px = pos[1]
    local pz = pos[3] or pos[2]  -- handle {x,y,z} and {x,z}

    -- Territory = closer to bot start than to player start
    local dist_to_bot    = math.sqrt((px - _bot_start_pos[1])^2 + (pz - _bot_start_pos[2])^2)
    local dist_to_player = math.sqrt((px - _player_start_pos[1])^2 + (pz - _player_start_pos[2])^2)

    return dist_to_bot <= dist_to_player
end

---Return bot start position.
---@return table|nil  {x, z}
function GetBotStartPosition()
    return _bot_start_pos
end

---Return midpoint of the territory split line.
---@return table|nil  {x, z}
function GetMidpoint()
    return _midpoint
end
