# SupCom LLM AI Bot — "Perfection" Program Design

**Date:** 2026-06-11
**Branch:** `002-agent-react-loop`
**Goal (user's words):** "довести систему до идеала, чтобы легко и свободно играть с агентом."
**Status:** SP1 detailed and ready for planning; SP2/SP3 outlined.

---

## 1. Problem Statement

The bot connects and the LLM runs, but **plays like a generic base AI and does not react to chat**. Grounded in the live decision log (`llm_ai_decisions.log`, 2026-04-16, 8 consecutive cycles) and a full read-only trace of the runtime, three independent breaks were confirmed:

1. **Perception is broken (foundation).** Snapshots reaching the Python decision loop are missing the `economy`/`units`/`threats` keys and `tick` is null. `GameStateCollector.Collect()` (`mod/lua/AI/GameStateCollector.lua:185-216`) always includes these, so the data the loop processes is **not** its output — an empty/default snapshot arrives instead. The LLM is queried on emptiness (~4.6 s latency, `prompt_tokens=0`) and rationally returns `tool_calls: []` → the bot does nothing LLM-driven.
2. **Action is not wired (confirmed by code read, not speculation).** Even with good data, the two strategic tools are no-ops:
   - `set_strategy` only sets `brain.CurrentStrategy`; **`StrategyExecutor.Apply()` is never called** (the module exists but is never imported).
   - `build_units` writes `brain.LLMBuildPriority/Urgency` which **nothing ever reads** → 100% stub.
   - `TacticalMicroThread` (micro toward `AttackDirection`) is **never forked**.
   - Working: `scout`, `reclaim`, `chat`; partial: `attack`/`defend` (create base-AI platoons that the base AI may reclaim).
   Net: the LLM layer runs in parallel with no effect → "plays like a normal AI."
3. **Chat is broken twice.** Incoming chat rides inside the (broken) snapshot **and** is delivered only every 20–60 s. Outgoing display (`SyncAIChat → OnSync → DisplayBotChat`, trying 3 chat-UI methods) **fails silently** when the expected chat-UI function is absent.

These map to the user's stated priorities: ①+② = "сила стратегии", ③ = "чат и тимплей", hardening of ①/transport = "надёжность". The foundation is ① — strategy and chat both starve without it — so work proceeds bottom-up.

---

## 2. Cross-Cutting Decisions (locked)

### 2.1 Architecture — "Director over base AI" + reflex floor
The LLM **steers** the robust base FAF AI rather than **replacing** it.
- Base AI keeps running low-level economy/engineers/platoons (robust floor — the bot is never idle even if the LLM/transport stalls).
- LLM decisions **bias** the base AI's priorities (build focus, attack direction, retreat threshold) and issue tactical commands.
- The deterministic reflex layer catches crises instantly.
- This is **finishing the already-designed architecture** (`StrategyExecutor`, `TacticalMicro`, `ReflexLayer`, `EconomyManager` all exist, just unwired), not a rewrite.

### 2.2 Model — Qwen3.5 14B (deep), 4B (fast/fallback)
Hardware: RTX 5070 Ti (16 GB GDDR7, Blackwell sm_120), Ryzen 9 9950X, 32 GB RAM. The LLM **shares the 16 GB with the running game** (~1.5–2.5 GB).
- **Deep brain: `qwen3.5:14b` Q4** (~9 GB) — the strongest model that co-resides with the game in real time (~3–4 s/decision), with reliable tool-calling. Maximizes "сила стратегии" within the physical budget.
- **Fast/fallback: `qwen3.5:4b`** — routine cycles + existing 3-timeout auto-degrade.
- **27B (incl. NVFP4) is rejected for co-play:** Qwen3.6-27B-NVFP4 needs ~16–18 GB LLM-only (14 GB weights + vision encoder + KV), which **cannot co-reside** with the game on a 16 GB card. Revisit only with a 2nd GPU or accepting non-real-time play. Qwen3.6 has no official ≤14B dense variant.

### 2.3 Inference backend — pluggable, OpenAI-compatible
Refactor `server/llm_client.py` from Ollama-specific `/api/chat` to the standard **OpenAI-compatible `/v1/chat/completions`** (with `tools`).
- Engine becomes a swappable backend by config/URL: **Ollama** (zero-friction default — play today) ⇄ **vLLM / TensorRT-LLM / TabbyAPI** (modern, faster, guided/structured tool decoding — when the user stands it up).
- This satisfies "replace Ollama" architecturally without coupling the bot to bleeding-edge sm_120-NVFP4 (which, per 2026 research, has no official vLLM Windows support and immature FP4 kernels on desktop Blackwell).
- The XML-tool-call fallback parser is retained for Qwen quirks.

---

## 3. SP1 — "Perception you can trust" (detailed)

**Goal:** Every cycle, real game state reaches the LLM **or the system says loudly why not** — and the bot keeps playing on the reflex/base-AI floor instead of hanging on an empty query.

### 3.1 Task group A — Perception pipeline
- **A1. End-to-end snapshot tracing (debug-flagged).** Lua logs, right before send: `tick`, key count, and JSON byte length. Python logs the raw received line and the parsed key set. Purpose: on the next live game, pin the exact failing hop among the strong candidates: (a) DLL injection never completes → pipe delivers nothing → file-IPC fallback reads the wrong/stale line; (b) **game-log line truncation** of the long `[LLM_SNAP]` JSON → `json.loads` fails; (c) encoder/parse desync.
- **A2. Perception-health guard** in `_decision_loop` (`server/bridge_server.py`): validate the snapshot has core keys (`tick`, `economy`, `units`). If not → `WARNING` with the raw payload, **skip the LLM query** (don't burn a multi-second call on emptiness), and yield the turn to the reflex/base-AI floor. No more misleading empty `tool_calls`.
- **A3. Make one transport reliable.** Given DLL-injection fragility, make **file-IPC the dependable primary** and fix its command-dispatch break: `Callbacks.LLMCommand` (`mod/hook/lua/SimCallbacks.lua`) currently only stores data and **never dispatches** to the brain → wire it to `ApplyLLMDecision`. Guard the long-JSON path against log truncation (compact JSON / chunking). Keep the named pipe as an optional fast path.

### 3.2 Task group B — Reliability / loud failures
- **B1.** Fix the `decision is None` crash path (`bridge_server.py:404` accesses `.get` on a possibly-None decision when fallbacks are missing).
- **B2.** Make the ~6 critical-path silent failures loud (injection timing, missing log path, snapshot/command queue full, VFS path mismatch): targeted `WARNING`/`ERROR` logs + defensive guards.
- **B3.** Fix decision-log truthfulness: `prompt_tokens` is computed from a non-existent `decision["reasoning"]` key → always 0. Compute a real estimate from the actual prompt so the log is a trustworthy debugging tool.

### 3.3 Task group C — Backend decoupling (cross-cutting 2.3, lands here)
- **C1.** Refactor `llm_client.py` to OpenAI-compatible `/v1/chat/completions` with `tools`; keep Ollama working via its OpenAI-compatible endpoint (`/v1`). Config selects `base_url` + `model`. Retain XML fallback.
- **C2.** Config: `model_deep = qwen3.5:14b`, keep `model_fast = qwen3.5:4b`; document the backend-swap path (Ollama default; vLLM/TabbyAPI optional).

### 3.4 Out of scope for SP1
Strategy execution wiring (SP2), chat responsiveness/teamplay (SP3), vLLM/NVFP4 infra build-out (separate infra task), one-click launcher polish.

### 3.5 Validation / acceptance
- **Unit tests:** perception-health guard (valid / missing-keys / null snapshots); file-IPC parse including truncated/corrupt `[LLM_SNAP]` lines; `decision is None` no longer crashes; OpenAI-client request/response shape (mocked).
- **Live test (user launches a game):** decision log shows **non-null snapshot fields** and **non-empty `tool_calls`**; when state is genuinely empty, the log shows the guard `WARNING` and the bot keeps playing (no hang).
- All Python passes `uv run ruff check .` and `uv run pytest -v`.

---

## 4. SP2 — "Decisions that change the game" (outline)
Wire LLM strategy to real behavior: import and call `StrategyExecutor.Apply()` on `set_strategy`; fork `TacticalMicroThread` in `OnBeginSession`; make `build_units` real by injecting into the base AI's build queue (consume `LLMBuildPriority/Urgency`); ensure `attack`/`defend` platoons aren't reclaimed by the base AI. Acceptance: a `set_strategy` change visibly alters build order / factory output; an attack direction visibly moves the army. Own spec to follow.

## 5. SP3 — "Chat & teamplay" (outline)
Decouple incoming chat from the 20–60 s snapshot (dedicated fast path so the bot answers within a few seconds); harden outgoing display (verify the chat-UI injection method exists, log on failure); add ally awareness for genuine teamplay. Own spec to follow.

---

## 6. Sequencing
SP1 (foundation) → SP2 (strategy) → SP3 (chat). Each ends at a playable checkpoint. The backend decoupling (2.3 / C) lands inside SP1 because SP1 already touches and live-tests the LLM round-trip.
