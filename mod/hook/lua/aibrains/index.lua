-- Hook: register LLM AI Bot brain in keyToBrain.
-- Runs in the SIM layer (sim mod, ui_only = false).
-- Called when lua/aibrains/index.lua is imported by OnCreateArmyBrain().

LOG('*LLMAIBot* aibrains/index.lua hook loading')

keyToBrain['LLM_AI_UEF'] = { '/mods/supcom-llm-ai-bot/lua/AI/LLMAIBrain.lua', 'LLMAIBrain' }

LOG('*LLMAIBot* registered LLM_AI_UEF brain')
