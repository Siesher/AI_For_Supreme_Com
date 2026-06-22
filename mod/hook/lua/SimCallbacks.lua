-- Hook: register LLMPlayerChat SimCallback for receiving chat from UI layer.
-- The UI hook on chat.lua sends SimCallback({Func='LLMPlayerChat', Args=...})
-- whenever a player message arrives.  We store messages in a global table
-- that LLMAIBrain.ChatHandlerThread reads.

-- LLMCommand: UI layer sends LLM command (read from file) into sim
Callbacks.LLMCommand = function(data)
    if not data then return end
    -- Store for FileIPC.ReadCommand() to pick up
    rawset(_G, 'LLMPendingCommand', data)
end

Callbacks.LLMPlayerChat = function(data)
    if not data or not data.text then return end
    local pending = rawget(_G, 'LLMPendingPlayerChat')
    if not pending then
        pending = {}
        rawset(_G, 'LLMPendingPlayerChat', pending)
    end
    table.insert(pending, {
        text   = data.text,
        sender = data.sender or "Unknown",
    })
end
