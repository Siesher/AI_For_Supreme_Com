# SupCom LLM AI Bot — Development Guide

**Branch**: `001-supcom-llm-ai-bot`
**Last updated**: 2026-03-29

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

## ReAct Agent Loop (002-agent-react-loop)

The LLM agent uses a multi-step ReAct (Reason + Act) loop per decision cycle:

1. **Observe** — LLM calls observation tools (get_enemy_army, get_threat_at, etc.)
2. **Decide** — LLM analyzes results, calls action tools (attack, defend, etc.)
3. **Feedback** — Action results returned to LLM for follow-up decisions
4. **Memory** — Completed cycles stored in rolling buffer, included in future prompts

**Key files**: `server/react_loop.py` (orchestrator), `server/decision_memory.py` (session memory), `mod/lua/AI/ObservationHandlers.lua` (Lua-side queries)

**Config** (`installer/config.json` → `bot`):
- `react_max_iterations`: 3 (max LLM queries per cycle)
- `react_cycle_timeout_s`: 30 (total cycle budget)
- `decision_memory_size`: 10 (rolling buffer entries)

**Pipe message types**:
- `observation_request` / `observation_result` — read-only game queries
- `action_execute` / `action_result` — action execution with feedback
- `command` — legacy batch command (still sent for backward compat)

**Observation tools**: `get_enemy_army`, `get_threat_at`, `get_mass_points`, `get_my_factories`, `get_map_control`

---

## LLM Models

| Tag | Size | VRAM | Use |
|-----|------|------|-----|
| `qwen3.5:9b` | Q4_K_M | ~5.5 GB | Deep strategic decisions |
| `qwen3.5:4b` | Q4_K_M | ~2.5 GB | Fast/routine/ally |

**Critical**: Every system prompt MUST begin with `/no_think` to disable Qwen3.5 chain-of-thought tokens.

### Inference Engine Selection (`llm.engine` in config.json)

Switch engines by changing **one key** — `llm.engine` — to a preset in `llm.engines`:

| `engine` | api_style | base_url | Quant | When to use |
|----------|-----------|----------|-------|-------------|
| `koboldcpp` (default) | openai | `:5001/v1` | GGUF Q4 | Native Windows, single .exe, best VRAM control + GBNF tool-grammar |
| `ollama` | openai | `:11434/v1` | GGUF Q4 | Incumbent, zero-install; Ollama's OpenAI endpoint |
| `ollama_native` | ollama | `:11434` | GGUF Q4 | Ollama via `/api/chat` (keeps tuned options + `keep_alive`, `kv_cache_type`) |
| `lmstudio` | openai | `:1234/v1` | GGUF Q4 | GUI one-click; `lms server start` |
| `vllm` | openai | `:8000/v1` | NVFP4/FP8 | Max throughput (WSL2 on Blackwell); experimental |
| `tabbyapi` | openai | `:5000/v1` | exl3 | ExLlamaV3; fast single-stream |

All `openai` engines go through `server/openai_client.py` (`/v1/chat/completions` with `tools`).
`ollama_native` uses `server/llm_client.py`. `hf_turbo` (set legacy `llm.backend`) uses `server/hf_llm_client.py`.
Engine speed is NOT the bottleneck for this bot **as long as all layers are on the GPU** (full-offload 14B
Q4 runs ~35-44 tok/s = ~0.6 s/decision). The default favors native-Windows reliability + game co-residency
over raw FP4 throughput. CPU-spilled layers (see below) collapse this to ~0.8 tok/s and break every cycle.

**KoboldCpp quick start:** `powershell -File installer/serve_koboldcpp.ps1 -Restart`
(forces full GPU offload + waits for `:5001/v1`; equivalently
`koboldcpp.exe --model Qwen3-14B-Q4_K_M.gguf --usecublas --gpulayers 999 --contextsize 4096 --jinja --jinja_tools --jinjathink false --skiplauncher --port 5001`)
- `--gpulayers 999` (all layers on GPU) is the **#1 latency control**. Symptom of CPU spill: VRAM used ~6 GB
  not ~13 GB, ~0.8 tok/s, every ReAct cycle times out into a `noop`. On a 16 GB RTX 5070 Ti a 14B Q4 + 4096
  ctx occupies ~13 GB. Verify with `nvidia-smi --query-gpu=memory.used --format=csv`.
- **Do NOT pass `--flashattention`** — KoboldCpp 1.115.x has flash attention **default-on** (only
  `--noflashattention` exists); the unknown flag makes argparse abort the launch.
- `--usecublas`, not `--usecuda`. `--contextsize` should match `llm.num_ctx` in config.json (4096) — larger
  just wastes VRAM the prompt builder never uses, squeezing the co-resident game.
- `--jinja_tools` routes tool calls through Qwen3's native `<tool_call>` template (needs `--jinja`); without
  it KoboldCpp's AutoGuess adapter emits plain text that only the XML/text fallback catches. `--jinjathink
  false` disables Qwen3 thinking.

### Voice mode (Phase 1)

Requires `voice.enabled = true` in `installer/config.json` and the optional deps below.
Ship default is `false` — existing setups are unaffected.

**1. Install deps**
```powershell
.\.venv\Scripts\python.exe -m pip install faster-whisper sounddevice piper-tts onnxruntime pynput numpy
```

**2. Download the Piper voice model** (ru_RU-irina-medium):
```powershell
# Download .onnx + .json from https://github.com/rhasspy/piper/releases
# Place in e.g. C:\models\piper\ru_RU-irina-medium.onnx
# Then set "tts": { "voice": "C:\\models\\piper\\ru_RU-irina-medium.onnx" }
```

**3. Flip the switch** in `installer/config.json`:
```json
"voice": { "enabled": true, ... }
```

**4. Pick your mouse button**: default `"x1"` (M4, side-back button).
Change to `"x2"` for M5 (side-forward). Mode `"toggle"` = press once on, press again off;
`"push"` = hold to speak. Set in `"gate": { "mode": "toggle", "mouse_button": "x1" }`.

**5. Self-test** (mic + STT + TTS, no game needed):
```powershell
.\.venv\Scripts\python.exe server/voice_io.py --selftest
```
Expected: logs "Voice mic loop active", you press the side button, say a phrase in Russian,
it prints the transcript and speaks it back via TTS. Silero VAD model (~1 MB) downloads
automatically to `~/.cache/silero-vad/silero_vad.onnx` on first run.

---

**TurboQuant** (arXiv:2504.19874): online vector quantization for KV cache.
Keys use Q_prod (MSE + QJL residual), values use Q_mse (rotation + Lloyd-Max codebook).
Config: `turbo_quant.key_bits`, `turbo_quant.value_bits` in config.json.

**HF backend requires**: `torch`, `transformers`, `bitsandbytes`, `scipy`, `accelerate`.
Install: `pip install torch transformers bitsandbytes scipy accelerate`

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
│   ├── save_state.py           Save/load bot state (T049)
│   ├── hf_llm_client.py        HF Transformers + TurboQuant backend
│   ├── turbo_quant.py          TurboQuant engine (arXiv:2504.19874)
│   └── turbo_quant_cache.py    TurboQuantCache (HF Cache API)
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
