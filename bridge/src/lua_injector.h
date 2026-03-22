#pragma once

// LuaInjector_Start() — spawns a background thread that:
//   1. Reads g_ConsoleLuaState (FA global at 0x10A6478) to find lua_State*.
//   2. Installs a one-shot lua_sethook so registration runs inside FA's Lua thread.
//   3. In the hook, calls luaopen_llm_bridge(L) to register LLMBridge.* as a global.
//
// Called once from DllMain(DLL_PROCESS_ATTACH).

void LuaInjector_Start();
