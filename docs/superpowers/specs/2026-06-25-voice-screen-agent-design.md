# SupCom LLM AI Bot — Real-Time Voice + On-Screen-Context Agent Design

**Date:** 2026-06-25
**Branch:** `002-agent-react-loop`
**Goal (user's words):** "чтобы я в реальном времени общался с агентом через голосовой чат и командами на экране, чтобы он понимал, что вижу я и что я от него хочу." Serves the overarching program goal: "легко и свободно играть с агентом."
**Status:** Design approved section-by-section; ready for spec review → implementation plan.

---

## 1. Problem Statement

The bot plays as an allied AI, but the player cannot communicate with it in real time:

- **In-game chat is broken twice** (confirmed in `2026-06-11-llm-bot-perfection-design.md` §1.3): incoming chat is delivered only every 20–60 s inside the snapshot, and outgoing `SyncAIChat` display fails silently. Typing mid-fight is also impractical in an RTS.
- **The agent has no spatial awareness of the player.** It receives a 14-field snapshot (economy, units, threats, map control, phase, player_chat) but **no camera, cursor, or selection state** (`GameStateCollector.lua:200-218`). So even if chat worked, the player cannot say "attack *here*" — the agent has no referent for "here".

The player wants to **talk** to the agent (hands free, mid-game) and have it **understand what is on screen** ("here", "those", "there") and **respond visibly** (voice + a map ping where it is acting). This design delivers that, routing entirely around the broken in-game chat path.

---

## 2. Locked Decisions (from clarifying dialogue)

| Decision | Choice | Rationale |
|---|---|---|
| Meaning of "understand what I see" | **Deictic grounding** (camera focus + selection + last ping), **not** screen vision | The sim AI already has full ground-truth state; the only gap is *where the player is looking / pointing*. Deixis is cheap, reliable, and **frees VRAM** for game co-residency (no vision model). Covers ~90% of real commands. |
| Agent response channel | **Voice (TTS) + map pings/markers** | Player both hears and *sees* where the agent acts; reuses native SupCom pings. |
| Input gating | **Always-on VAD + mouse side-button gate** (toggle default, push optional); side button also triggers **barge-in** | Hands-free during play, but casual talk isn't sent unless gated. |
| TTS language | **Russian only** | Player preference. |
| Architecture | **Approach A — Python-centric, thin UI-Lua hook; Sim sandbox untouched** | Camera/cursor/selection/pings live in the UI Lua state; audio + libs live in Python. Lowest risk, maximally isolated, reuses existing IPC transports (`LOG('[LLM_...]')` out, `cmd_NNNN.lua` in). |
| Privacy | **Fully local** (faster-whisper + Piper); audio in RAM, not persisted | Consistent with the local-Qwen, no-data-leaves-machine ethos. |

**Rejected:** (B) Sim-centric screen context — Sim cannot read UI-layer camera/selection, would add hops. (C) Dedicated real-time socket/pipe sidecar — overkill for an RTS ally; the DLL/pipe path is being deprecated.

---

## 3. Architecture & Components

**Principle:** all new code lives in **Python** (audio + libraries) and **one thin UI-Lua hook** (camera/selection/pings). The Sim sandbox (`LLMAIBrain`) is **not modified**. Existing transports are reused: `LOG('[LLM_...]')` outbound, `cmd_NNNN.lua` inbound.

### 3.1 New module `server/voice_io.py` — three isolated units

| Unit | Responsibility | Interface | Depends on |
|---|---|---|---|
| `SpeechListener` | Mic capture + VAD + STT | event stream `Utterance(text, t0, t1)` | sounddevice, faster-whisper (CPU int8), Silero VAD (onnxruntime), pynput (side button) |
| `SpeechSpeaker` | TTS synth + playback | `await speak(text)`; events `speaking_started` / `speaking_ended` | piper-tts, sounddevice |
| `VoiceIO` (facade) | Wires listener + speaker, owns echo-guard state machine | `.utterances` (async stream), `.speak(text)` | the two above |

### 3.2 Changes to existing Python

| File | Change |
|---|---|
| `bridge_server.py` | (a) construct `VoiceIO` if `voice.enabled`; (b) `_voice_loop` task: each utterance → synthetic snapshot → `snapshot_queue.put()` (~line 397); (c) at chat-tool extraction seam (`545-559`) → `voice.speak(...)`; (d) consume new `[LLM_SCREEN]` channel → keep `latest_screen_context` |
| `state_processor.py` | render a `screen_context` block + a deictic policy line into the user prompt (`build_messages`) |
| `tools.py` | add `mark_map` action tool; extend location-taking tools to accept symbolic locations |
| `file_ipc.py` | parse `[LLM_SCREEN]` (mirror of `[LLM_SNAP]`); add outbound command type `ping`; add shared `resolve_location()` helper |
| `config.py` / `config.json` | new `voice` and `screen_context` blocks |

### 3.3 New UI-Lua hook (`mod-ui/.../screen_context.lua` + extend `gamemain.lua`)

| Unit | Responsibility |
|---|---|
| Screen-context emitter | every ~1 s and on `RegisterSelectionSetCallback`: read `GetCamera('WorldCamera'):GetFocusPosition()`/`:GetZoom()`, summarize `GetSelectedUnits()`, last player ping → `LOG('[LLM_SCREEN]'..json)` |
| Ping renderer | extend `PollCommandFile` to handle command type `ping` → `DisplayPing` / `DoPing` (color by kind, label, lifetime) |

**Isolation:** all new complexity is concentrated in one testable Python module (`voice_io.py`) plus one small UI hook. Existing IPC paths are reused; the Sim layer is unchanged.

---

## 4. Data Flow

Three independent flows, each debuggable in isolation.

### 4.1 Inbound (player → agent), event-driven
```
microphone
  → SpeechListener: VAD detects speech, gate (side button) permits → STT
  → Utterance(text="атакуй сюда", t0, t1)
  → _voice_loop builds a SYNTHETIC snapshot:
        clone(latest_real_snapshot)            # economy / units / threats — game context
        + player_chat      = [text]
        + screen_context   = latest_screen_context   # camera / selection / ping
        + trigger_event    = "player_chat"
  → snapshot_queue.put(...)                    # bridge_server.py ~397
  → _decision_loop (player_chat ∈ PRIORITY_EVENTS → immediate, skips 20 s poll)
  → state_processor.build_messages: state + screen_context + chat blocks
  → ReAct cycle → action tools (attack / defend / mark_map) + chat tool
```

### 4.2 Screen-context side-channel (continuous, ~1 Hz)
```
UI Lua screen_context.lua  (every ~1 s OR on selection change / new ping)
  → LOG('[LLM_SCREEN]{cam, sel, ping, t}')
  → game log
  → file_ipc tail (same mechanism as [LLM_SNAP])
  → bridge: latest_screen_context = ...        # always the freshest "frame"
```
The screen context flows **continuously and cheaply**; voice merely *attaches* the latest frame at the moment of an utterance — **zero added round-trip latency**.

### 4.3 Outbound (agent → player)
```
chat tool    → bridge:545-559 → VoiceIO.speak("иду защищать север") → Piper → headphones
                              (+ existing SyncAIChat still writes text to in-game chat)
mark_map tool → bridge → file_ipc.write_command{type:"ping", x, z, label, kind}
              → cmd_NNNN.lua → UI PollCommandFile → DisplayPing on map
```

### 4.4 Latency budget
STT (~0.5–1.5 s, CPU int8) + ReAct (~1–3 s, Qwen3.5-9B @ ~40 tok/s) + TTS (~0.5 s) ≈ **2–5 s**. `latest_screen_context` is already in memory (adds nothing).

**Decoupling:** flows 4.1 and 4.2 are independent — if screen context is stale/off, voice still works (the agent asks "where?" by voice). If voice fails, the bot plays normally.

---

## 5. Screen-Context Protocol (`[LLM_SCREEN]`)

**Coordinates:** SupCom world is the `(x, z)` ground plane (y = height). Camera focus, selection centroid, and pings all yield `(x, z)` — exactly what action tools consume. Single coordinate system, no conversions.

**One logged line (~200 bytes):**
```json
[LLM_SCREEN]{
  "t": 12345,
  "cam":  { "x": 256.0, "z": 512.0, "zoom": 120.0 },
  "sel":  {
    "n": 12,
    "by_tier": { "t1": 4, "t2": 8, "t3": 0, "exp": 0 },
    "by_kind": { "land": 12, "air": 0, "navy": 0, "engineer": 0, "structure": 0 },
    "center": { "x": 250.0, "z": 500.0 },
    "label":  "12 ед.: 8xT2 land, 4xT1 land"
  },
  "ping": { "x": 300.0, "z": 480.0, "kind": "attack", "age_s": 3 }
}
```

- `cam` — `GetCamera('WorldCamera'):GetFocusPosition()` + `:GetZoom()`. Anchor for "сюда / здесь / тут".
- `sel` — from `GetSelectedUnits()`: counts by tech tier and arm, centroid, prebuilt `label`. Anchor for "эти / они / выделенные".
- `ping` — last player ping. Anchor for "туда / там". **Best-effort, second priority** (needs ping-Sync observation); MVP deixis is fully covered by `cam` + `sel`.

**Emission cadence (hybrid period + event):**
- Periodic every `screen_context.poll_interval_s` (default **1.0 s**), with a light dedup: skip if nothing changed (camera moved < ε, same selection, no new ping) to keep the log clean while idle.
- **Immediate** on `RegisterSelectionSetCallback` (selection change) and on a new ping, so context is fresh at the instant the player acts.

**Staleness:** bridge stores `latest_screen_context` with its tick; `state_processor` compares to the current snapshot tick and, if older than `stale_after_s` (default 5 s), annotates the prompt "(вид N с назад)" so the model does not over-trust a stale frame.

**Lua 5.0:** `table.getn` / `table.insert`, no `#`, lazy `categories`, encode via bundled `JSON.lua`.

---

## 6. Deixis & Tools

**Separation of concerns:** the *linguistics* ("сюда" = camera) is done by the model in-prompt (its strength); the *symbol→coordinate substitution* is a deterministic, testable function. The model never invents floats.

### 6.1 Prompt policy (in `state_processor`, beside the `screen_context` block)
```
Игрок видит экран. Привязывай его слова к контексту вида:
  «сюда / здесь / тут»     → location="camera"
  «эти / они / выделенные» → location="selection"
  «туда / там»             → location="last_ping" (если есть, иначе спроси)
  «моя база / у меня»      → location="my_base"
Передавай СИМВОЛ, не координаты. Если привязать не к чему — спроси голосом (chat).
```

### 6.2 Symbolic locations (key robustness mechanism)
Any action tool with a location argument accepts **either** explicit `{x, z}` **or** a string:
`"camera" | "selection" | "last_ping" | "my_base"`. Before issuing the game command, the bridge runs it through one shared helper:
```python
resolve_location(loc, screen_ctx, ally_state) -> {x, z} | None
```
Pure function, reused by all tools. The model reliably emits a symbol; we always substitute the *freshest* coordinates.

### 6.3 New tool `mark_map`
```
mark_map(location, label, kind)
  location : {x,z} | "camera" | "selection" | "last_ping" | "my_base"
  label    : short ping caption (<= 20 chars), e.g. "защищаю"
  kind     : "alert" | "move" | "attack" | "defend" | "marker"   → ping color/type
```
Bridge: `resolve_location` → `write_command{type:"ping", x, z, label, kind}` → UI `DisplayPing`.

### 6.4 Existing action tools (`attack` / `defend` / …)
Their location argument also begins to accept symbols, resolved by the same `resolve_location`. One logic point, zero duplication.

### 6.5 Edge cases (model gets feedback and re-asks by voice)
- `"selection"` but selection empty → `None` → tool returns error "ничего не выделено" → agent voices "что защищать? выдели юнитов".
- `"last_ping"` with no ping → error (do not guess) → agent clarifies.
- Coordinates out of map bounds → reject + error.

The model never fires blind: if it cannot ground a reference, it asks the player — closing the conversational loop.

---

## 7. Error Handling, Echo-Guard, Degradation

**Side button = master gate + barge-in.** VAD always listens, but utterances are *sent to the agent* only while the gate is active. Modes (`voice.gate.mode`):
- `toggle` (default) — button toggles listening; auto-off after silence.
- `push` — hold to talk (classic PTT).

Talk while the gate is off is not sent.

**`VoiceIO` — single state machine (also the echo-guard):**
```
IDLE ──(gate on)──► LISTENING ──(VAD: end of utterance)──► emit Utterance, stay LISTENING
LISTENING ──(gate off / silence)──► IDLE
any ──(speak() called)──► SPEAKING        (STT suppressed — TTS is not transcribed = echo-guard)
SPEAKING ──(TTS done)──► previous state
SPEAKING ──(button pressed)──► barge-in: abort TTS, go LISTENING
```
Echo-guard = in `SPEAKING`, mic input is suppressed except an explicit button press (= barge-in). No acoustic echo cancellation needed — the button decides.

**STT filter:** min duration + confidence threshold; empty/garbage dropped.

**TTS backlog:** `speak()` enqueues; if pending > `voice.tts.max_pending` (default 2), drop the oldest (a fresh agent intent matters more than a stale one). Voice never lags behind the game.

**Graceful degradation — voice NEVER crashes the bot:**

| Failure | Behavior |
|---|---|
| No microphone / device busy | WARN, VoiceIO disabled, **bot keeps playing**, in-game chat still works |
| Whisper fails to load | same |
| Piper fails to load | STT-in still works; `speak()` → no-op + WARN (text still visible via `SyncAIChat`) |
| Mouse hook fails | fall back to VAD-only (conservative threshold) + WARN |
| Exception in `_voice_loop` | caught per-iteration, never reaches `_decision_loop` |

**Privacy/security:** faster-whisper and Piper are **fully local**, no network; audio lives only in RAM and is not written to disk (optional debug dump behind `voice.debug_dump_audio`, default off).

---

## 8. Testing & Acceptance Criteria

### 8.1 Unit tests (pure Python, no audio hardware)
- `resolve_location()` — each symbol → coords; empty selection → `None`; no ping → `None`; out-of-bounds → `None`; explicit `{x,z}` passthrough.
- `VoiceIO` state machine — IDLE→LISTENING→SPEAKING transitions, STT suppression in SPEAKING (echo-guard), barge-in on button, toggle vs push logic (driven by a fake clock + fake audio source).
- `[LLM_SCREEN]` parser — valid lines, malformed lines, "keep latest".
- Synthetic-snapshot builder — fake snapshot + utterance + screen_context → correct merged dict (`player_chat`, `screen_context`, `trigger_event="player_chat"`).
- `mark_map` schema (kind enum, label length), TTS drop-oldest, STT filter.

### 8.2 Integration tests (mock LLM + mock IPC, no game)
- Fake Utterance → synthetic snapshot enqueued with correct fields + immediate cycle.
- `chat` tool in LLM response → `VoiceIO.speak()` called with text.
- `mark_map` tool → ping command written with resolved coordinates.
- Mic-open failure → `_decision_loop` still runs.

### 8.3 Lua
Run a captured `[LLM_SCREEN]` sample line through the Python parser (locks the contract) + a documented manual checklist for the in-engine hook (camera/selection/ping emit).

### 8.4 Live acceptance (the real proof, in-game)
| # | Scenario | Expected |
|---|---|---|
| AC1 | look at a spot, gate, "атакуй сюда" | agent's army moves to camera focus + voice + ping, ≤ ~5 s |
| AC2 | select own units, "прикрой этих" | agent sends army to selection centroid + ping |
| AC3 | "защити мою базу" | `location=my_base` → agent at base |
| AC4 | "defend" with no context (empty) | agent asks by voice "что защищать?" |
| AC5 | agent is speaking | its voice does NOT enter STT (no false cycles) |
| AC6 | press button during agent speech | TTS aborts, listens to player (barge-in) |
| AC7 | mic / Piper disabled | bot keeps playing, bridge does not crash |
| AC8 | latency harness | median mic→ack ≤ ~5 s |

---

## 9. Suggested Build Order (for the plan)

- **Phase 1 — Voice loop** (STT/TTS/echo-guard/gate) with **no** screen context. Proves the conversational round-trip live (AC4–AC8 partially testable with text-only commands like "scout", "attack the enemy").
- **Phase 2 — Screen-context channel + deixis + `mark_map`** (`[LLM_SCREEN]` UI hook, `resolve_location`, prompt policy, ping rendering). Enables AC1–AC3.
- `ping` field (`"туда/там"`) is the last increment within Phase 2.

This stages live testing earlier and keeps each phase independently shippable.

---

## 10. Config Additions (`installer/config.json`)
```json
"voice": {
  "enabled": true,
  "stt":  { "model": "small", "language": "ru", "device": "cpu", "compute_type": "int8",
            "min_speech_ms": 400, "min_confidence": 0.5 },
  "tts":  { "engine": "piper", "voice": "ru_RU-irina-medium", "max_pending": 2 },
  "vad":  { "engine": "silero", "aggressiveness": 2, "silence_timeout_ms": 800 },
  "gate": { "mode": "toggle", "mouse_button": "x1" },
  "echo_guard": true,
  "input_device": null,
  "output_device": null,
  "debug_dump_audio": false
},
"screen_context": {
  "enabled": true,
  "poll_interval_s": 1.0,
  "include_camera": true,
  "include_selection": true,
  "include_ping": false,
  "stale_after_s": 5,
  "camera_move_epsilon": 5.0
}
```
(`mouse_button` `x1`/`x2` = M4/M5 via pynput.)

---

## 11. Out of Scope (YAGNI)
- Screen-pixel vision / multimodal model (explicitly rejected — deixis covers the need).
- Commanding the **player's** own units (the agent commands its own army; player units are read-only context via selection).
- Acoustic echo cancellation (the button-gated state machine removes the need).
- Languages other than Russian for TTS.
- Sub-second real-time transport (file-IPC + log-tail at ~1 Hz is sufficient).

---

## 12. Open Items to Verify Live
- `GetCamera('WorldCamera'):GetFocusPosition()` exact return (vs `GetPosition()`); confirm against the running UI Lua state.
- Whether the player-ping Sync is observable from the UI hook for the `ping` field (Phase 2 tail item).
- Piper Russian voice selection + sample-rate/device match with `sounddevice`.
- faster-whisper `small` vs `medium` Russian accuracy/latency trade-off on the user's CPU (tune `voice.stt.model`).
- pynput global mouse-button hook reliability while the game has focus (fullscreen vs windowed).
