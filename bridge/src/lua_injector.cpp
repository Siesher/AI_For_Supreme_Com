// lua_injector.cpp — register LLMBridge.* in FA's Lua VM after DLL injection.
//
// FA keeps its global LuaPlus state at 0x10A6478:
//   g_ConsoleLuaState = GPtr(0x10A6478, LuaState*)   (from global.h)
//   LuaState::m_state (offset 0) = raw lua_State*
//
// Thread safety:
//   FA runs its Lua VM in a single OS thread.  We MUST NOT call Lua C API
//   functions from our background thread — doing so races with FA and crashes.
//   Instead, we use lua_sethook(LUA_MASKCOUNT, 1) to install a one-shot hook.
//   The hook callback fires inside FA's Lua thread on the next VM instruction,
//   where it is safe to call luaopen_llm_bridge().

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include "lua_api_direct.h"

// Defined in lua_bridge.cpp and exported from the DLL.
extern "C" int luaopen_llm_bridge(lua_State* L);

// FA address of g_ConsoleLuaState: holds a LuaState* pointer.
// LuaState::m_state (offset 0 in LuaState) is the raw lua_State*.
static constexpr uintptr_t CONSOLE_LUASTATE_PTR = 0x10A6478;

// Set to 1 once we have registered; prevents double-registration.
static volatile LONG g_registered = 0;

// ---------------------------------------------------------------------------
// Diagnostic log — writes to a file readable even if game is running.
// ---------------------------------------------------------------------------
static FILE* g_log = nullptr;
static void log_open() {
    g_log = fopen("C:\\ProgramData\\FAForever\\logs\\llm_inject.log", "w");
}
static void log_write(const char* fmt, ...) {
    if (!g_log) return;
    va_list ap; va_start(ap, fmt); vfprintf(g_log, fmt, ap); va_end(ap);
    fflush(g_log);
}

// ---------------------------------------------------------------------------
// Hook callback — runs inside FA's Lua thread (safe to call Lua C API).
// ---------------------------------------------------------------------------
static void __cdecl hook_callback(lua_State* L, lua_Debug* /*ar*/) {
    // Remove the hook immediately so it only fires once.
    lua_sethook(L, nullptr, 0, 0);

    // Guard against double-registration (shouldn't happen, but be safe).
    if (InterlockedCompareExchange(&g_registered, 1, 0) != 0) {
        log_write("hook_callback: already registered, skipping\n");
        return;
    }

    log_write("hook_callback: running luaopen_llm_bridge(L=%p) in FA thread\n", L);
    luaopen_llm_bridge(L);
    log_write("hook_callback: luaopen_llm_bridge returned OK\n");
}

// ---------------------------------------------------------------------------
// InjectionThread — background thread started by LuaInjector_Start().
// Waits for the Lua VM to exist, then installs a one-shot hook.
// ---------------------------------------------------------------------------
static DWORD WINAPI InjectionThread(LPVOID) {
    log_open();
    log_write("InjectionThread started, waiting 10s for game init...\n");
    Sleep(10000);

    for (int attempt = 0; attempt < 30; ++attempt) {
        if (g_registered) {
            log_write("attempt=%d — already registered, done\n", attempt);
            return 0;
        }

        // Step 1: read g_ConsoleLuaState = *(LuaState**)0x10A6478
        void* luaPlus = *reinterpret_cast<void**>(CONSOLE_LUASTATE_PTR);
        log_write("attempt=%d luaPlus=%p\n", attempt, luaPlus);
        if (!luaPlus) { Sleep(2000); continue; }

        // Step 2: LuaState::m_state at offset 0 is the raw lua_State*
        lua_State* L = *reinterpret_cast<lua_State**>(luaPlus);
        log_write("  lua_State* L = %p\n", L);
        if (!L) { Sleep(2000); continue; }

        // Step 3: install a one-shot count hook — fires on the next VM instruction.
        // lua_sethook is one of the few Lua C API functions safe to call from
        // another thread: it only writes hook fields in lua_State, which are
        // read by the VM loop between instructions (no allocation, no GC).
        log_write("  installing lua_sethook(LUA_MASKCOUNT, count=1)...\n");
        lua_sethook(L, hook_callback, LUA_MASKCOUNT, 1);
        log_write("  hook installed, waiting for it to fire...\n");

        // Wait for the hook to fire (up to 5 seconds).
        for (int i = 0; i < 50; ++i) {
            if (g_registered) break;
            Sleep(100);
        }

        if (g_registered) {
            log_write("  registration confirmed via hook\n");
            return 0;
        }

        // Hook didn't fire — Lua VM might not be running yet. Remove and retry.
        log_write("  hook did not fire in 5s, removing and retrying...\n");
        lua_sethook(L, nullptr, 0, 0);
        Sleep(2000);
    }

    log_write("All retries exhausted — staying offline\n");
    return 1;
}

// ---------------------------------------------------------------------------
// Public entry point — called once from DllMain(DLL_PROCESS_ATTACH).
// ---------------------------------------------------------------------------
void LuaInjector_Start() {
    HANDLE h = CreateThread(nullptr, 0, InjectionThread, nullptr, 0, nullptr);
    if (h) CloseHandle(h);
}
