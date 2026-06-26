-- Hook: forward player chat messages to SIM layer for LLM AI Bot.
-- The base ReceiveChat is called by the engine when a chat message arrives.
-- We wrap it to also send a SimCallback so the SIM-layer brain can read it.

local _base_ReceiveChat = ReceiveChat

function ReceiveChat(sender, msg)
    -- Call original handler
    if _base_ReceiveChat then
        _base_ReceiveChat(sender, msg)
    end

    -- Only forward HUMAN player messages to SIM — skip AI messages
    -- to prevent infinite feedback loop (bot reads its own reply).
    if msg and msg.text and sender
       and not msg.aisender        -- AI chat from SyncAIChat
       and not msg.ConsoleOutput   -- console/system messages
    then
        pcall(function()
            SimCallback({
                Func = 'LLMPlayerChat',
                Args = {
                    text   = tostring(msg.text),
                    sender = tostring(sender),
                },
            })
        end)
    end
end
