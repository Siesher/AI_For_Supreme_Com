# Tasks: SupCom:FA LLM AI Bot

**Input**: Design documents from `/specs/001-supcom-llm-ai-bot/`
**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/ ✅ quickstart.md ✅

**Models**: Qwen3-8B Q4_K_M (`qwen3:8b`, deep decisions) + Qwen3-4B-Instruct-2507 Q5_K_M (`qwen3:4b-instruct-2507-q4_K_M`, fast/routine)
**Response Architecture**: Three-layer — Reflex (Lua, <1s) → Fast LLM 4B (~3-5s) → Deep LLM 8B (~7-10s)
**Organization**: Tasks grouped by user story — each story independently testable

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: User story label (US1–US5)

---

## Phase 1: Setup (Project Structure)

**Purpose**: Create all directories and base configuration files before any implementation begins

- [X] T001 Create full project directory structure: `mod/hook/lua/aibrains/`, `mod/lua/AI/`, `bridge/src/`, `server/`, `installer/`, `tests/unit/`, `tests/integration/`
- [X] T002 [P] Create `server/requirements.txt` with: `pywin32>=306`, `httpx>=0.27`, `pytest>=8.0`
- [X] T003 [P] Create `bridge/CMakeLists.txt` for x64 Windows DLL target with C++17, linking against luaplus (see `LuaAPI.h` from FAForever/FA-Binary-Patches)
- [X] T004 [P] Create `installer/config.json` with all default values per data-model.md BotConfiguration schema (model: `qwen3:8b`, poll_interval_s: 45, difficulty: normal, playstyle: balanced)
- [ ] T005 [P] Pull both Ollama models: `ollama pull qwen3:8b` and `ollama pull qwen3:4b-instruct-2507-q4_K_M` — document expected VRAM (5.0 GB + 2.9 GB) in `installer/config.json` comments

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core IPC infrastructure — MUST be complete before any user story begins

**⚠️ CRITICAL**: No user story work can begin until T006–T014 are complete

- [X] T006 Implement lock-free SPSC ring buffer (2-slot) for send/receive queues in `bridge/src/ring_buffer.h` — template struct with `push()`/`pop()` using `std::atomic`
- [X] T007 Implement Named Pipe background thread in `bridge/src/pipe_client.cpp` — `CreateFile` to `\\.\pipe\supcom_llm_bridge`, background `std::thread` for read/write loop, 3s reconnect on disconnect; never blocks caller
- [X] T008 Implement Lua function registration in `bridge/src/lua_bridge.cpp` — register `LLMBridge.Send(json)`, `LLMBridge.Receive()`, `LLMBridge.IsConnected()`, `LLMBridge.GetStatus()` per contract `contracts/lua-dll-bridge.md`
- [X] T009 Implement DLL entry point in `bridge/src/dllmain.cpp` — `DllMain` starts pipe background thread on `DLL_PROCESS_ATTACH`, stops on `DLL_PROCESS_DETACH`; `luaopen_llm_bridge` registers all Lua functions
- [X] T010 [P] Create FAF mod skeleton in `mod/mod_info.lua` — fill all required fields (uid, name, version, selectable=true, ui_only=false) per research.md section 4
- [X] T011 [P] Register custom AI brain in `mod/hook/lua/aibrains/index.lua` — append `LLM_AI` entry (key, name "LLM AI Bot (UEF)", rating 1000) to existing AI list via FAF hook pattern
- [X] T012 Create minimal `mod/lua/AI/LLMAIBrain.lua` — class inheriting from `AIBrain`, overriding `OnCreateAI()` to fork three coroutine threads: `StrategyUpdateThread`, `TacticalMicroThread`, `ChatHandlerThread` (stubs initially)
- [X] T013 [P] Implement Python asyncio bridge server entry point in `server/bridge_server.py` — `asyncio.run(main())`, creates `PipeServer`, `LLMClient`, `StateProcessor`, `DecisionLogger` instances; coordinates tasks
- [X] T014 [P] Implement Windows Named Pipe async server in `server/pipe_server.py` — use `pywin32` `win32pipe`/`win32file`; async read loop populating `asyncio.Queue`, async write from command queue; length-prefix framing per contract

**Checkpoint**: Build DLL, install mod into FAF mods folder, start `bridge_server.py` → verify pipe connects (T014 logs "Game connected")

---

## Phase 3: User Story 1 — Bot Plays a Skirmish Game (Priority: P1) 🎯 MVP

**Goal**: Bot builds UEF base, manages economy, produces units, attacks player — guided by Qwen3-8B

**Independent Test**: Start 1v1 skirmish vs "LLM AI Bot (UEF)" → bot builds factory + MEX + pgens within 60s, launches army attack within 5 min

### Implementation for User Story 1

- [X] T015 [P] [US1] Implement `mod/lua/AI/GameStateCollector.lua` — `Collect(brain)` function assembling full `GameStateSnapshot` JSON per data-model.md: economy, unit counts by tier, threats via `GetThreatAtPosition`, map control estimate, trigger event, current strategy
- [X] T016 [P] [US1] Implement `mod/lua/AI/LLMBridge.lua` — wrap DLL calls with nil-safety; `SendSnapshot(brain, event)` calls `LLMBridge.Send(json)` only when connected; `PollCommand()` calls `LLMBridge.Receive()` and returns parsed table or nil
- [X] T017 [US1] Implement `server/state_processor.py` — `build_prompt(snapshot, config, history)` producing full prompt string: system prompt with `/no_think` directive (Qwen3 thinking mode off), UEF faction context, difficulty/playstyle injection, snapshot summary, JSON schema instruction for `StrategicDecision`; target ≤800 tokens total
- [X] T018 [US1] Implement `server/llm_client.py` — `async query(prompt)` posting to Ollama `/api/generate` with `model=qwen3:8b`, `format="json"`, `stream=False`, `temperature=0.3`, `num_predict=256`; on timeout (10s) auto-retry once with `qwen3:4b-instruct-2507-q4_K_M`; return parsed `StrategicDecision` dict or `None`
- [X] T019 [US1] Implement `server/command_builder.py` — `build(llm_response)` validating `StrategicDecision` fields per data-model.md: army_composition sums to 1.0, retreat_threshold in [0.1, 0.9], chat_message ≤200 chars; fill missing optional fields with safe defaults; return validated dict
- [X] T020 [US1] Implement `server/fallback_strategy.py` — `get_command(snapshot)` rule-based: if factories<5 → build; if land_military>20 → attack; if near_base threat>50 → defend; if mass_income<5 → reclaim; always returns valid `StrategicDecision`
- [X] T021 [P] [US1] Implement `mod/lua/AI/BuildOrderUEF.lua` — UEF build templates keyed by strategy name: `land_rush` (5 land factories → T1 army), `tech_up` (T2 factory → T2 units), `turtle` (shields + T2 PDs + arty), `balanced` (mixed); each template is an ordered list of unit/structure blueprint IDs
- [X] T022 [P] [US1] Implement `mod/lua/AI/EconomyManager.lua` — `Tick(brain)` called every 30 game-ticks: detect mass stall (income<3 for 30s), detect energy stall, find idle engineers and assign reclaim or assist; expose `GetEconomySummary()` for GameStateCollector
- [X] T023 [US1] Implement `mod/lua/AI/StrategyExecutor.lua` — `Apply(brain, decision)` translating `StrategicDecision` fields into game commands: set active build template (BuildOrderUEF), issue factory queue commands, assign platoon attack direction using map markers, set retreat threshold on platoons
- [X] T024 [US1] Implement `mod/lua/AI/TacticalMicro.lua` — `TacticalMicroThread(brain)` coroutine: every 5 game-ticks find military platoons, issue attack-move toward current attack direction, retreat units below retreat_threshold HP, focus-fire on nearest enemy ACU/artillery/experimentals first
- [X] T025 [US1] Wire full 45s LLM cycle in `mod/lua/AI/LLMAIBrain.lua` `StrategyUpdateThread` — loop: collect snapshot, send via LLMBridge, `WaitSeconds(45)`, poll for command, apply via StrategyExecutor; on disconnect use FallbackAI
- [X] T026 [US1] Implement event triggers in `mod/lua/AI/LLMAIBrain.lua` — track army size each tick; on delta > -50% (army_lost), nearest enemy < 300 units (enemy_detected), mass_income < 3 for 60s (economy_stall), phase transition (phase_change): call `LLMBridge.SendSnapshot(brain, event)` immediately without waiting 45s timer

**Checkpoint**: Full 1v1 game vs bot completes. Check `llm_ai_decisions.log` for LLM queries with ≤10s latency. Bot survives >5 min and launches at least one attack.

---

## Phase 4: User Story 2 — Natural Language Chat (Priority: P2)

**Goal**: Player sends chat messages → bot understands and responds in Russian or English; bot proactively warns of threats

**Independent Test**: Send "attack north" → bot acknowledges and issues north attack command; bot sends unprompted warning when large enemy force detected

### Implementation for User Story 2

- [X] T027 [P] [US2] Implement `mod/lua/AI/ChatHandler.lua` — `ReceiveThread(brain)` coroutine hooking `SimCallbacks.ChatMessage`; filter messages directed at bot (all-chat or team-chat); push to rolling buffer (max 10); expose `GetPendingMessages()` for GameStateCollector
- [X] T028 [US2] Update `server/state_processor.py` — inject `player_chat` array into prompt context when non-empty; add language detection function (`has_cyrillic(text)` → "ru"/"en"); set `chat_language` in system prompt so LLM responds in same language
- [X] T029 [US2] Implement chat send-back pipeline — in `server/bridge_server.py`: after receiving `StrategicDecision`, if `chat_message` is non-null wrap it in `{"type":"chat","text":"..."}` pipe message; in `mod/lua/AI/ChatHandler.lua` `SendThread`: poll `LLMBridge.Receive()` for chat messages and call `SessionSync.SendChatMessage(text)` or equivalent FAF chat API
- [X] T030 [US2] Implement proactive threat warning in `mod/lua/AI/LLMAIBrain.lua` — detect `nearest_enemy_distance < 400` AND `enemy_army_size_estimate > 15`; trigger immediate snapshot with event `"enemy_detected"`; the LLM's `chat_message` field in response becomes the threat warning sent to player
- [X] T031 [US2] Add `/no_think` enforcement in `server/state_processor.py` system prompt — ensure system prompt begins with `/no_think` (Qwen3 directive to disable chain-of-thought) so responses are fast and don't leak internal reasoning into `chat_message`

**Checkpoint**: In game chat, type "что строишь?" → bot replies in Russian within 10s. Large enemy force approaches → bot warns unprompted.

---

## Phase 5: User Story 3 — Allied Teammate Mode (Priority: P2)

**Goal**: Bot behaves like a human teammate — instant reflex reactions (<3s) to ally threats + LLM-driven coordination + proactive chat

**Independent Test**: 2v2 vs built-in AI — enemy sends bombers to player's base → bot's interceptors scramble within 3 seconds WITHOUT player asking; enemy ground attack → bot diverts army within 5 seconds; bot notifies in chat what it's doing

### Layer 1 — Reflex (pure Lua, no LLM, < 3 seconds)

- [X] T032 [P] [US3] Implement `mod/lua/AI/AllyMonitor.lua` — coroutine running every 5 game-ticks; reads `AllyState` per data-model.md entity 8: ally army index, base position, `GetThreatAtPosition` at ally base (air-specific and ground), ally unit count delta, ally mass income; exposes `GetAllyState()` for GameStateCollector
- [X] T033 [P] [US3] Implement `mod/lua/AI/ReflexLayer.lua` — `EvaluateAndAct(brain, allyState)` called every 5 ticks; evaluates four triggers: (1) `air_threat_near_base > 15` → `SCRAMBLE_INTERCEPTORS`: issue `IssuePatrol(interceptors, ally_base_pos)` immediately; (2) `base_threat > 30` → `SEND_GROUND_SUPPORT`: redirect nearest idle land platoon to `MoveToLocation(ally_base_pos)`; (3) `losing_army_fast` → `SEND_ARMY_SUPPORT`: send 40% of own attack army to ally; (4) ally mass stall 30s AND own surplus > 300 → `GiveResource`; each trigger fires at most once per 20 ticks (cooldown) to avoid spam
- [X] T034 [US3] Integrate ReflexLayer into `mod/lua/AI/LLMAIBrain.lua` ally-mode — fork `ReflexThread` coroutine in `OnCreateAI` when mode="ally"; after each reflex action, immediately send snapshot to Python with `trigger_event = "ally_under_attack"` / `"ally_air_threat"` / `"ally_economy_stall"` so LLM provides chat commentary

### Layer 2 — Fast LLM routing (Qwen3-4B-2507, ~3-5 sec)

- [X] T035 [US3] Implement `server/llm_router.py` — `route(snapshot) -> model_tag` decision: `trigger_event in ["player_chat","phase_change","army_lost"]` AND `len(player_chat) > 0 AND is_complex(chat)` → `qwen3:8b`; all ally events + periodic → `qwen3:4b-instruct-2507-q4_K_M`; `is_complex()` heuristic: message contains strategy words (атака/attack/plan/стратег) or length > 60 chars
- [X] T036 [US3] Update `server/llm_client.py` to accept `model_tag` parameter — `async query(prompt, model_tag)` posts to Ollama with the specified model; update `bridge_server.py` to call `llm_router.route(snapshot)` and pass result to `llm_client.query()`
- [X] T037 [US3] Reduce baseline poll interval — update `server/bridge_server.py` periodic timer from 45s to 20s using `qwen3:4b-instruct-2507-q4_K_M`; add separate 60s deep-scan timer using `qwen3:8b` for full strategic review

### Layer 3 — Proactive communication and coordination

- [X] T038 [P] [US3] Implement territory avoidance in `mod/lua/AI/LLMAIBrain.lua` `OnCreateAI` ally-mode branch — get player ACU start position; store as `player_base_center`; in `BuildOrderUEF` restrict bot build zone to own half of map using `GetMapSize()` and distance check
- [X] T039 [US3] Update `server/state_processor.py` ally system prompt variant — inject `AllyState` summary: air threat, ground threat, army losses, mass stall status; add instruction: "You are a human teammate. Describe what you just did and why in chat_message. Be brief and natural (e.g. 'Вижу авиацию у тебя — перехватчики летят'). Never be silent when reacting to threats."
- [X] T040 [US3] Update `server/state_processor.py` for proactive sharing context — when `ally.mass_income < 3`: add to prompt "Ally is mass-starved. Consider auto-sharing if you have surplus."

**Checkpoint**: 2v2 vs built-in AIs. Enemy sends T1 air → interceptors scramble within 3 seconds, bot writes in chat. Enemy ground attack on player → army diverts within 5 seconds. Player types "отправь массу" → share within 2 seconds (reflex, no LLM).

---

## Phase 6: User Story 4 — Local Setup and Configuration (Priority: P3)


**Goal**: Guided setup gets all components installed and running within 30 minutes on a clean Windows PC

**Independent Test**: Run `install.ps1` on PC with SupCom:FA installed → all components installed, `bridge_server.py` starts, mod appears in FAF

### Implementation for User Story 4

- [X] T041 [P] [US4] Implement full config validation in `server/config.py` — load `config.json`, validate all fields against updated BotConfiguration schema (including `poll_interval_fast_s=20`, `poll_interval_deep_s=60`), provide typed dataclass `BotConfig`; expose `load_config(path) -> BotConfig`
- [X] T042 [P] [US4] Implement `installer/install.ps1` guided setup script — steps: 1) verify game path exists, 2) check FAF client installed, 3) check Ollama running, 4) pull `qwen3:8b` + `qwen3:4b-instruct-2507-q4_K_M`, 5) copy mod to `%APPDATA%\FAForever\mods\supcom-llm-ai-bot\`, 6) copy DLL to mod bin folder, 7) write config.json; print colored status per step
- [X] T043 [US4] Add GPU VRAM detection to `installer/install.ps1` — query `nvidia-smi --query-gpu=memory.total --format=csv,noheader`; if VRAM < 6GB disable `qwen3:8b` and use only `qwen3:4b-instruct-2507-q4_K_M`; if no GPU set `poll_interval_fast_s=30`, `poll_interval_deep_s=120`
- [X] T044 [US4] Create `installer/start_bridge.ps1` launch script — check Ollama running (HTTP ping to both models), start `bridge_server.py` with config path arg, wait for pipe ready message, print "Bridge ready — start your game in FAF"

**Checkpoint**: Fresh PC: run `install.ps1` → 30 min or less → start game → LLM AI Bot appears in AI dropdown and connects.

---

## Phase 7: User Story 5 — Adjustable Difficulty and Playstyle (Priority: P3)

**Goal**: Easy/Normal/Hard difficulty + 5 playstyle presets (rush/balanced/turtle/air/naval) produce measurably different behavior

**Independent Test**: Easy vs Hard game → Hard bot builds base 2x faster; Rush vs Turtle → different army composition visible in decision log

### Implementation for User Story 5

- [X] T045 [P] [US5] Implement difficulty modifiers in `server/state_processor.py` — `easy`: "Play conservatively, react slowly, make occasional suboptimal decisions"; `normal`: neutral; `hard`: "Optimize every decision, react instantly, maximize economy"; inject into system prompt; also configure `llm_router.py`: easy always uses 4B, hard always uses 8B
- [X] T046 [P] [US5] Implement 5 playstyle system prompt variants in `server/state_processor.py` `PLAYSTYLE_PROMPTS` dict — `rush`: "Prioritize early T1 attack within 4 minutes"; `balanced`: "Balance economy and military"; `turtle`: "Maximize base defense before attacking"; `air`: "Prioritize air factory and gunships"; `naval`: "Expand to water, build destroyers"
- [X] T047 [US5] Implement difficulty-based reflex speed in `mod/lua/AI/ReflexLayer.lua` — read difficulty from config handshake; `easy`: reflex cooldown = 40 ticks, only shares mass (no combat reflexes); `normal`: cooldown = 20 ticks, all reflexes; `hard`: cooldown = 5 ticks, additionally pre-positions interceptors near ally proactively

**Checkpoint**: Start easy Rush game → bot attacks within 3 min with massed T1 tanks. Start hard Turtle game → bot builds 10+ PDs and shields before any army push.

---

## Phase 8: Observability — Debug Overlay and Logging

**Purpose**: Decision logging (FR-016) and UI overlay (FR-017) — cross-cutting, serves all user stories

- [X] T048 [P] Implement `server/decision_logger.py` — `DecisionLogger` class writing `DecisionLogEntry` JSONL to `%ProgramData%\FAForever\logs\llm_ai_decisions.log`; each entry: ISO timestamp, game_tick, trigger_event, model_used (4B or 8B), condensed snapshot, prompt_tokens, response_tokens, response_latency_ms, decision, fallback_used, reflex_actions_this_tick
- [X] T049 [P] Implement `server/save_state.py` — `SaveState` class: `save(state: BotSaveState, save_name: str)` writes to `%APPDATA%\FAForever\saves\llm_bot_state_{save_name}.json`; `load(save_name)` restores StrategicDecision + conversation history; hook into game save/load events via pipe message type `"save"/"load"`
- [X] T050 Implement UI debug overlay in `mod/lua/UI/LLMOverlay.lua` — FAF UI layer panel: toggle with Ctrl+Shift+D; display: current strategy name, last reasoning (200 chars), active model (4B/8B), LLM connection indicator, countdown to next query, reflex layer status (last reflex action + when); read from `UserSync` shared table updated by sim layer
- [X] T051 Hook save/load state into `mod/lua/AI/LLMAIBrain.lua` — on `OnSave()`: send save snapshot to Python; on `OnPostLoad()`: send load request so Python restores full context before next LLM query

---

## Phase 9: Polish and Cross-Cutting Concerns

- [X] T052 [P] Implement map marker fallback in `mod/lua/AI/LLMAIBrain.lua` — on game start check if map has AI markers; if empty generate minimal markers from `GetAllMassPoints()` and start positions
- [X] T053 [P] Implement automatic model timeout fallback in `server/llm_client.py` — if `qwen3:8b` response > 10s, immediately retry with `qwen3:4b-instruct-2507-q4_K_M`; after 3 consecutive 8B timeouts, route all traffic to 4B for remainder of session with warning in overlay
- [X] T054 [P] Write integration smoke test in `tests/integration/test_pipe_roundtrip.py` — start bridge_server as subprocess, send mock snapshot via pipe, verify StrategicDecision returned within 15s; mock Ollama timeout, assert fallback to 4B fires; send `"ally_air_threat"` event, assert 4B model selected by router
- [X] T055 Update `CLAUDE.md` with three-layer architecture, both model tags, reflex triggers, poll intervals (20s/60s), pipe name, log paths, development workflow

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — **blocks all user stories**
- **Phase 3 (US1)**: Depends on Phase 2 completion — core MVP
- **Phase 4 (US2)**: Depends on Phase 2; integrates with US1 ChatHandler
- **Phase 5 (US3)**: Depends on Phase 2; integrates with US1 + US2 chat parsing
- **Phase 6 (US4)**: Depends on Phase 2; independent of US1–US3
- **Phase 7 (US5)**: Depends on Phase 3 (state_processor.py exists)
- **Phase 8 (Observability)**: Depends on Phase 3 (pipeline exists); T045 overlay can parallel with Phase 4
- **Phase 9 (Polish)**: Depends on Phase 3–8 completion

### Within User Story 1

```
T015 [P] ─┐
T016 [P] ─┤
T021 [P] ─┤─► T023 ─► T025 ─► T026
T022 [P] ─┤
T017 ──────┤
T018 ──────┤─► T019 ─► T020
T024 (independent, start anytime after T012)
```

### Parallel Opportunities

**Phase 1**: T002, T003, T004, T005 all parallel after T001
**Phase 2**: T006→T007→T008→T009 (DLL chain, sequential); T010, T011 parallel; T013, T014 parallel
**Phase 3**: T015, T016, T021, T022 all parallel; T017→T018→T019→T020 sequential; T024 independent
**Phase 4**: T027 parallel with T028; T029→T030 sequential
**Phase 8**: T043, T044 parallel; T045 independent; T046 after T044

---

## Parallel Example: User Story 1 (Phase 3)

```bash
# Start these in parallel immediately after Phase 2:
Task A: T015 — GameStateCollector.lua
Task B: T016 — LLMBridge.lua
Task C: T021 — BuildOrderUEF.lua
Task D: T022 — EconomyManager.lua
Task E: T017 → T018 → T019 → T020  (server pipeline, sequential)
Task F: T024 — TacticalMicro.lua (independent)

# After A+B+C+D+E complete:
Task G: T023 — StrategyExecutor.lua
# After G:
Task H: T025 → T026 — Wire full cycle in LLMAIBrain.lua
```

---

## Implementation Strategy

### MVP (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational — build DLL, install mod, start Python server
3. Complete Phase 3: User Story 1 — full LLM-driven UEF gameplay
4. **STOP and VALIDATE**: Play 3 test games, review `llm_ai_decisions.log`
5. Bot plays a complete game → MVP achieved ✅

### Incremental Delivery

1. Phase 1+2 → DLL connects ✅
2. Phase 3 (US1) → Bot plays 1v1 game with LLM strategy ✅
3. Phase 4 (US2) → Add chat ✅
4. Phase 5 (US3) → Add ally mode ✅
5. Phase 8 → Add overlay + logging ✅
6. Phase 6+7 → Add installer + difficulty ✅
7. Phase 9 → Polish ✅

---

## Notes

- [P] tasks = different files, no shared write dependencies — safe to run concurrently
- [Story] label maps task to spec.md user story for traceability
- Qwen3 `/no_think` directive MUST be in every system prompt (T017, T028, T035, T040) — without it Qwen3-8B adds slow chain-of-thought tokens before JSON output
- DLL must be compiled as x64 Release (game is 64-bit); Debug builds link to MSVCRT debug DLL not present on user machines
- FAF mod hooks append to existing files — never overwrite; test hook order carefully (T011)
- All pipe messages use 4-byte LE length prefix — both DLL (T007) and Python (T014) must match exactly
- Decision log (T043) is the primary debugging tool — review after every test game
