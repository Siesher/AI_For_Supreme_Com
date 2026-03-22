-- Hook: inject LLM AI Bot into the lobby AI dropdown.
-- Runs in the UI layer because this mod is ui_only = true.

LOG('*LLMAIBot-UI* aitypes hook loading')

local _base_GetAItypes = GetAItypes

function GetAItypes()
    local types = _base_GetAItypes()
    table.insert(types, {
        key    = 'LLM_AI_UEF',
        name   = 'LLM AI Bot (UEF)',
        rating = 1000,
    })
    LOG('*LLMAIBot-UI* injected LLM_AI_UEF, total=' .. table.getn(types))
    return types
end

-- also patch the backward-compat global built at module-load time
table.insert(aitypes, {
    key    = 'LLM_AI_UEF',
    name   = 'LLM AI Bot (UEF)',
    rating = 1000,
})

LOG('*LLMAIBot-UI* aitypes hook done')
