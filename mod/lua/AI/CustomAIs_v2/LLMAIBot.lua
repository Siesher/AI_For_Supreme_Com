-- CustomAIs_v2 registration for LLM AI Bot
-- FAF reads this file via GetAItypes() in lua/ui/lobby/aitypes.lua.
-- The AI table must have AIList and optionally CheatAIList.

LOG('*LLMAIBot* CustomAIs_v2 loading...')

AI = {
    AIList = {
        {
            key    = 'LLM_AI_UEF',
            name   = 'LLM AI Bot (UEF)',
            rating = 1000,
        },
    },
    CheatAIList = {},
}

LOG('*LLMAIBot* AI table registered, key=LLM_AI_UEF')
