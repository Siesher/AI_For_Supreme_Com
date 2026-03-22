// lua_api_direct.h — Direct function-pointer wrappers for FA's Lua C API.
//
// Replaces the asm("0x...") + linker-script approach used in LuaAPI.h.
// Each wrapper casts an absolute FA process address to a typed function
// pointer and calls through it.  This produces code like:
//
//     mov  eax, 0x90cdf0
//     call eax
//
// No external symbol references → no linker script needed → clean PE DLL
// that LoadLibraryA can load successfully.
//
// FA uses no ASLR, so absolute addresses are stable across sessions.
// Our DLL's own relocations only cover pointers to OUR data, not these
// FA constants, so the DLL loads and relocates correctly.

#pragma once
#include <stddef.h>

// ---------------------------------------------------------------------------
// Minimal type definitions (subset of LuaAPI.h needed by lua_bridge.cpp)
// ---------------------------------------------------------------------------

struct lua_State;

typedef struct luaL_reg {
    const char*   name;
    int (*func)(struct lua_State*);
} luaL_reg;

#define LUA_GLOBALSINDEX  (-10001)

// ---------------------------------------------------------------------------
// Direct wrappers — address comes from LuaAPI.h asm("0x...") declarations
// ---------------------------------------------------------------------------

static inline const char* luaL_checklstring(
    struct lua_State* L, int narg, unsigned int* len)
{
    typedef const char* (*fn_t)(struct lua_State*, int, unsigned int*);
    return ((fn_t)0x90eaa0)(L, narg, len);
}

static inline void luaL_openlib(
    struct lua_State* L, const char* libname,
    const luaL_reg* funcs, int nup)
{
    typedef void (*fn_t)(struct lua_State*, const char*, const luaL_reg*, int);
    ((fn_t)0x90de00)(L, libname, funcs, nup);
}

static inline void lua_pushboolean(struct lua_State* L, int b)
{
    typedef void (*fn_t)(struct lua_State*, int);
    ((fn_t)0x90cf80)(L, b);
}

static inline void lua_pushlstring(
    struct lua_State* L, const char* s, unsigned int len)
{
    typedef void (*fn_t)(struct lua_State*, const char*, unsigned int);
    ((fn_t)0x90cd80)(L, s, len);
}

static inline void lua_pushnil(struct lua_State* L)
{
    typedef void (*fn_t)(struct lua_State*);
    ((fn_t)0x90cd00)(L);
}

static inline void lua_pushstring(struct lua_State* L, const char* s)
{
    typedef void (*fn_t)(struct lua_State*, const char*);
    ((fn_t)0x90cdf0)(L, s);
}

static inline void lua_settop(struct lua_State* L, int idx)
{
    typedef void (*fn_t)(struct lua_State*, int);
    ((fn_t)0x90c5a0)(L, idx);
}

// lua_pop is a macro in the original too
#define lua_pop(L, n)  lua_settop(L, -(n) - 1)

// ---------------------------------------------------------------------------
// Debug / hook API — needed for thread-safe Lua injection
// ---------------------------------------------------------------------------

struct lua_Debug {
    int event;
    const char* name;
    const char* namewhat;
    const char* what;
    const char* source;
    int currentline;
    int nups;
    int linedefined;
    char short_src[60];
    int i_ci;
};

typedef void (*lua_Hook)(struct lua_State* L, struct lua_Debug* ar);

#define LUA_HOOKCALL  0
#define LUA_HOOKRET   1
#define LUA_HOOKLINE  2
#define LUA_HOOKCOUNT 3

#define LUA_MASKCALL  (1 << LUA_HOOKCALL)
#define LUA_MASKRET   (1 << LUA_HOOKRET)
#define LUA_MASKLINE  (1 << LUA_HOOKLINE)
#define LUA_MASKCOUNT (1 << LUA_HOOKCOUNT)

static inline int lua_sethook(struct lua_State* L, lua_Hook func, int mask, int count)
{
    typedef int (*fn_t)(struct lua_State*, lua_Hook, int, int);
    return ((fn_t)0x912560)(L, func, mask, count);
}
