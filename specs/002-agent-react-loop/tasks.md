# Tasks: Agent ReAct Loop

**Input**: Design documents from `/specs/002-agent-react-loop/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not explicitly requested — test tasks omitted. Validation via decision log analysis.

**Organization**: Tasks grouped by user story for independent implementation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3)
- Exact file paths included in all tasks

---

## Phase 1: Setup

**Purpose**: Config and shared infrastructure for the ReAct system

- [x] T001 Add `react_max_iterations`, `react_cycle_timeout_s`, `decision_memory_size` fields to `installer/config.json`
- [x] T002 [P] Add observation tool definitions (`get_enemy_army`, `get_threat_at`, `get_mass_points`, `get_my_factories`, `get_map_control`) to `server/tools.py`
- [x] T003 [P] Create `server/decision_memory.py` — DecisionMemory class with rolling buffer, add/summarize/to_prompt methods

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Pipe protocol support for request-response within a decision cycle. MUST complete before any user story.

- [x] T004 Add `send_and_wait_response(msg, timeout)` method to `server/pipe_server.py` — sends a message and blocks until a response of matching `request_id` arrives
- [x] T005 Add observation request dispatcher to `mod/lua/AI/LLMAIBrain.lua` — in `LLMDecisionThread`, after receiving a pipe message, check if `type == "observation_request"` and dispatch to handler, send `observation_result` back via `LLMBridge.Send`
- [x] T006 Create `mod/lua/AI/ObservationHandlers.lua` — module with handler functions for each observation tool (skeleton with `get_enemy_army` returning `{land=0, air=0}` stub)
- [x] T007 Deploy `ObservationHandlers.lua` to `C:/FAFData/mods/supcom-llm-ai-bot/lua/AI/ObservationHandlers.lua`

**Checkpoint**: Pipe can send observation requests and receive results. Lua dispatches to handler stubs.

---

## Phase 3: User Story 1 — Agent Queries Game State Before Acting (Priority: P1) MVP

**Goal**: LLM calls observation tools to gather intel before issuing action commands.

**Independent Test**: Start a game, trigger a decision cycle. Decision log shows observation tool call → result → action tool call. Confirm observation data influenced the action.

### Implementation for User Story 1

- [x] T008 [P] [US1] Implement `get_enemy_army` handler in `mod/lua/AI/ObservationHandlers.lua` — iterate enemy brains, count units by category (land/air/navy/experimental), return totals
- [x] T009 [P] [US1] Implement `get_threat_at` handler in `mod/lua/AI/ObservationHandlers.lua` — call `brain:GetThreatAtPosition(pos, radius, true, type)` for overall/land/air/structures
- [x] T010 [P] [US1] Implement `get_mass_points` handler in `mod/lua/AI/ObservationHandlers.lua` — scan map markers, classify as controlled/uncontrolled/contested
- [x] T011 [P] [US1] Implement `get_my_factories` handler in `mod/lua/AI/ObservationHandlers.lua` — count factories by tech level, identify idle vs building
- [x] T012 [P] [US1] Implement `get_map_control` handler in `mod/lua/AI/ObservationHandlers.lua` — sample threat grid in 4 quadrants, compute control percentage
- [x] T013 [US1] Create `server/react_loop.py` — `ReactLoop` class with `async run_cycle(snapshot, messages, config)` method: query LLM → classify tool_calls as observation vs action → execute observations via pipe `send_and_wait_response` → append tool results to messages → re-query LLM (max iterations from config)
- [x] T014 [US1] Update `server/llm_client.py` — `query()` must accept a full messages list (not build internally), pass accumulated conversation to Ollama `/api/chat`
- [x] T015 [US1] Update `server/bridge_server.py` — replace direct `llm_client.query()` call in decision loop with `react_loop.run_cycle()`, pass pipe_server for observation dispatch
- [x] T016 [US1] Update `server/state_processor.py` — update system prompt to instruct LLM: "You may call observation tools first to gather information. Call action tools to execute decisions. Call noop when done."
- [x] T017 [US1] Deploy updated Lua files to `C:/FAFData/mods/supcom-llm-ai-bot/lua/AI/`

**Checkpoint**: Agent can observe → decide → act in a single cycle. Decision log shows multi-tool flow.

---

## Phase 4: User Story 2 — Action Tool Feedback Loop (Priority: P1)

**Goal**: Action tool execution results feed back to LLM for follow-up decisions within the same cycle.

**Independent Test**: Trigger an attack. Decision log shows: action call → execution result → follow-up tool call based on result. Loop terminates within 3 iterations.

### Implementation for User Story 2

- [x] T018 [US2] Update action tool handlers in `mod/lua/AI/LLMAIBrain.lua` — each Tool* function (`ToolAttack`, `ToolDefend`, `ToolScout`, etc.) must return a result dict `{success, units_sent, ...}` instead of just logging
- [x] T019 [US2] Add `action_execute` / `action_result` message handling to `mod/lua/AI/LLMAIBrain.lua` — when pipe receives `type == "action_execute"`, dispatch to Tool* function and send `action_result` back
- [x] T020 [US2] Update `server/react_loop.py` — after executing action tools via pipe, collect results, format as `{"role": "tool", "content": json_result}` messages, append to conversation, re-query LLM if iterations remain
- [x] T021 [US2] Add loop termination logic to `server/react_loop.py` — stop on: `noop` call, no tool_calls, max iterations, cycle timeout exceeded
- [x] T022 [US2] Update `server/decision_logger.py` — log full ReAct cycle: all iterations, tool calls, tool results, total latency
- [x] T023 [US2] Deploy updated Lua files to `C:/FAFData/mods/supcom-llm-ai-bot/lua/AI/`

**Checkpoint**: Full ReAct loop works: observe → act → feedback → follow-up → terminate. Decision log captures all iterations.

---

## Phase 5: User Story 3 — Decision Memory Across Cycles (Priority: P2)

**Goal**: Rolling history of past decisions with outcomes included in LLM context. Agent adapts based on prior results.

**Independent Test**: Play 5+ minutes. Decision log shows later decisions referencing earlier outcomes. Memory buffer stays within configured limit.

### Implementation for User Story 3

- [x] T024 [US3] Implement `DecisionMemory.add_cycle()` in `server/decision_memory.py` — accepts a completed DecisionCycle, summarizes to DecisionSummary, appends to buffer (evict oldest if full)
- [x] T025 [US3] Implement `DecisionMemory.to_messages()` in `server/decision_memory.py` — format memory as a concise text block for inclusion in system prompt (e.g., `"[Cycle #5, 4:30] Observed: enemy 25 land → Action: defend(80) → Result: base held"`)
- [x] T026 [US3] Update `server/state_processor.py` — inject decision memory block into system prompt via `build_messages()`, accept `DecisionMemory` instance as parameter
- [x] T027 [US3] Update `server/bridge_server.py` — instantiate `DecisionMemory` at startup, pass to `react_loop.run_cycle()`, call `memory.add_cycle()` after each completed cycle
- [x] T028 [US3] Update `server/react_loop.py` — after cycle completes, build a `DecisionSummary` from the iteration data and return it alongside the final tool_calls

**Checkpoint**: Agent has session memory. Decisions in the second half of a game reference earlier outcomes.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Robustness, edge cases, and latency optimization

- [x] T029 [P] Add observation timeout handling in `server/react_loop.py` — if pipe `send_and_wait_response` times out, skip observation and proceed with available data
- [x] T030 [P] Add cycle timeout guard in `server/react_loop.py` — check elapsed time before each iteration, abort and fall back if > `react_cycle_timeout_s`
- [x] T031 [P] Update fallback strategy in `server/bridge_server.py` — if ReAct loop fails entirely, use existing `_fallback_to_tools()` / `_minimal_fallback()`
- [x] T032 Update `CLAUDE.md` — document ReAct loop architecture, new message types, observation tools, memory system

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on T001–T003 (config + tool defs + memory class)
- **US1 (Phase 3)**: Depends on Phase 2 (pipe request-response + observation dispatch)
- **US2 (Phase 4)**: Depends on US1 (ReAct loop exists, observation flow works)
- **US3 (Phase 5)**: Depends on US2 (feedback loop produces outcomes to remember)
- **Polish (Phase 6)**: Depends on US1–US3

### User Story Dependencies

- **US1 (P1)**: After Foundational — core observation flow
- **US2 (P1)**: After US1 — extends loop with action feedback (shares `react_loop.py`)
- **US3 (P2)**: After US2 — adds memory on top of completed loop

### Parallel Opportunities

**Phase 1**: T001, T002, T003 all modify different files → run in parallel
**Phase 2**: T004 (Python) and T005+T006 (Lua) modify different layers → partial parallel
**Phase 3 (US1)**: T008–T012 all implement different handlers in same Lua file → sequential within file, but can parallel with T013 (Python)
**Phase 4 (US2)**: T018+T019 (Lua) parallel with T020+T021 (Python)

---

## Parallel Example: User Story 1

```
# Lua observation handlers (T008-T012) — sequential (same file):
Task: "Implement get_enemy_army handler in mod/lua/AI/ObservationHandlers.lua"
Task: "Implement get_threat_at handler in mod/lua/AI/ObservationHandlers.lua"
...

# Python ReAct engine (T013) — parallel with Lua work:
Task: "Create server/react_loop.py — ReactLoop class"

# After both complete:
Task: "Update server/bridge_server.py — wire ReactLoop into decision loop"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundational (T004–T007)
3. Complete Phase 3: US1 — Observation tools (T008–T017)
4. **STOP and VALIDATE**: Start a game, check decision log for observation → action flow
5. If working: proceed to US2

### Incremental Delivery

1. Setup + Foundational → pipe supports request-response
2. US1 → agent observes before acting → **MVP ready**
3. US2 → agent gets feedback on actions → full ReAct loop
4. US3 → agent remembers past decisions → session learning
5. Polish → robust edge case handling

---

## Notes

- All Lua code must use Lua 5.0 syntax (no `#`, no `goto`, use `table.getn`)
- All pipe messages use 4-byte LE length prefix + UTF-8 JSON
- Observation handlers must complete in < 200ms (1-2 sim ticks)
- ReAct loop bounded at 3 iterations × ~5s = ~15s typical, 30s max
- Decision memory: 10 entries × ~100 tokens ≈ 1000 tokens (fits in 32k context)
- Deploy Lua files to `C:/FAFData/mods/supcom-llm-ai-bot/lua/AI/` after each phase
