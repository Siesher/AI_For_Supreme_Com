-- FileIPC.lua — File-based IPC via Sync/SimCallback relay.
--
-- Sim layer (where AI brain runs) has NO io/os access.
-- UI layer DOES have io access.
--
-- Outbound (snapshot → Python):
--   Sim: Sync.LLMSnapshot = json_str   → UI OnSync reads it → io.open writes file
--
-- Inbound (command → Sim):
--   UI: periodic poll reads command.json → SimCallback('LLMCommand', data) → Sim
--
-- This module (sim-side) provides:
--   WriteSnapshot()  — puts JSON into Sync.LLMSnapshot
--   ReadCommand()    — reads from _G.LLMPendingCommand (set by SimCallback)
--   IsAvailable()    — always true (Sync/SimCallback are always available)

local JSON = import("/mods/supcom-llm-ai-bot/lua/AI/JSON.lua")
local GameStateCollector = import("/mods/supcom-llm-ai-bot/lua/AI/GameStateCollector.lua")

LOG("[FileIPC] Sync/SimCallback relay mode (sim-side)")


function IsAvailable()
    -- Sync and SimCallback are always available in sim
    return true
end


-- ── WriteSnapshot ───────────────────────────────────────────────────
-- Writes snapshot JSON to game log via LOG() — Python tails the log file.
-- Previously tried Sync.LLMSnapshot → UI → LOG, but large strings don't
-- propagate through Sync table reliably. Sim can LOG() directly.

local _snapshot_id = 0

function WriteSnapshot(brain, trigger_event, ally_state, chat_messages)
    local ok, json_str = pcall(
        GameStateCollector.Collect,
        brain, trigger_event or "periodic", ally_state, chat_messages
    )
    if not ok then
        LOG("[FileIPC] Collect error: " .. tostring(json_str))
        return false
    end

    _snapshot_id = _snapshot_id + 1

    -- Write to game log — Python tails this file
    LOG("[LLM_SNAP]" .. json_str)

    return true
end


-- ── ReadCommand ─────────────────────────────────────────────────────
-- Reads from _G.LLMPendingCommand, which is set by the SimCallback
-- registered in SimCallbacks.lua hook.

function ReadCommand()
    local pending = rawget(_G, 'LLMPendingCommand')
    if not pending then return nil end

    -- Consume it
    rawset(_G, 'LLMPendingCommand', nil)

    if type(pending) == "string" then
        local decode_ok, cmd = pcall(JSON.decode, pending)
        if decode_ok and type(cmd) == "table" then
            return cmd
        end
        LOG("[FileIPC] decode error: " .. tostring(cmd))
        return nil
    elseif type(pending) == "table" then
        return pending
    end

    return nil
end
