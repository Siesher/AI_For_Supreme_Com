# SupCom LLM AI Bot

> Local LLM-powered AI opponent & ally for **Supreme Commander: Forged Alliance** via [FAForever](https://www.faforever.com/).

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Lua 5.0](https://img.shields.io/badge/lua-5.0_(SupCom)-yellow.svg)]()
[![C++17](https://img.shields.io/badge/C%2B%2B-17_(i686)-red.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Overview

A three-layer AI system that runs entirely on your local machine — no cloud APIs, no internet required during gameplay. The bot plays as a UEF ally, making strategic decisions through a combination of instant reflexes and LLM reasoning.

| Layer | Mechanism | Latency | Trigger |
|-------|-----------|---------|---------|
| **Reflex** | Pure Lua rules (AllyMonitor + ReflexLayer) | < 1s | Immediate threats |
| **Fast LLM** | Qwen3.5-4B via Ollama | ~3-5s | Periodic / ally events |
| **Deep LLM** | Qwen3.5-9B via Ollama | ~7-10s | Strategic decisions |

### How It Works

```
  SupCom:FA (32-bit)              Python Bridge Server
  +-----------------+             +-------------------+
  | Lua AI Brain    |   Named     | State Processor   |
  | GameState ------+---Pipe----->| Prompt Builder    |
  | Collector       |   (IPC)     | LLM Router ------>| Ollama (local)
  |                 |             |   4B / 9B         |
  | Strategy  <-----+---Pipe-----| Command Builder   |
  | Executor        |             | Fallback Strategy |
  +-----------------+             +-------------------+
```

**IPC**: Windows Named Pipe `\\.\pipe\supcom_llm_bridge` with 4-byte LE length-prefix framing.

---

## Project Structure

```
AI_For_Supreme_Com/
├── bridge/                    # C++ DLL — game ↔ Python bridge
│   ├── src/
│   │   ├── pipe_client.cpp    # Named Pipe client (pure Win32 API)
│   │   ├── lua_bridge.cpp     # LLMBridge.* Lua function registration
│   │   ├── lua_injector.cpp   # Thread-safe Lua VM hook injection
│   │   ├── lua_api_direct.h   # Direct fn-ptr calls to FA addresses
│   │   └── dllmain.cpp        # DLL entry point
│   ├── inject.c               # 32-bit DLL injector (CreateRemoteThread)
│   └── CMakeLists.txt         # MinGW GCC i686 build
│
├── server/                    # Python asyncio bridge server
│   ├── bridge_server.py       # Entry point — pipe server + process watcher
│   ├── pipe_server.py         # Named Pipe server (async)
│   ├── state_processor.py     # Game state → LLM prompt builder
│   ├── llm_client.py          # Ollama HTTP client (think: false)
│   ├── llm_router.py          # Model selection (4B fast / 9B deep)
│   ├── command_builder.py     # LLM response → validated commands
│   ├── fallback_strategy.py   # Rule-based fallback when LLM unavailable
│   ├── decision_logger.py     # JSONL decision audit log
│   └── config.py              # Configuration loader
│
├── mod/                       # FAF SIM mod (game logic)
│   ├── mod_info.lua           # Mod descriptor
│   ├── hook/lua/
│   │   ├── aibrains/index.lua # AI brain registration
│   │   └── ui/lobby/aitypes.lua
│   └── lua/AI/
│       ├── LLMAIBrain.lua     # Main brain — threads + decision loop
│       ├── GameStateCollector.lua  # Snapshot assembler
│       ├── LLMBridge.lua      # DLL wrapper with offline fallback
│       ├── BuildOrderUEF.lua  # Build order templates
│       ├── EconomyManager.lua # Economy / engineer management
│       ├── StrategyExecutor.lua   # Decision → game commands
│       ├── TacticalMicro.lua  # Unit micro coroutine
│       ├── ChatHandler.lua    # In-game chat integration
│       ├── AllyMonitor.lua    # Ally state reader
│       ├── ReflexLayer.lua    # Instant reflex actions
│       ├── TerritoryManager.lua   # Build zone restriction
│       └── JSON.lua           # Lua 5.0 JSON encoder/decoder
│
├── mod-ui/                    # FAF UI mod (lobby dropdown)
│   ├── mod_info.lua
│   └── hook/lua/ui/lobby/aitypes.lua
│
├── installer/                 # User-facing setup
│   ├── config.json            # Default configuration
│   ├── install.ps1            # Guided installer
│   └── start_bridge.ps1       # Bridge launcher
│
├── tests/                     # Test suite
│   ├── unit/                  # Unit tests (state_processor, router, etc.)
│   └── integration/           # Pipe roundtrip smoke tests
│
└── specs/                     # Design documents
    └── 001-supcom-llm-ai-bot/
        ├── spec.md            # Feature specification
        ├── plan.md            # Implementation plan
        ├── tasks.md           # Task breakdown
        ├── contracts/         # IPC protocol contracts
        └── data-model.md     # Data model reference
```

---

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| **Python** | 3.12+ | Bridge server |
| **Ollama** | latest | Local LLM inference |
| **MSYS2 MinGW32** | GCC i686 | DLL compilation (SupCom is 32-bit) |
| **FAForever** | latest | Game client & mod platform |
| **SupCom: Forged Alliance** | Steam | Base game |
| **VRAM** | 6+ GB | For Qwen3.5-9B (Q4_K_M) |

### LLM Models

| Tag | Size | VRAM | Purpose |
|-----|------|------|---------|
| `qwen3.5:9b` | Q4_K_M | ~5.5 GB | Deep strategic reasoning |
| `qwen3.5:4b` | Q4_K_M | ~2.5 GB | Fast routine decisions |

---

## Quick Start

### 1. Install Models

```bash
ollama pull qwen3.5:9b
ollama pull qwen3.5:4b
```

### 2. Build the DLL

```bash
# In MSYS2 MinGW32 shell:
cd bridge
cmake -B build_cmake -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build_cmake
# Output: bridge/build/supcom_llm_bridge.dll
```

### 3. Install the Mod

```powershell
# Run the guided installer:
powershell -File installer/install.ps1

# Or manually copy:
Copy-Item -Recurse mod/* "C:\FAFData\mods\supcom-llm-ai-bot\"
Copy-Item -Recurse mod-ui/* "C:\FAFData\mods\supcom-llm-ai-bot-ui\"
Copy-Item bridge/build/supcom_llm_bridge.dll "C:\ProgramData\FAForever\bin\llm_bridge.dll"
Copy-Item bridge/build/inject.exe "C:\ProgramData\FAForever\bin\inject.exe"
```

### 4. Run

```powershell
# Terminal 1 — Start Ollama (if not already running):
ollama serve

# Terminal 2 — Start bridge server:
python server/bridge_server.py --config installer/config.json

# Terminal 3 — Launch FAF, enable both mods, add "LLM AI Bot (UEF)", play!
```

---

## Architecture Details

### Reflex Layer (< 1s response)

Pure Lua — no LLM involved. Handles emergencies:

| Condition | Action | Cooldown |
|-----------|--------|----------|
| Air threat near base > 15 | Scramble interceptors | 20 ticks |
| Base threat > 30 | Send ground support | 20 ticks |
| Army losing fast (< 60%) | Send army support | 20 ticks |
| Ally mass stall + own > 300 | Share mass | 20 ticks |

### LLM Decision Pipeline

1. **GameStateCollector** assembles a snapshot (resources, units, threats, map control)
2. **StateProcessor** builds a structured prompt with game context
3. **LLMRouter** selects 4B or 9B model based on trigger severity
4. **LLMClient** queries Ollama with `think: false` (no chain-of-thought overhead)
5. **CommandBuilder** validates JSON response against allowed actions
6. **StrategyExecutor** translates decisions into game commands

### DLL Bridge

The C++ DLL bridges SupCom's 32-bit Lua 5.0 VM and the Python server:

- **No external dependencies** — only KERNEL32.dll and msvcrt.dll
- **Thread-safe injection** via `lua_sethook` (hook fires in FA's Lua thread)
- **Lock-free IPC** — `InterlockedExchange`-based single-slot queues
- **Direct function pointers** to FA's Lua C API (no linker script needed)

### Fallback Strategy

When the LLM is unavailable (Ollama down, 3+ timeouts), the bot falls back to rule-based decisions covering expansion, defense, and basic army management.

---

## Development

### Run Tests

```bash
uv run pytest -v
```

### Lint & Format

```bash
uv run ruff check .
uv run ruff format .
```

### Debug

- **Decision log**: `%ProgramData%\FAForever\logs\llm_ai_decisions.log`
- **Injection log**: `%ProgramData%\FAForever\logs\llm_inject.log`
- **Game log**: `%APPDATA%\Forged Alliance Forever\logs\game_*.log`
- **In-game overlay**: `Ctrl+Shift+D` (when UI mod is active)

---

## Key Constraints

- **Lua 5.0** — no `#`, no `goto`, use `table.getn()` and `table.insert()`
- **32-bit DLL** — must be compiled with MinGW GCC i686, not MSVC
- **ASCII paths** — FAF vault path must avoid Cyrillic and OneDrive reparse points
- **No ASLR** — FA (2007) uses fixed addresses; DLL calls FA functions via absolute pointers
- **`/no_think` deprecated** — use Ollama API `"think": false` for Qwen3.5

---

## License

MIT
