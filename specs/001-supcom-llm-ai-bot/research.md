# Research: SupCom:FA LLM AI Bot

**Phase**: 0 — Research & Technical Decisions
**Date**: 2026-02-18
**Branch**: `001-supcom-llm-ai-bot`

---

## 1. Game Environment: FAF Lua Architecture

### Decision
Use FAF (Forged Alliance Forever) Lua codebase as the modding target. The game's AI scripting layer is Lua 5.0 (LuaPlus 1081), split into two isolated environments:

- **Sim layer** (`/lua/`, `/hook/lua/`): Deterministic game simulation. All AI logic lives here. Single-threaded, uses coroutines. No socket/IO libraries available by default.
- **UI layer** (`/lua/ui/`): Rendering and interface. Separate from sim. The debug overlay will live here.

### FAF Lua Source
The FAF Lua codebase lives in the [FAForever/fa](https://github.com/FAForever/fa) repository. Key files:
- `lua/aibrain.lua` — AIBrain base class with all economy/unit query methods
- `lua/sim/Unit.lua` — Unit class
- `engine/Sim.lua` — Engine-exposed Lua functions (C++ bindings)
- `hook/lua/aibrains/` — Where custom AI brains are registered

### Rationale
FAF has active maintenance, better documentation, larger community, and the AI-Uveso marker generator infrastructure. The vanilla game's AI APIs are less documented and the player base is primarily on FAF.

---

## 2. IPC Bridge: Lua ↔ Python Communication

### Decision: Named Pipes via a C++ DLL

**Mechanism**: Windows Named Pipes (via `CreateNamedPipe` / `ConnectNamedPipe` Win32 API)

**Architecture**:
```
Game Process (SupForge.exe)
  └── Custom DLL (supcom_llm_bridge.dll)
        ├── Background thread: Named Pipe I/O (never blocks sim thread)
        ├── Send queue: game state snapshots → Python
        ├── Receive queue: LLM commands ← Python
        └── Lua bindings: LLmBridgeSend(json), LLmBridgeReceive() → json

Python Process (bridge_server.py)
  └── asyncio server
        ├── Named Pipe reader: receives game state from DLL
        ├── LLM client: sends prompts to Ollama API
        └── Named Pipe writer: sends commands back to DLL
```

**Why Named Pipes over alternatives**:
| IPC Method | Latency | Complexity | Windows Support | Notes |
|---|---|---|---|---|
| **Named Pipes** | ~1-2ms | Low | Native Win32 | Best fit. Reliable, no ports needed |
| Shared Memory | <1ms | High | Win32 | Complex sync primitives (mutexes); harder to implement safely |
| Local TCP Socket | ~2-5ms | Medium | Full | Requires port management; Lua DLL can use Winsock |
| File polling | ~50-500ms | Very Low | Full | Too slow for 45s cycle; not a bridge |

### DLL Loading Mechanism
The DLL is loaded via a **FAF game patch hook**, not binary injection. FAF supports custom init scripts via the `/init` flag. The game loads the DLL from a path specified in the init script, registering new Lua functions.

Key DLL exports:
```cpp
// Registers Lua-callable functions into the game's Lua state
extern "C" __declspec(dllexport) int luaopen_llm_bridge(lua_State* L);

// Registered Lua functions:
// LLMBridge.Send(json_string)  → fire-and-forget to send queue
// LLMBridge.Receive()          → returns latest command from receive queue (nil if none)
// LLMBridge.IsConnected()      → returns true/false
```

### Async Pattern (DLL Side)
The DLL spawns a **background Win32 thread** on load:
1. Connects to the named pipe (retry loop)
2. Reads from an in-memory `send_queue` (lock-free SPSC ring buffer) and writes to pipe
3. Reads from pipe and writes to `receive_queue`

The Lua AI brain polls `LLMBridge.Receive()` on its own schedule — never blocks.

### Rationale
Named pipes are the simplest reliable IPC on Windows, native to Win32, no external dependencies, no port conflicts, and work within the game's process sandbox.

---

## 3. Data Serialization Format

### Decision: JSON (UTF-8)

**Why JSON**:
- Lua has a lightweight JSON library (`dkjson`) already used in FAF mods
- Python's `json` module is zero-dependency
- Human-readable — critical for debugging and the decision log
- Sufficient performance at 45-second polling intervals

**Game State Snapshot structure** (Lua → Python):
```json
{
  "tick": 12450,
  "game_time_s": 415,
  "phase": "mid",
  "faction": "UEF",
  "economy": {
    "mass_income": 12.4,
    "mass_stored": 342,
    "mass_storage_max": 1000,
    "energy_income": 980,
    "energy_stored": 4200,
    "energy_storage_max": 8000
  },
  "units": {
    "factories": 3,
    "engineers": 8,
    "land_military": 24,
    "air_military": 0,
    "t1": 18,
    "t2": 6,
    "t3": 0,
    "experimentals": 0
  },
  "threats": {
    "near_base": 0,
    "nearest_enemy_distance": 820
  },
  "map_control_pct": 38,
  "last_event": "army_lost",
  "player_chat": ["attack north", ""],
  "current_strategy": "land_push",
  "mode": "enemy"
}
```

**Strategic Command structure** (Python → Lua):
```json
{
  "strategy": "tech_up",
  "build_priority": ["t2_factory", "t2_mex", "shield"],
  "army_composition": {"land": 0.7, "air": 0.2, "navy": 0.1},
  "attack_direction": "north",
  "retreat_threshold": 0.3,
  "chat_message": "Switching to tech 2. Building shields.",
  "reasoning": "Enemy has T2 units; T1 army ineffective. Recommend T2 transition."
}
```

### Alternatives Considered
- **MessagePack**: 30-40% smaller, but no human-readable debug; not worth it at 45s cadence
- **Protocol Buffers**: Overkill, requires codegen, complex setup for this use case

---

## 4. FAF AI Mod Structure

### Decision: Custom AIBrain Framework (M28AI approach)

Rather than using the default builder/platoon framework, implement a **custom AIBrain** with its own decision loop. This allows direct LLM command integration without fighting the builder priority system.

### Minimum Mod File Structure
```
mod/
├── mod_info.lua                    # Mod metadata and registration
├── hook/
│   └── lua/
│       └── aibrains/
│           └── index.lua           # Registers the custom AI in game's brain list
└── lua/
    └── AI/
        ├── LLMAIBrain.lua          # Main AI brain class
        ├── LLMBridge.lua           # DLL wrapper + polling logic
        ├── StrategyExecutor.lua    # Translates LLM commands → game actions
        ├── TacticalMicro.lua       # Real-time unit control (no LLM dependency)
        ├── EconomyManager.lua      # Mass/energy management heuristics
        ├── BuildOrderUEF.lua       # UEF-specific build templates
        └── GameStateCollector.lua  # Assembles game state snapshots
```

### mod_info.lua example
```lua
name = "SupCom LLM AI Bot"
version = 1
copyright = ""
description = "LLM-powered AI opponent/ally with natural language chat"
author = ""
url = ""
uid = "supcom-llm-ai-bot-v001"
selectable = true
enabled = true
exclusive = false
ui_only = false
requires = {}
requiresNames = {}
```

### AI Brain Registration (hook/lua/aibrains/index.lua)
```lua
-- Appended to existing file via FAF hook system
local existingAIs = __original_GetAIBrains()
table.insert(existingAIs, {
    key = 'LLM_AI',
    name = 'LLM AI Bot (UEF)',
    rating = 1000,
    nuke = true,
    sonar = true
})
return existingAIs
```

### Main Brain Loop (LLMAIBrain.lua pattern)
```lua
-- Coroutine-based brain, runs every N ticks
function AIBrain:OnCreateAI(planName)
    -- Start background coroutines
    self:ForkThread(self.StrategyUpdateThread)
    self:ForkThread(self.TacticalMicroThread)
    self:ForkThread(self.ChatHandlerThread)
end

function AIBrain:StrategyUpdateThread()
    while true do
        local snapshot = GameStateCollector.Collect(self)
        LLMBridge.Send(snapshot)
        WaitSeconds(45)  -- baseline polling
        local cmd = LLMBridge.Receive()
        if cmd then
            StrategyExecutor.Apply(self, cmd)
        end
    end
end
```

### Chat System Access
```lua
-- Send message to all players (from Sim layer via SimCallbacks)
-- Requires routing through UserSync/SimCallbacks bridge
-- Pattern: write to a sync table, UI mod reads and displays

-- Receiving player chat: hook into SimCallbacks.lua
-- SimCallbacks.ChatMessage(sender, message) → parse and queue
```

---

## 5. LLM Model & Serving

### Decision: Ollama + Qwen2.5 7B Instruct (Q5_K_M)

#### Extended Model Research (2026-02-18): Candidate Evaluation

Four additional models were evaluated as potential replacements or alternatives to the planned Qwen2.5-7B-Instruct-Q5_K_M. Research covers Ollama availability, VRAM requirements, Russian language quality, structured JSON output, inference speed on RTX 2080 (8GB), and game-AI-relevant features.

---

##### Model 1: mistralai/Mistral-7B-Instruct-v0.2

**Reality check**: Real, shipping model. Available on [HuggingFace](https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.2) with full GGUF support.

**Ollama availability**: Officially available in the Ollama library.
- `mistral:7b-instruct-v0.2-q4_K_M` — 4.4 GB
- `mistral:7b-instruct-v0.2-q5_K_M` — 5.1 GB
- `mistral:7b-instruct-v0.2-q8_0` — 7.7 GB

Note: v0.3 is the newer version (`mistral:7b-instruct-v0.3-q5_K_M`) and adds native function-calling tokens (TOOL_CALLS, AVAILABLE_TOOLS, TOOL_RESULTS). v0.2 lacks these tokens; prefer v0.3 for structured output pipelines.

**VRAM requirements (GGUF)**:

| Quantization | File Size | Min VRAM (no offload) | RTX 2080 fit? |
|---|---|---|---|
| Q4_K_M | ~4.4 GB | ~5-6 GB | Yes, comfortable |
| Q5_K_M | ~5.1 GB | ~6-7 GB | Yes, good headroom |
| Q8_0 | ~7.7 GB | ~8-9 GB | Borderline — context cache may overflow |

**Russian language quality**: Moderate-to-Good. Mistral 7B was trained primarily on English and European languages. Russian is included in the training data but is not a priority language. Mistral does not publish multilingual benchmark scores broken out by language. Community reports on r/LocalLLaMA indicate Russian quality is noticeably below Qwen2.5-7B and Qwen3-8B at the same parameter count. No MERA (Russian-language benchmark) submission found.

**Structured output / JSON mode**:
- v0.2: JSON possible via prompt engineering only; no native grammar-constrained output tokens.
- v0.3: Adds tool-call tokens; Ollama raw mode supports function calling.
- Ollama's `format: "json"` schema mode works but adherence is less reliable than Qwen models.
- Community reports note occasional JSON malformation under complex schemas.

**Inference speed on RTX 2080**: ~42-50 tokens/s at Q4_K_M, ~38-45 tokens/s at Q5_K_M. RTX 2080 achieves approximately 34-42 tokens/s on Llama2-7B class models; Mistral 7B performs similarly (same architecture class, GGML-optimized). Sufficient for 45-second polling cycles.

**Game AI relevance**:
- Instruction following: Good (v0.2) to Very Good (v0.3 with tool tokens).
- Context window: 32,768 tokens — adequate for game state snapshots.
- Latency: Acceptable. A 256-token response at 45 tokens/s completes in ~5-6 seconds.
- Limitation: Russian quality and JSON reliability are weaker than Qwen models. v0.2 is superseded by v0.3. No reason to prefer v0.2 over v0.3.

---

##### Model 2: Qwen/Qwen3-8B

**Reality check**: Real, shipping model. Released May 2025. Available on [HuggingFace](https://huggingface.co/Qwen/Qwen3-8B) and official Ollama library.

**Ollama availability**: Officially in the Ollama library.
- `qwen3:8b` — default tag (Q4_K_M, ~5.0 GB)
- `qwen3:8b-q4_K_M` — explicit Q4_K_M
- `qwen3:8b-q5_K_M` — Q5_K_M
- `qwen3:8b-q8_0` — Q8_0

**VRAM requirements (GGUF)**:

| Quantization | File Size | Min VRAM | RTX 2080 fit? |
|---|---|---|---|
| Q4_K_M | ~5.03 GB | ~6-7 GB | Yes |
| Q5_K_M | ~5.85 GB | ~7 GB | Yes, tight but workable |
| Q8_0 | ~8.71 GB | ~9-10 GB | No — exceeds 8GB VRAM |

**Russian language quality**: Very Good. Qwen3 was pre-trained on ~36 trillion tokens covering 119 languages and dialects — 3x more languages and 2x more tokens than Qwen2.5. Russian is explicitly included. Qwen3-8B matches or exceeds Qwen2.5-14B on multilingual benchmarks. Qwen3-14B is top-3 for Russian in community rankings (2026 guide). The 8B variant is expected to perform comparably to Qwen2.5-14B in Russian tasks.

**Structured output / JSON mode**:
- Qwen3 has excellent structured output support. Ollama's `format` parameter with JSON schema works reliably.
- Built-in tool-calling via XML-tagged function signatures.
- Community articles specifically note `qwen3:8b` as a recommended model for Ollama structured output.
- Thinking mode (`/think`) available but can be disabled with `/no_think` for low-latency use.

**Inference speed on RTX 2080**: ~35-45 tokens/s at Q4_K_M. Qwen3-8B at Q4_K_M produces ~25 tokens/s on CPU-only laptop; GPU-accelerated RTX 2080 at ~4x multiplier gives approximately 35-50 tokens/s. RTX 5090 achieves 145 tokens/s; scaling suggests RTX 2080 at roughly 25-30% of that = ~36-43 tokens/s. Adequate for 45-second cycle with 256-token responses completing in ~5-7 seconds.

**Game AI relevance**:
- Thinking mode: Qwen3 includes hybrid thinking/non-thinking. Use `"/no_think"` in system prompt or temperature control for low-latency game AI responses. Without thinking mode, latency is equivalent to a standard instruct model.
- Context window: 32,768 tokens (native); 128K with YaRN scaling.
- Instruction following: Excellent. Qwen3-8B significantly outperforms Qwen2.5-7B on instruction-following benchmarks (IFEval).
- Limitation: Q8_0 does not fit in 8GB VRAM — must use Q4_K_M or Q5_K_M. Q5_K_M is tight (7GB+ with context buffer).

---

##### Model 3: Qwen/Qwen3-4B-Instruct-2507

**Reality check**: Real, confirmed model. Released July 2025 (the "2507" suffix denotes the release date: 25=2025, 07=July). Available on [HuggingFace](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) with extensive quantized variants from bartowski, unsloth, and others.

**Ollama availability**: Officially in the Ollama library.
- `qwen3:4b-instruct-2507-q4_K_M` — ~2.5 GB
- `qwen3:4b-instruct-2507-q8_0` — ~4.3 GB
- `qwen3:4b-instruct-2507-fp16` — ~8.1 GB (fills entire 8GB VRAM)

**Key improvements over base Qwen3-4B** (from official model card):
- AIME25 reasoning: 19.1 → 47.4 (major jump)
- Arena-Hard v2 alignment: 9.5 → 43.4
- Creative Writing v3: 53.6 → 83.5
- IFEval (instruction following): improved to 83.4
- Context: 262,144 tokens native (vs 32K in base Qwen3-4B)
- Significant gains in long-tail multilingual knowledge

**VRAM requirements (GGUF)**:

| Quantization | File Size | Min VRAM | RTX 2080 fit? |
|---|---|---|---|
| Q4_K_M | ~2.5 GB | ~3-4 GB | Easily |
| Q5_K_M | ~2.89 GB | ~4-5 GB | Easily |
| Q8_0 | ~4.3 GB | ~5-6 GB | Yes, comfortable |
| FP16 | ~8.1 GB | ~8-9 GB | Borderline |

**Russian language quality**: Good-to-Very Good. Qwen3-4B inherits the 119-language training corpus. The 2507 revision specifically claims "substantial gains in long-tail knowledge coverage across multiple languages." At 4B parameters, Russian quality will be below the 8B class, but significantly better than Mistral 7B for Russian. Suitable for short command-and-response patterns used in game AI.

**Structured output / JSON mode**:
- Inherits full Qwen3 structured output support via Ollama's `format` parameter.
- IFEval score of 83.4 indicates strong instruction following — reliable JSON schema adherence.
- Tool calling: supported (Qwen3 family).

**Inference speed on RTX 2080**: ~70-90 tokens/s at Q4_K_M, ~60-75 tokens/s at Q5_K_M, ~50-65 tokens/s at Q8_0. 4B models run substantially faster than 7-8B models on the same hardware. A 256-token response completes in ~3 seconds at Q4_K_M — excellent for real-time game AI.

**Game AI relevance**:
- Fastest among the candidates for real-time responsiveness.
- 256K context window allows embedding extensive game history or ruleset documentation.
- Limitation: 4B parameter models sacrifice reasoning depth vs 7-8B. Complex multi-factor strategic decisions may show quality degradation. However, for the structured command output format used in this project (fixed JSON schema with ~8 fields), the quality difference may be negligible.
- Thinking mode available but disable with `/no_think` for low latency.

---

##### Model 4: LiquidAI/LFM2-8B-A1B

**Reality check**: Real, confirmed model. Released October 2025. Available on [HuggingFace](https://huggingface.co/LiquidAI/LFM2-8B-A1B) with official GGUF, ONNX, and community quantizations.

**Architecture**: Hybrid Mixture-of-Experts (MoE). 8.3B total parameters, only 1.5B active per token. Uses 32 experts (top-4 activated), 18 LIV convolution blocks + 6 GQA attention blocks. Designed explicitly for edge/on-device deployment.

**Ollama availability**: NOT in the official Ollama library. Only community-uploaded versions exist:
- `sam860/LFM2:8b-A1B-Q4_0` — community upload, Q4_0 format
- `sam860/LFM2:8b-a1b-Q8_0` — community upload, Q8_0 format

Note: Q4_0 is an older, lower-quality quantization than Q4_K_M. There is no official Ollama model page for LFM2. Community uploads may not receive updates or be reliable long-term.

**VRAM requirements (GGUF)** (from bartowski/LiquidAI_LFM2-8B-A1B-GGUF):

| Quantization | File Size | Min VRAM | RTX 2080 fit? |
|---|---|---|---|
| Q4_K_M | ~5.05 GB | ~6-7 GB | Yes |
| Q5_K_M | ~5.92 GB | ~7 GB | Yes, tight |
| Q8_0 | ~8.87 GB | ~9-10 GB | No — exceeds 8GB VRAM |

**Russian language quality**: POOR — Russian is NOT a supported language. LFM2-8B-A1B officially supports only: English, Arabic, Chinese, French, German, Japanese, Korean, Spanish. Russian is not included in the prioritized or officially supported language set. The training mixture (60% English, 25% multilingual, 15% code) does not specifically target Russian. This is a disqualifying factor for this project.

**Structured output / JSON mode**:
- Tool use is supported via JSON schema definitions between special tokens `<|tool_list_start|>` / `<|tool_list_end|>`.
- Output defaults to Pythonic function calls; JSON format requires explicit system prompt instruction: `"Output function calls as JSON"`.
- Not integrated with Ollama's native `format: "json"` schema enforcement (no official Ollama model).

**Inference speed on RTX 2080**: Fast per-token due to MoE sparse activation (only 1.5B active parameters per token), but total model must be loaded. On Apple M2 Pro with Metal: ~38 tokens/s at Q4_0; Samsung Galaxy S24 Ultra (phone CPU): ~14 tokens/s. RTX 2080 CUDA estimate with full GPU offload: likely 60-90 tokens/s at Q4_K_M, given that active compute is equivalent to ~1.5B dense model.

**Game AI relevance**:
- Designed for edge devices; optimized for CPU inference on phones/laptops — RTX 2080 will overprovision it.
- Liquid AI explicitly states: "We do not recommend using [LFM2 models] for tasks that are knowledge-intensive or require programming skills."
- Lacks Russian language support — disqualifies it for this project.
- No official Ollama integration — requires manual setup via llama.cpp or direct GGUF loading.
- Interesting architecture but wrong tool for this use case.

---

#### Extended Model Comparison Table

| Property | Qwen2.5-7B Q5_K_M (current plan) | Mistral-7B-v0.2 Q5_K_M | Qwen3-8B Q4_K_M | Qwen3-4B-Inst-2507 Q5_K_M | LFM2-8B-A1B Q4_K_M |
|---|---|---|---|---|---|
| **HuggingFace** | Real | Real | Real | Real | Real |
| **Ollama official** | Yes (`qwen2.5:7b-instruct`) | Yes (`mistral:7b-instruct-v0.2`) | Yes (`qwen3:8b`) | Yes (`qwen3:4b-instruct-2507`) | No (community only: `sam860/LFM2`) |
| **Ollama tag (recommended quant)** | `qwen2.5:7b-instruct-q5_K_M` | `mistral:7b-instruct-v0.2-q5_K_M` | `qwen3:8b-q4_K_M` or `qwen3:8b` | `qwen3:4b-instruct-2507-q4_K_M` | `sam860/LFM2:8b-A1B-Q4_0` (no Q4_K_M) |
| **VRAM @ Q4_K_M** | ~5.5 GB | ~4.4 GB | ~5.03 GB | ~2.5 GB | ~5.05 GB |
| **VRAM @ Q5_K_M** | ~5.5 GB | ~5.1 GB | ~5.85 GB | ~2.89 GB | ~5.92 GB |
| **VRAM @ Q8_0** | ~8.5 GB (tight) | ~7.7 GB | ~8.71 GB (no fit) | ~4.3 GB | ~8.87 GB (no fit) |
| **RTX 2080 recommended quant** | Q5_K_M | Q5_K_M | Q4_K_M or Q5_K_M | Q5_K_M or Q8_0 | Q4_K_M only |
| **Russian quality** | Excellent | Moderate | Very Good | Good | Poor (unsupported) |
| **Languages** | 29+ languages | EN-focused, partial RU | 119 languages | 119 languages | EN+7 languages, NO Russian |
| **JSON / structured output** | Very good (native) | Moderate (v0.2); Good (v0.3) | Excellent (native schema) | Excellent (IFEval 83.4) | Moderate (requires prompt setup, no Ollama schema) |
| **Tool calling** | Yes | Yes (v0.3 only) | Yes (native) | Yes (native) | Yes (custom tokens) |
| **Context window** | 128K | 32K | 32K (128K YaRN) | 262K | 32K |
| **Speed @ RTX 2080 (est., tok/s)** | ~45-60 | ~38-50 | ~35-45 | ~60-75 | ~60-90 (sparse MoE) |
| **Instruction following** | Very good | Good | Excellent | Excellent | Moderate |
| **Thinking/reasoning mode** | No | No | Yes (disable with /no_think) | Yes (disable with /no_think) | No |
| **Special limitations** | — | v0.2 outdated vs v0.3 | Q8_0 doesn't fit 8GB | 4B — less depth | No RU, no official Ollama, "not for knowledge tasks" |
| **Architecture** | Dense transformer | Dense transformer | Dense transformer | Dense transformer | MoE (8.3B total, 1.5B active) |
| **Release date** | Sep 2024 | Dec 2023 | Apr 2025 | Jul 2025 | Oct 2025 |
| **Recommendation** | Current choice, solid | Not recommended (weaker RU, outdated) | Strong alternative | Speed-focused alternative | Disqualified (no Russian) |

---

#### Recommendation

**Maintain current choice: Qwen2.5-7B-Instruct-Q5_K_M**, with Qwen3-8B-Q4_K_M as the primary upgrade candidate.

Rationale:

1. **LFM2-8B-A1B is disqualified**: Russian is not an officially supported language, it has no official Ollama integration, and Liquid AI explicitly recommends against knowledge-intensive tasks. Despite impressive architecture, it is the wrong tool for this use case.

2. **Mistral-7B-v0.2 is not recommended**: Weaker Russian than Qwen models, less reliable JSON output, and the v0.2 variant is superseded by v0.3. Even v0.3 offers no advantage over Qwen2.5-7B for Russian+JSON tasks.

3. **Qwen3-8B-Q4_K_M is a strong future upgrade**:
   - Better instruction following and Russian quality than Qwen2.5-7B at Q4_K_M.
   - Fits comfortably in 8GB VRAM at Q4_K_M (5.03 GB model weight).
   - Excellent Ollama integration with schema-constrained JSON output.
   - Disable thinking mode via `/no_think` system prompt directive for low-latency game AI responses.
   - Downside: Q5_K_M is tight on 8GB (leaves ~1GB for KV cache), and Q8_0 does not fit at all. Must run Q4_K_M, which is slightly lower quality than current Qwen2.5 Q5_K_M.

4. **Qwen3-4B-Instruct-2507 is a speed-focused alternative**:
   - Dramatically faster (~70-90 tokens/s at Q4_K_M) — useful if latency below 45 seconds becomes a requirement.
   - 2507 revision has exceptional instruction following (IFEval 83.4) and 256K context.
   - Russian quality acceptable for structured command output.
   - Downside: 4B parameters may produce less sophisticated strategic reasoning than 7-8B models.
   - Consider if the 45-second polling cycle is reduced to 10-15 seconds in future iterations.

**Recommended upgrade path**:
- Phase 1 (now): `qwen2.5:7b-instruct-q5_K_M` — proven, excellent Russian, comfortable VRAM.
- Phase 2 (when Qwen3 GGUF quality improves): Switch to `qwen3:8b-q4_K_M` for better instruction following and broader multilingual training.
- Phase 3 (if real-time response needed): Evaluate `qwen3:4b-instruct-2507-q4_K_M` for sub-10-second turnaround.

#### Model Comparison (7B class, Russian support, 8GB VRAM)

| Model | Russian Quality | Structured Output | VRAM @ Q5 | Speed (tok/s RTX2080) |
|---|---|---|---|---|
| **Qwen2.5 7B Instruct** | Excellent | Very good | ~5.5GB | ~45-60 |
| Qwen3 8B (Q4_K_M) | Very Good | Excellent | ~5.0GB | ~35-45 |
| Qwen3 4B-Instruct-2507 | Good | Excellent | ~2.9GB | ~60-75 |
| Mistral 7B v0.3 | Good | Good | ~5.1GB | ~38-50 |
| LFM2-8B-A1B | Poor (no RU) | Moderate | ~5.9GB | ~60-90 |
| Llama 3.1 8B Instruct | Good | Good | ~6.5GB | ~40-55 |
| Llama 3.2 3B | Moderate | Good | ~2.5GB | ~90-120 |
| Gemma 2 9B | Good | Good | ~7.2GB | ~30-40 |

**Winner: Qwen2.5 7B Instruct** — best Russian language performance among 7B models, strong structured output (JSON), fits in 5.5GB VRAM (leaves headroom on RTX 2080). Qwen3-8B is the preferred upgrade once quantization quality at Q4 is validated on the target hardware.

#### Serving: Ollama

| Solution | Windows Support | API Compatibility | JSON Mode | Setup Ease |
|---|---|---|---|---|
| **Ollama** | Native | OpenAI-compatible | Yes | Very easy |
| llama.cpp server | Good | OpenAI-compatible | Yes | Moderate |
| vLLM | Linux-only | OpenAI-compatible | Yes | Complex |
| LM Studio | Native | OpenAI-compatible | Yes | GUI-only |

**Winner: Ollama** — native Windows support, one-line model pull, OpenAI-compatible REST API, JSON mode (`format: "json"`), integrates trivially with Python via `requests` or `httpx`.

#### Quantization
**Q5_K_M** — best quality/VRAM tradeoff for inference-focused tasks. Q4_K_M is faster but noticeable quality degradation for Russian; Q8_0 exceeds 8GB VRAM.

#### Ollama API call from Python
```python
import httpx, json

async def get_strategy(game_state: dict) -> dict:
    response = await client.post("http://localhost:11434/api/generate", json={
        "model": "qwen2.5:7b-instruct-q5_K_M",
        "prompt": build_prompt(game_state),
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 256}
    })
    return json.loads(response.json()["response"])
```

#### Fine-tuning Assessment
Fine-tuning a 7B model on SupCom replay data is **feasible but deferred**. The hardware (RTX 2080, 8GB VRAM) supports QLoRA fine-tuning with libraries like Unsloth. However, the primary challenge is assembling structured training data from replays. This is a future iteration task.

---

## 6. UI Debug Overlay

### Decision: FAF UI Mod (separate `ui_only` layer)

FAF supports UI mods that run in the **User layer** (not Sim), drawing custom panels using the game's XML/Lua UI framework (`UIUtil`, `LayoutHelpers`). The overlay reads from a shared state table (`import('/lua/ui/game/gameview.lua')`) and displays it as a non-blocking panel.

**Overlay features**:
- Current strategy name (e.g., "land_push → tech_up transition")
- Last LLM reasoning (truncated)
- Economy status
- Next LLM query countdown
- LLM connection status indicator
- Toggle: `Ctrl+Shift+D`

**Log file location**: `C:\ProgramData\FAForever\logs\llm_ai_decisions.log`
Format: timestamped JSONL (one JSON object per line)

---

## 7. Python Bridge Server

### Decision: Python 3.11 + asyncio + httpx

```
bridge_server.py
├── PipeServer       — asyncio NamedPipe reader/writer (via pywin32)
├── LLMClient        — async Ollama API client (httpx)
├── StateProcessor   — parses game state, builds LLM prompt
├── CommandBuilder   — formats LLM response into game command JSON
├── DecisionLogger   — writes JSONL decision log
└── FallbackStrategy — rule-based commands when LLM is unavailable
```

**Dependencies**: `pywin32` (named pipe access), `httpx` (async HTTP), standard library only otherwise.

---

## 8. Source Structure Decision

### Multi-component project (3 independent components)

```
[repo root]/
├── mod/                          # FAF Lua AI mod (installed to FAF mods folder)
│   ├── mod_info.lua
│   ├── hook/lua/aibrains/
│   └── lua/AI/
│
├── bridge/                       # C++ DLL (Windows DLL, loaded by game)
│   ├── src/
│   │   ├── dllmain.cpp
│   │   ├── lua_bridge.cpp        # Lua function registration
│   │   ├── pipe_server.cpp       # Named pipe I/O + background thread
│   │   └── LuaAPI.h              # From FAForever/FA-Binary-Patches
│   ├── CMakeLists.txt
│   └── build/                    # Compiled DLL output
│
├── server/                       # Python bridge server
│   ├── bridge_server.py          # Main entry point
│   ├── llm_client.py
│   ├── state_processor.py
│   ├── command_builder.py
│   ├── decision_logger.py
│   ├── fallback_strategy.py
│   └── requirements.txt
│
├── installer/                    # Setup scripts
│   ├── install.ps1               # Guided installation PowerShell script
│   └── config.json               # User configuration (model, difficulty, paths)
│
└── docs/
    └── quickstart.md
```

---

## 9. Save/Load State Persistence

### Decision: External state file synchronized with FAF save

On game save, the Lua mod writes bot state (current strategy, LLM conversation history, tactical context, game tick) to a JSON file alongside the FAF save file. On load, the Python server reads this file and restores context before the first LLM query.

**Save file location**: `%APPDATA%\FAForever\saves\llm_bot_state_{savename}.json`

---

## 10. Fallback AI

### Decision: Simplified Lua rule-based AI (subset of Sorian AI patterns)

When LLM is unavailable, the bot executes a minimal rule-based strategy:
1. Build to a fixed economic template (5 T1 factories, 10 MEX, 15 pgens)
2. Form attack waves of 20+ land units every 3 minutes
3. Reclaim if mass stalled

This keeps the game playable and is entirely Lua-based with no external dependencies.
