-- T027: Chat Handler — mod/lua/AI/ChatHandler.lua
--
-- ReceiveThread: hooks SimCallbacks.ChatMessage to capture player messages.
-- SendThread: polls LLMBridge.Receive() for chat-type messages and sends them.
--
-- Rolling buffer: max 10 messages.
-- Expose GetPendingMessages() for GameStateCollector.

local LLMBridge = import("/mods/supcom-llm-ai-bot/lua/AI/LLMBridge.lua")

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local MAX_BUFFER = 10

-- Player messages waiting to be sent to LLM
local _pending_player_messages = {}

-- Bot messages queued for sending to game chat
local _pending_bot_messages = {}

-- ---------------------------------------------------------------------------
-- Chat receive callback (deferred — SimCallbacks may not exist at import time)
-- ---------------------------------------------------------------------------

local _hooked = false

--- Install the SimCallbacks.ChatMessage hook.  Must be called from a thread
--- (not at module scope) because SimCallbacks is only available after the sim
--- layer finishes initialisation.
function EnsureHooked()
    if _hooked then return end
    local ok = pcall(function()
        local sc = rawget(_G, "SimCallbacks")
        if sc then
            local original = sc.ChatMessage
            sc.ChatMessage = function(data)
                if original then original(data) end
                if not data or not data.Msg then return end
                local msg    = data.Msg
                local sender = msg.sender or ""
                local text   = msg.text   or ""
                if sender ~= "" and text ~= "" then
                    table.insert(_pending_player_messages, text)
                    while table.getn(_pending_player_messages) > MAX_BUFFER do
                        table.remove(_pending_player_messages, 1)
                    end
                end
            end
            _hooked = true
            LOG("[ChatHandler] SimCallbacks.ChatMessage hooked successfully")
        end
    end)
    if not ok then
        LOG("[ChatHandler] SimCallbacks hook failed (will retry)")
    end
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

---Return (and clear) pending player messages for the LLM context.
---@return string[]
function GetPendingMessages()
    local msgs = _pending_player_messages
    _pending_player_messages = {}
    return msgs
end

---Queue a bot message for sending to game chat (called by LLMAIBrain).
---@param text string
function QueueBotMessage(text)
    if text and text ~= "" then
        table.insert(_pending_bot_messages, text)
    end
end

-- ---------------------------------------------------------------------------
-- T029: SendThread — drain bot message queue and send to game chat
-- ---------------------------------------------------------------------------

function SendThread(brain)
    while not brain.Dead do
        WaitSeconds(0.5)

        -- Drain pending bot messages
        while table.getn(_pending_bot_messages) > 0 do
            local text = table.remove(_pending_bot_messages, 1)
            -- FAF chat send API
            -- SessionSync.BroadcastChatToTeam posts to team chat;
            -- fall back to LOG if not available in sim context.
            local ok = pcall(function()
                -- In FAF, the sim layer cannot directly call UI functions.
                -- The correct path is through a UserSync event or SyncTable.
                -- For now, log the message; full implementation wires UserSync in T029.
                if GetFocusArmy and GetFocusArmy() == brain:GetArmyIndex() then
                    -- Observer / spectator view: just log
                    LOG("[ChatHandler] BOT CHAT: " .. text)
                else
                    LOG("[ChatHandler] BOT CHAT (pending UI wiring): " .. text)
                end
            end)
            if not ok then
                LOG("[ChatHandler] SendThread error sending: " .. tostring(text))
            end
        end
    end
end

-- ---------------------------------------------------------------------------
-- T029: Check LLMBridge.Receive() for chat-type envelopes
-- Called from the main chat handling loop.
-- ---------------------------------------------------------------------------

function PollBotChat()
    -- The bridge_server wraps chat messages as {"type":"chat","text":"..."}
    -- but in the current architecture the StrategicDecision's chat_message field
    -- is read by LLMAIBrain and passed to SendChat(). This function handles
    -- any raw chat-type envelopes that arrive separately.
    -- (This path is used if bridge_server decides to send chat independent of decisions.)
    if not LLMBridge.IsConnected() then return end
    local raw = LLMBridge.PollCommand and LLMBridge.PollCommand()
    if raw and raw.type == "chat" and raw.text then
        QueueBotMessage(raw.text)
    end
end
