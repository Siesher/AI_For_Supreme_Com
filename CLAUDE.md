# SupCom LLM AI Bot — Development Guide

**Branch**: `001-supcom-llm-ai-bot`
**Last updated**: 2026-02-18

---

## Architecture Overview

Three-layer response system for the ally-mode bot:

| Layer | Mechanism | Latency | Trigger |
|-------|-----------|---------|---------|
| 1 — Reflex | Pure Lua (AllyMonitor + ReflexLayer) | < 1s | Air/ground threat, army loss, mass stall |
| 2 — Fast LLM | Qwen3.5-4B via Ollama | ~3-5s | Ally events, periodic |
| 3 — Deep LLM | Qwen3.5-9B via Ollama | ~7-10s | army_lost, phase_change, complex chat |

**IPC**: Windows Named Pipe `\\.\pipe\supcom_llm_bridge` (4-byte LE length-prefix framing)

---

## LLM Models

| Tag | Size | VRAM | Use |
|-----|------|------|-----|
| `qwen3.5:9b` | Q4_K_M | ~5.5 GB | Deep strategic decisions |
| `qwen3.5:4b` | Q4_K_M | ~2.5 GB | Fast/routine/ally |

**Critical**: Every system prompt MUST begin with `/no_think` to disable Qwen3.5 chain-of-thought tokens.

---

## Poll Intervals

| Timer | Model | Interval |
|-------|-------|----------|
| Fast baseline | 4B | 20s (`poll_interval_fast_s`) |
| Deep scan | 8B | 60s (`poll_interval_deep_s`) |

---

## Reflex Triggers (AllyMonitor → ReflexLayer)

| Condition | Action | Cooldown |
|-----------|--------|---------|
| `air_threat_near_base > 15` | SCRAMBLE_INTERCEPTORS | 20 ticks (normal) |
| `base_threat > 30` | SEND_GROUND_SUPPORT | 20 ticks |
| `losing_army_fast` (< 60% of prev) | SEND_ARMY_SUPPORT | 20 ticks |
| ally mass stall + own mass > 300 | SHARE_MASS | 20 ticks |

Cooldown by difficulty: easy=40 ticks, normal=20, hard=5.

---

## File Structure

```
AI_For_Supreme_Com/
├── bridge/
│   ├── src/
│   │   ├── ring_buffer.h       SPSC lock-free queue (T006)
│   │   ├── pipe_client.h/cpp   Named Pipe background thread (T007)
│   │   ├── lua_bridge.cpp      Lua function registration (T008)
│   │   └── dllmain.cpp         DLL entry point (T009)
│   ├── build/                  Output: supcom_llm_bridge.dll
│   └── CMakeLists.txt          x64 Windows DLL, C++17
├── mod/
│   ├── mod_info.lua            FAF mod descriptor (T010)
│   ├── hook/lua/aibrains/
│   │   └── index.lua           AI brain registration hook (T011)
│   └── lua/
│       ├── AI/
│       │   ├── LLMAIBrain.lua          Main brain + all threads (T012/T025/T026)
│       │   ├── GameStateCollector.lua  Snapshot assembler (T015)
│       │   ├── LLMBridge.lua           DLL wrapper (T016)
│       │   ├── BuildOrderUEF.lua       Build templates (T021)
│       │   ├── EconomyManager.lua      Economy/engineer AI (T022)
│       │   ├── StrategyExecutor.lua    Decision → game commands (T023)
│       │   ├── TacticalMicro.lua       Unit micro coroutine (T024)
│       │   ├── ChatHandler.lua         Chat receive/send (T027/T029)
│       │   ├── AllyMonitor.lua         Ally state reader (T032)
│       │   ├── ReflexLayer.lua         Instant reflex actions (T033)
│       │   └── TerritoryManager.lua    Build zone restriction (T038)
│       └── UI/
│           └── LLMOverlay.lua          Debug overlay Ctrl+Shift+D (T050)
├── server/
│   ├── bridge_server.py        asyncio entry point (T013)
│   ├── pipe_server.py          Windows Named Pipe server (T014)
│   ├── state_processor.py      Prompt builder (T017/T028)
│   ├── llm_client.py           Ollama HTTP client (T018)
│   ├── command_builder.py      Response validator (T019)
│   ├── fallback_strategy.py    Rule-based fallback (T020)
│   ├── llm_router.py           Model selection (T035/T036)
│   ├── config.py               Config loader/validator (T041)
│   ├── decision_logger.py      JSONL decision log (T048)
│   └── save_state.py           Save/load bot state (T049)
├── installer/
│   ├── config.json             Default configuration
│   ├── install.ps1             Guided installer (T042/T043)
│   └── start_bridge.ps1        Bridge launcher (T044)
└── tests/
    └── integration/
        └── test_pipe_roundtrip.py  Smoke tests (T054)
```

---

## Build DLL

**Toolchain requirement**: MinGW GCC (i686, 32-bit) — **not MSVC**.
SupCom:FA is a 32-bit process. `LuaAPI.h` uses GCC `asm("0x...")` syntax to bind
Lua functions to absolute game addresses; MSVC does not support this attribute.

### Install MinGW via MSYS2 (recommended)

```powershell
# 1. Download and install MSYS2 from https://www.msys2.org/
# 2. In MSYS2 MinGW32 shell:
pacman -S mingw-w64-i686-gcc mingw-w64-i686-cmake mingw-w64-i686-make
```

### Build

```bash
# In MSYS2 MinGW32 shell, from repo root:
cd bridge
python gen_link_script.py        # regenerates link.ld (already committed)
cmake -B build_cmake -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build_cmake
# Output: bridge/build/supcom_llm_bridge.dll
```

### What's in bridge/src/

| File | Source |
|------|--------|
| `LuaAPI.h` | FAForever/FA-Binary-Patches (auto-downloaded, committed) |
| `global.h` | FAForever/FA-Binary-Patches (auto-downloaded, committed) |
| `lua/lua.h` | FAForever/FA-Binary-Patches (auto-downloaded, committed) |
| `PatchBase.h` | Macro stubs (committed) |
| `../workflow.cpp` | Empty stub required by global.h (committed) |
| `../link.ld` | Generated linker script mapping `asm("0x...")` to addresses |

### How the linker script works

`LuaAPI.h` declares e.g.:
```cpp
void lua_pushstring(lua_State*, const char*) asm("0x90cdf0");
```
GCC emits an external reference to symbol `"0x90cdf0"`. The linker script
provides: `PROVIDE("0x90cdf0" = 0x90cdf0);` which resolves it to that absolute
address in SupCom's process space (no ASLR on this 2007 game).

---

## Run Bridge Server

```powershell
# Start Ollama first, then:
python server/bridge_server.py --config installer/config.json

# Or use the guided launcher:
powershell -File installer/start_bridge.ps1
```

---

## Key Paths

| Item | Path |
|------|------|
| Pipe name | `\\.\pipe\supcom_llm_bridge` |
| Decision log | `%ProgramData%\FAForever\logs\llm_ai_decisions.log` |
| Save state | `%APPDATA%\FAForever\saves\llm_bot_state_{name}.json` |
| FAF mod install | `%APPDATA%\FAForever\mods\supcom-llm-ai-bot\` |

---

## Development Notes

- All pipe messages: 4-byte LE uint32 length prefix + UTF-8 JSON payload
- DLL compiled as i686 (32-bit) Release — SupCom:FA is a 32-bit process; x64 DLL will not load
- FAF mod hooks append to existing files — never overwrite base files
- `WaitTicks(N)` in Lua sim = N × 100ms (10 ticks/second)
- `WaitSeconds(N)` in Lua sim = N seconds game time
- Decision log is the primary debugging tool — review after every test game
- Qwen3.5 `/no_think` is **mandatory** — without it, responses include slow CoT tokens before JSON
- After 3 consecutive 8B timeouts, all traffic auto-routes to 4B for the session
