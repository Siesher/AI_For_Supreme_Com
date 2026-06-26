-- Hook: LLM AI Bot — gamemain.lua UI extension
--
-- Appended to FAF's /lua/ui/game/gamemain.lua by the hook system.
-- Responsibilities:
--   1. Display bot chat messages pushed from sim via Sync.LLMBotChat
--   2. Initialise LLMOverlay (Ctrl+Shift+D debug panel)
--   3. File IPC relay:
--      Outbound: Sync.LLMSnapshot → LOG("[LLM_SNAP]json") → Python reads game log
--      Inbound:  Python writes cmd_NNNN.lua → import() → SimCallback → Sim
--
-- io/os are NOT available in FAF's UI sandbox.
-- We use LOG() for outbound and import() for inbound.

LOG("[LLMBotUI] gamemain hook loading")

local _botChatLastId = 0
local _cmdCounter = 0  -- next command file number to check
local _CMD_VFS_PREFIX = "/mods/supcom-llm-ai-bot/ipc/cmd_"

-- ---------------------------------------------------------------------------
-- DisplayBotChat — injects a message into the game chat log
-- ---------------------------------------------------------------------------
local function DisplayBotChat(text, from)
    if not text or text == "" then return end
    LOG("[LLMBotUI] " .. (from or "AI Bot") .. ": " .. text)

    pcall(function()
        local chatMod = import("/lua/ui/game/chat.lua")
        if not chatMod then return end

        local msg = {
            sender  = from or "AI Bot",
            text    = text,
            color   = "FF00FF99",
        }

        if chatMod.ReceiveChatMessage then
            chatMod.ReceiveChatMessage(msg)
        elseif chatMod.AddMessage then
            chatMod.AddMessage(msg)
        elseif chatMod.AddLine then
            chatMod.AddLine(msg)
        end
    end)
end

-- ---------------------------------------------------------------------------
-- CheckSyncForBotChat — reads Sync.LLMBotChat and calls DisplayBotChat
-- ---------------------------------------------------------------------------
local function CheckSyncForBotChat()
    local syncData = rawget(_G, "Sync")
    if not syncData then return end
    local chatData = rawget(syncData, "LLMBotChat")
    if not chatData or type(chatData) ~= "table" then return end

    local id = chatData.id or 0
    if id ~= _botChatLastId then
        _botChatLastId = id
        DisplayBotChat(chatData.text, chatData.from)
    end
end

-- Snapshot relay removed — sim now calls LOG("[LLM_SNAP]json") directly.
-- (Large strings in Sync table did not propagate reliably to UI OnSync.)

-- ---------------------------------------------------------------------------
-- PollCommandFile — check for a command file via DiskGetFileInfo (silent),
-- and only import() when it exists (avoids spamming "Unable to find file"
-- warnings into the game log).
-- ---------------------------------------------------------------------------
local _dgfi = rawget(_G, "DiskGetFileInfo")
LOG("[LLMBotUI] DiskGetFileInfo available=" .. tostring(_dgfi ~= nil))

local _pollCount = 0

-- Import a command file and dispatch its payload to the sim.
-- Returns (consumed, err): consumed=true means advance the counter; on a true
-- import failure (file not resolvable via the VFS) returns (false, err).
-- `via` labels HOW the file was detected, so the game log shows which path works.
local function _ConsumeCommandFile(path, via)
    local ok, mod = pcall(import, path)
    if not ok then
        return false, mod
    end

    local payload = nil
    if type(mod) == "table" then
        payload = rawget(mod, "LLMCmd")  -- cmd file assigns LLMCmd = '<json>'
    elseif type(mod) == "string" then
        payload = mod                    -- legacy 'return' format tolerance
    end

    if type(payload) == "string" and payload ~= "" then
        LOG("[LLMBotUI] Command imported (" .. via .. "): " .. path
            .. " (" .. tostring(string.len(payload)) .. " chars)")
        pcall(function()
            SimCallback({ Func = 'LLMCommand', Args = payload })
        end)
    else
        LOG("[LLMBotUI] imported but no LLMCmd payload: " .. path .. " modtype=" .. type(mod))
    end
    return true  -- file existed (even if malformed) -> consume to avoid stalling
end

local function PollCommandFile()
    local path = _CMD_VFS_PREFIX .. string.format("%04d", _cmdCounter) .. ".lua"

    -- Throttled beat so diagnostics / fallback imports don't spam (poll ~3x/s).
    _pollCount = _pollCount + 1
    local beat = false
    if _pollCount >= 33 then _pollCount = 0; beat = true end

    -- Fast path: DiskGetFileInfo reports the file present -> import immediately.
    local seen = _dgfi and _dgfi(path)
    if seen then
        local consumed = _ConsumeCommandFile(path, "stat")
        if consumed then _cmdCounter = _cmdCounter + 1 end
        return
    end

    -- DiskGetFileInfo says absent. It can be stale for runtime-created files when
    -- the ipc/ dir was empty at mod-mount (SupCom VFS indexes dirs at mount). On
    -- each beat, try import() directly anyway -- it may resolve the file even when
    -- the stat does not. This is the inbound safety net.
    if beat then
        local consumed, err = _ConsumeCommandFile(path, "import-fallback")
        if consumed then
            _cmdCounter = _cmdCounter + 1
        else
            LOG("[LLMBotUI] poll: waiting for " .. path
                .. " (stat=nil, import_err=" .. tostring(err) .. ")")
        end
    end
end

-- ---------------------------------------------------------------------------
-- Drive chat display + command-file polling from a UI thread.
-- This FAF build's base gamemain.lua has NO OnSync and NO GameMain global, so
-- those hooks never fired. CreateUI(isReplay) IS the game-UI entry point and
-- ForkThread works there, so we start the poll loop from inside CreateUI.
-- AddBeatFunction (per-beat UI callback) is the fallback.
-- ---------------------------------------------------------------------------
local function LLMPollTick()
    CheckSyncForBotChat()
    PollCommandFile()
end

local function StartPollThread(source)
    ForkThread(function()
        LOG("[LLMBotUI] poll thread started (" .. tostring(source) .. ")")
        while true do
            WaitSeconds(0.3)
            LLMPollTick()
        end
    end)
end

local _origCreateUI = rawget(_G, "CreateUI")
if _origCreateUI then
    CreateUI = function(isReplay)
        _origCreateUI(isReplay)
        pcall(function()
            import("/mods/supcom-llm-ai-bot/lua/UI/LLMOverlay.lua").Init(false)
        end)
        pcall(StartPollThread, "CreateUI")
    end
    LOG("[LLMBotUI] CreateUI hooked")
else
    -- No CreateUI global: try the per-beat callback, else a load-time thread.
    local _addBeat = rawget(_G, "AddBeatFunction")
    if _addBeat then
        _addBeat(LLMPollTick)
        LOG("[LLMBotUI] poll via AddBeatFunction (no CreateUI)")
    else
        pcall(StartPollThread, "load-fallback")
    end
end
