-- Hook: add LLM AI Bot to the lobby AI type dropdown.
-- This file runs AFTER the base lua/ui/lobby/aitypes.lua in the same environment,
-- so GetAItypes and the aitypes table are already defined here.

LOG('*LLMAIBot* aitypes hook loading...')

-- Wrap GetAItypes so every future call includes our entry.
local _base_GetAItypes = GetAItypes

function GetAItypes()
    local types = _base_GetAItypes()
    table.insert(types, {
        key    = 'LLM_AI_UEF',
        name   = 'LLM AI Bot (UEF)',
        rating = 1000,
    })
    LOG('*LLMAIBot* injected into aitypes, total=' .. table.getn(types))
    return types
end

-- Also patch the backward-compat global table that was built at module load time.
table.insert(aitypes, {
    key    = 'LLM_AI_UEF',
    name   = 'LLM AI Bot (UEF)',
    rating = 1000,
})

LOG('*LLMAIBot* aitypes hook done')
