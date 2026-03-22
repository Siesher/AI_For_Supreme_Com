// T008: Lua ↔ DLL bridge — registers LLMBridge.* functions into the Lua VM.
// Conforms to contract: contracts/lua-dll-bridge.md
//
// SupCom:FA uses LuaPlus (Lua 5.0 ABI). lua_api_direct.h declares all Lua
// functions as direct function-pointer calls into FA's fixed process addresses.
// Building requires MinGW GCC (i686, 32-bit).

#include "pipe_client.h"
#include "lua_api_direct.h"

// Receive buffer: static (Lua sim is single-threaded, l_Receive never re-enters)
static char s_recv_buf[128 * 1024];

// ---------------------------------------------------------------------------
// LLMBridge.Send(json_string)
//   Enqueues a game-state snapshot for the Python server. Non-blocking.
//   Returns: nil
// ---------------------------------------------------------------------------
static int l_Send(lua_State* L)
{
    unsigned int len = 0;
    const char* s = luaL_checklstring(L, 1, &len);
    if (s && len > 0) {
        PipeClient_Send(s, len);
    }
    return 0;
}

// ---------------------------------------------------------------------------
// LLMBridge.Receive()
//   Returns the latest StrategicDecision JSON string from the Python server,
//   or nil if the queue is empty.
// ---------------------------------------------------------------------------
static int l_Receive(lua_State* L)
{
    unsigned int len = 0;
    if (PipeClient_TryReceive(s_recv_buf, sizeof(s_recv_buf), &len)) {
        lua_pushlstring(L, s_recv_buf, (unsigned int)len);
    } else {
        lua_pushnil(L);
    }
    return 1;
}

// ---------------------------------------------------------------------------
// LLMBridge.IsConnected()
//   Returns boolean: true when the named pipe is connected to the Python server.
// ---------------------------------------------------------------------------
static int l_IsConnected(lua_State* L)
{
    lua_pushboolean(L, PipeClient_IsConnected() ? 1 : 0);
    return 1;
}

// ---------------------------------------------------------------------------
// LLMBridge.GetStatus()
//   Returns one of: "connected" | "connecting" | "disconnected" | "error"
// ---------------------------------------------------------------------------
static int l_GetStatus(lua_State* L)
{
    lua_pushstring(L, PipeClient_GetStatus());
    return 1;
}

// ---------------------------------------------------------------------------
// Function table — uses luaL_reg (lowercase) matching Lua 5.0/LuaPlus ABI.
// ---------------------------------------------------------------------------
static const luaL_reg llm_bridge_funcs[] = {
    {"Send",        l_Send},
    {"Receive",     l_Receive},
    {"IsConnected", l_IsConnected},
    {"GetStatus",   l_GetStatus},
    {0,             0}
};

// ---------------------------------------------------------------------------
// luaopen_llm_bridge — called by LuaInjector after DLL injection, or by
// SupCom when the mod does loadlib("llm_bridge", "luaopen_llm_bridge").
//
// luaL_openlib (Lua 5.0) creates _G["LLMBridge"], registers all functions
// into it, and leaves the table on the stack.  We pop it so the stack is
// balanced (return 0 means no value is left for the caller).
// ---------------------------------------------------------------------------
extern "C" __declspec(dllexport) int luaopen_llm_bridge(lua_State* L)
{
    luaL_openlib(L, "LLMBridge", llm_bridge_funcs, 0);
    lua_pop(L, 1);
    return 0;
}
