-- T016: LLM Bridge Lua wrapper — mod/lua/AI/LLMBridge.lua
--
-- Wraps DLL calls with nil-safety and JSON parsing.
-- The DLL (supcom_llm_bridge.dll) registers LLMBridge.* as a Lua global.
-- This module provides a higher-level API for use in LLMAIBrain.
--
-- Usage:
--   local LLMBridge = import("/mods/supcom-llm-ai-bot/lua/AI/LLMBridge.lua")
--   LLMBridge.SendSnapshot(brain, "periodic")
--   local cmd = LLMBridge.PollCommand()  -- returns table or nil

-- Load the native DLL so it registers LLMBridge.* in the global Lua state.
-- The DLL must be at C:\ProgramData\FAForever\bin\llm_bridge.dll
--
-- SupCom's LuaPlus treats require() files as text — cannot load binary DLLs.
-- Use loadlib (Lua 5.0 global) or package.loadlib (Lua 5.1) instead.
-- Both are checked via rawget to bypass FAF's strict global checker.
local _dll_path = "C:\\ProgramData\\FAForever\\bin\\llm_bridge.dll"
local _dll_func = "luaopen_llm_bridge"

-- Try Lua 5.0 global loadlib first, then package.loadlib (Lua 5.1 style)
local _loadlib = rawget(_G, "loadlib")
if not _loadlib then
    local _pkg = rawget(_G, "package")
    if _pkg then _loadlib = rawget(_pkg, "loadlib") end
end

if _loadlib then
    local _loader, _load_err = _loadlib(_dll_path, _dll_func)
    if _loader then
        local _ok, _err = pcall(_loader)
        if _ok then
            LOG("[LLMBridge] loadlib OK — DLL loaded")
        else
            LOG("[LLMBridge] loadlib init error: " .. tostring(_err))
        end
    else
        LOG("[LLMBridge] loadlib failed: " .. tostring(_load_err))
    end
else
    LOG("[LLMBridge] loadlib not available — running offline")
end

-- Capture the DLL global via rawget to bypass FAF's strict global checker.
-- FAF intercepts bare global variable reads via __index metamethod on _G;
-- rawget bypasses the metamethod and returns nil safely if not registered.
local _dll = rawget(_G, "LLMBridge")
if _dll then
    LOG("[LLMBridge] DLL global registered, pipe status=" .. tostring(pcall(_dll.GetStatus) and "ok" or "err"))
else
    LOG("[LLMBridge] DLL global NOT registered — running in offline mode")
end

local GameStateCollector = import("/mods/supcom-llm-ai-bot/lua/AI/GameStateCollector.lua")
local JSON = import("/mods/supcom-llm-ai-bot/lua/AI/JSON.lua")

-- ---------------------------------------------------------------------------
-- RetryConnect() — re-check rawget(_G, "LLMBridge") in case the DLL was
-- injected after this module was first imported.
-- Call periodically from StrategyUpdateThread.
-- Returns true if the DLL was newly found.
-- ---------------------------------------------------------------------------
function RetryConnect()
    if type(_dll) == "table" then return false end  -- already connected

    local fresh = rawget(_G, "LLMBridge")
    if type(fresh) == "table" then
        _dll = fresh   -- update upvalue; all functions in this module now use it
        LOG("[LLMBridge] DLL found after late injection — switching to online mode")
        return true
    end
    return false
end

-- ---------------------------------------------------------------------------
-- IsConnected() — safe wrapper around the DLL function
-- ---------------------------------------------------------------------------
function IsConnected()
    if type(_dll) ~= "table" then return false end
    local ok, result = pcall(_dll.IsConnected)
    return ok and result == true
end

-- ---------------------------------------------------------------------------
-- GetStatus() — returns status string for debug overlay
-- ---------------------------------------------------------------------------
function GetStatus()
    if type(_dll) ~= "table" then return "no_dll" end
    local ok, result = pcall(_dll.GetStatus)
    return ok and result or "error"
end

-- ---------------------------------------------------------------------------
-- SendSnapshot(brain, trigger_event, ally_state, chat_messages)
--   Collects current game state and sends via DLL pipe. Non-blocking.
-- ---------------------------------------------------------------------------
function SendSnapshot(brain, trigger_event, ally_state, chat_messages)
    if not IsConnected() then
        -- Silently discard — not connected, no point collecting
        return
    end

    local ok, json_str = pcall(
        GameStateCollector.Collect,
        brain, trigger_event or "periodic", ally_state, chat_messages
    )
    if not ok then
        LOG("[LLMBridge] GameStateCollector.Collect failed: " .. tostring(json_str))
        return
    end

    -- Wrap in the pipe protocol envelope: {"type":"snapshot","data":{...}}
    local envelope = '{"type":"snapshot","data":' .. json_str .. '}'

    local send_ok, err = pcall(_dll.Send, envelope)
    if not send_ok then
        LOG("[LLMBridge] Send failed: " .. tostring(err))
    end
end

-- ---------------------------------------------------------------------------
-- PollCommand() — dequeues and parses the latest StrategicDecision.
--   Returns parsed Lua table, or nil if no command is available.
-- ---------------------------------------------------------------------------
function PollCommand()
    if type(_dll) ~= "table" then return nil end

    local ok, raw = pcall(_dll.Receive)
    if not ok or raw == nil then return nil end

    -- Parse JSON envelope: {"type":"command","data":{...}}
    local envelope, decode_err = JSON.decode(raw)
    if type(envelope) ~= "table" then
        LOG("[LLMBridge] Failed to decode envelope: " .. tostring(decode_err) .. " raw=" .. tostring(raw))
        return nil
    end

    if envelope.type == "heartbeat" then
        return nil  -- heartbeat — no action needed
    end

    if envelope.type == "command" and type(envelope.data) == "table" then
        return envelope.data
    end

    LOG("[LLMBridge] Unknown message type: " .. tostring(envelope.type))
    return nil
end

-- ---------------------------------------------------------------------------
-- SendRaw(json_str) — for save/load state messages and other raw envelopes
-- ---------------------------------------------------------------------------
function SendRaw(json_str)
    if not IsConnected() then return end
    local ok, err = pcall(_dll.Send, json_str)
    if not ok then
        LOG("[LLMBridge] SendRaw failed: " .. tostring(err))
    end
end
