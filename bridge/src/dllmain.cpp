// T009: DLL entry point for supcom_llm_bridge.dll
//
// DllMain — lifecycle:
//   DLL_PROCESS_ATTACH → start named-pipe background thread
//   DLL_PROCESS_DETACH → stop background thread (blocks until clean exit)
//
// luaopen_llm_bridge — exported symbol defined in lua_bridge.cpp,
//   called by Lua require("llm_bridge") to register LLMBridge.* globals.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "pipe_client.h"
#include "lua_injector.h"

// ---------------------------------------------------------------------------
// DllMain — Windows DLL lifecycle hook
// ---------------------------------------------------------------------------
BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID /*lpvReserved*/) {
    switch (fdwReason) {
        case DLL_PROCESS_ATTACH:
            // Disable per-thread DLL_THREAD_ATTACH/DETACH notifications for
            // performance — we don't need them.
            DisableThreadLibraryCalls(hinstDLL);
            // Start the background named-pipe thread that connects to Python.
            PipeClient_Start();
            LuaInjector_Start();
            break;

        case DLL_PROCESS_DETACH:
            // Stop the background thread cleanly before the DLL unloads.
            // Safe to block here — the game is already shutting down.
            PipeClient_Stop();
            break;

        default:
            break;
    }
    return TRUE;
}
// Note: luaopen_llm_bridge is defined in lua_bridge.cpp and exported from
// there. No need to re-declare it here; the linker merges both .obj files.
