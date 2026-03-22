# Implementation Plan: SupCom:FA LLM AI Bot

**Branch**: `001-supcom-llm-ai-bot` | **Date**: 2026-02-18 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-supcom-llm-ai-bot/spec.md`

---

## Summary

Build a locally-running AI bot for Supreme Commander: Forged Alliance (FAF) that uses Qwen3-8B (primary) and Qwen3-4B-Instruct-2507 (speed fallback) via Ollama for high-level strategic decision-making and natural language chat, while Lua scripts handle real-time tactical unit control. The system consists of three components: a FAF Lua AI mod, a C++ DLL bridge (Windows Named Pipe IPC), and a Python asyncio server. MVP targets the UEF faction in single-player and private games only.

---

## Technical Context

**Language/Version**: Lua 5.0 (FAF LuaPlus), C++17 (DLL), Python 3.11
**Primary Dependencies**: Ollama + qwen3:8b / qwen3:4b-instruct-2507 (LLM serving), pywin32 (Named Pipe), httpx (async HTTP), FAForever/fa Lua API, Win32 API (DLL)
**Storage**: JSON files for config and save state; JSONL file for decision log
**Testing**: Manual game-in-loop testing + Python unit tests (pytest) for bridge server; Lua test scripts for mod components
**Target Platform**: Windows 10/11, FAF client, game at `C:\Program Files (x86)\Supreme Commander`
**Project Type**: Multi-component (Lua mod + C++ DLL + Python server)
**Performance Goals**: LLM response < 3s on RTX 2080; sim speed ≥ +0; bridge latency < 5ms
**Constraints**: 8GB VRAM limit (7B model Q5_K_M), no internet required, single-player/private only, no blocking of sim thread
**Scale/Scope**: Single player, one concurrent game session, one AI bot instance per game

---

## Constitution Check

*No constitution.md found — proceeding without gates.*

Key design constraints enforced manually:
- **No sim thread blocking**: DLL uses background thread + lock-free queues; Lua only polls, never waits
- **No internet dependency**: All inference local via Ollama
- **Determinism preserved**: External communication is one-directional influence on AI decisions, not sim state; restricted to single-player/private games
- **Fallback required**: Rule-based Lua fallback active whenever LLM is unreachable

---

## Project Structure

### Documentation (this feature)

```text
specs/001-supcom-llm-ai-bot/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── lua-dll-bridge.md
│   └── python-llm-server.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
mod/                                  # FAF Lua AI mod
├── mod_info.lua                      # Mod metadata and FAF registration
├── hook/
│   └── lua/
│       └── aibrains/
│           └── index.lua             # Registers "LLM AI Bot (UEF)" in game AI list
└── lua/
    └── AI/
        ├── LLMAIBrain.lua            # Main AIBrain class, coroutine orchestration
        ├── LLMBridge.lua             # DLL function wrappers + polling loop
        ├── StrategyExecutor.lua      # Translates StrategicDecision → game commands
        ├── TacticalMicro.lua         # Real-time unit micro (independent of LLM)
        ├── EconomyManager.lua        # Mass/energy management heuristics
        ├── BuildOrderUEF.lua         # UEF build templates and priority queues
        ├── GameStateCollector.lua    # Assembles GameStateSnapshot JSON
        ├── ChatHandler.lua           # Chat send/receive via SimCallbacks
        ├── AllyMonitor.lua           # Continuous ally situation monitoring (every 5 ticks)
        ├── ReflexLayer.lua           # Instant Lua reactions: intercept, reinforce, share (<1 sec)
        └── FallbackAI.lua            # Rule-based fallback when LLM unavailable

bridge/                               # C++ DLL (Windows, x64)
├── src/
│   ├── dllmain.cpp                   # DLL entry, thread lifecycle
│   ├── lua_bridge.cpp                # Lua function registration (Send/Receive/IsConnected)
│   ├── pipe_client.cpp               # Named Pipe I/O + background thread
│   ├── ring_buffer.h                 # Lock-free SPSC ring buffer for queues
│   └── LuaAPI.h                      # From FAForever/FA-Binary-Patches
├── CMakeLists.txt
└── build/
    └── supcom_llm_bridge.dll         # Compiled output (x64 Release)

server/                               # Python asyncio bridge server
├── bridge_server.py                  # Main entry point + event loop
├── pipe_server.py                    # Windows Named Pipe async reader/writer
├── llm_client.py                     # Ollama async client (httpx) — 8B + 4B models
├── llm_router.py                     # Routes requests: 4B fast vs 8B deep based on complexity
├── state_processor.py                # GameStateSnapshot → LLM prompt (with ally context)
├── command_builder.py                # LLM JSON response → StrategicDecision
├── decision_logger.py                # JSONL decision log writer
├── fallback_strategy.py              # Rule-based commands when LLM unavailable
├── save_state.py                     # BotSaveState read/write for game save/load
├── config.py                         # Config loader and validator
└── requirements.txt                  # pywin32, httpx

installer/
├── install.ps1                       # Guided installation script (PowerShell)
└── config.json                       # Default user configuration

tests/
├── unit/
│   ├── test_state_processor.py
│   ├── test_command_builder.py
│   ├── test_fallback_strategy.py
│   └── test_decision_logger.py
└── integration/
    └── test_pipe_roundtrip.py        # DLL ↔ Python pipe end-to-end test
```

**Structure Decision**: Multi-component layout. Three independent components communicate via well-defined contracts. Each component can be developed and tested independently. The Lua mod is the thinnest possible layer; all complex logic lives in Python where it's easier to develop, test, and iterate.

---

## Implementation Phases

### Phase A — Foundation (P1 prerequisite)
*Goal*: Minimal working system — bot loads, connects, plays basic UEF game without LLM

1. **A1** — FAF mod skeleton: `mod_info.lua`, `index.lua`, minimal `LLMAIBrain.lua` that builds a hardcoded UEF base
2. **A2** — C++ DLL skeleton: DLL loads, registers `Send`/`Receive`/`IsConnected` as no-ops, compiles successfully
3. **A3** — Python server skeleton: `bridge_server.py` starts, opens named pipe, echoes received JSON
4. **A4** — `GameStateCollector.lua`: collect economy, unit counts, basic threat into JSON snapshot
5. **A5** — Named pipe IPC end-to-end: Lua → DLL → Python → DLL → Lua round-trip verified

### Phase B — LLM Integration (P1 core)
*Goal*: Bot queries LLM and executes returned strategy

6. **B1** — `state_processor.py`: build prompt from snapshot, inject system prompt with UEF faction context
7. **B2** — `llm_client.py`: async Ollama integration, JSON mode, timeout handling
8. **B3** — `command_builder.py`: parse and validate LLM JSON response → `StrategicDecision`
9. **B4** — `fallback_strategy.py`: rule-based commands active when LLM unavailable
10. **B5** — `StrategyExecutor.lua`: translate `StrategicDecision` into Lua build orders and attack commands
11. **B6** — `TacticalMicro.lua`: real-time platoon micro (move, attack, retreat) independent of LLM cycle
12. **B7** — `EconomyManager.lua` + `BuildOrderUEF.lua`: UEF build templates driven by strategy priorities

### Phase C — Communication (P2)
*Goal*: Natural language chat between player and bot

13. **C1** — `ChatHandler.lua`: hook into SimCallbacks to receive player chat; queue for LLM
14. **C2** — `state_processor.py` update: include player chat in prompt context
15. **C3** — Chat send-back: Python server sends `chat_message` field; Lua posts to game chat
16. **C4** — Ally coordination logic: `LLMAIBrain.lua` ally-mode — territory avoidance, resource sharing

### Phase D — Observability (P2 + P3)
*Goal*: Debug overlay + decision logging

17. **D1** — `decision_logger.py`: JSONL log with full snapshot/decision/latency per entry
18. **D2** — UI overlay Lua mod: toggle panel (Ctrl+Shift+D) showing strategy, reasoning, LLM status
19. **D3** — `save_state.py`: BotSaveState persistence on game save/load

### Phase E — Polish (P3)
*Goal*: Configuration, difficulty, installer

20. **E1** — `config.py`: load and validate `config.json`; expose difficulty/playstyle to prompt
21. **E2** — Difficulty modifiers: throttle LLM query freq, handicap economy targets for easy/hard
22. **E3** — Playstyle presets: 5 system-prompt variants (rush/balanced/turtle/air/naval)
23. **E4** — `install.ps1`: guided setup — copy mod, DLL, check Ollama, pull model, write config
24. **E5** — Map marker fallback: integrate AI-Uveso-style dynamic marker generation for maps without markers

---

## Complexity Tracking

| Decision | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| C++ DLL bridge | Only way to add IPC to SupCom's sandboxed Lua environment | File polling: 500ms+ latency, one-directional only |
| Named Pipes over TCP | Native Win32, no port management, lower latency | TCP: requires firewall rules, port conflicts |
| Separate Python server | Ollama HTTP API, async I/O, testable logic outside game process | Embedding Python in DLL: massive complexity, unstable |
| Custom AIBrain (not builder system) | Direct LLM command integration; builder priority system fights external decisions | Builder system: incompatible with dynamic LLM-driven priorities |
| Qwen3-8B Q4_K_M (primary) + Qwen3-4B-2507 Q5_K_M (speed fallback) | Qwen3-8B: best instruction following + Russian, 119 languages, fits 8GB at Q4_K_M; 4B-2507: ~70 tok/s fallback when latency matters | Qwen2.5-7B: good but inferior instruction following vs Qwen3; LFM2: no Russian; Mistral: weaker Russian |
