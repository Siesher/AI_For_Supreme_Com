-- Hook: LLM AI Bot — gamemain.lua UI extension
--
-- Appended to FAF's /lua/ui/game/gamemain.lua by the hook system.
-- Responsibilities:
--   1. Display bot chat messages pushed from sim via Sync.LLMBotChat
--   2. Initialise LLMOverlay (Ctrl+Shift+D debug panel)
--
-- Sync.LLMBotChat = { text = "...", from = "AI Bot", id = <number> }
-- The `id` field is a monotone counter; we track the last-seen id to
-- avoid showing the same message twice.

LOG("[LLMBotUI] gamemain hook loading")

local _botChatLastId = 0

-- ---------------------------------------------------------------------------
-- DisplayBotChat — injects a message into the game chat log
-- ---------------------------------------------------------------------------
local function DisplayBotChat(text, from)
    if not text or text == "" then return end
    LOG("[LLMBotUI] " .. (from or "AI Bot") .. ": " .. text)

    -- Try FAF's game chat module (function name varies across FAF versions)
    pcall(function()
        local chatMod = import("/lua/ui/game/chat.lua")
        if not chatMod then return end

        local msg = {
            sender  = from or "AI Bot",
            text    = text,
            color   = "FF00FF99",  -- teal — distinguishes bot from player chat
        }

        -- Different FAF versions expose different function names
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

-- ---------------------------------------------------------------------------
-- Hook OnSync if it is defined in gamemain.lua — called each sim tick
-- Use rawget to bypass FAF's strict global checker.
-- ---------------------------------------------------------------------------
local _origOnSync = rawget(_G, "OnSync")
if _origOnSync then
    OnSync = function(syncTable)
        _origOnSync(syncTable)
        if syncTable then
            local chatData = rawget(syncTable, "LLMBotChat")
            if chatData and type(chatData) == "table" then
                local id = chatData.id or 0
                if id ~= _botChatLastId then
                    _botChatLastId = id
                    DisplayBotChat(chatData.text, chatData.from)
                end
            end
        end
    end
    LOG("[LLMBotUI] OnSync hooked")
end

-- ---------------------------------------------------------------------------
-- Hook GameMain — start polling thread and init overlay.
-- Use rawget to bypass FAF's strict global checker.
-- ---------------------------------------------------------------------------
local _origGameMain = rawget(_G, "GameMain")
if _origGameMain then
    GameMain = function(ui)
        _origGameMain(ui)

        -- Initialise the debug overlay (Ctrl+Shift+D)
        pcall(function()
            import("/mods/supcom-llm-ai-bot/lua/UI/LLMOverlay.lua").Init(false)
        end)

        -- Polling fallback: if OnSync was not available we still catch messages
        ForkThread(function()
            while true do
                WaitSeconds(0.5)
                CheckSyncForBotChat()
            end
        end)

        LOG("[LLMBotUI] GameMain hook complete")
    end
    LOG("[LLMBotUI] GameMain hooked")
else
    -- GameMain not a global (local function in this FAF version) —
    -- rely solely on the OnSync hook above for chat display.
    LOG("[LLMBotUI] GameMain not found as global — OnSync-only mode")
end
