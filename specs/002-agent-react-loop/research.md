# Research: Agent ReAct Loop

**Date**: 2026-03-29
**Feature**: 002-agent-react-loop

## R1: Multi-turn Tool Calling in Ollama

**Decision**: Use Ollama `/api/chat` with `tool` role messages for multi-turn conversations.

**Rationale**: Tested locally with Qwen 3.5:9b. The model correctly:
1. Receives prior `assistant` message with `tool_calls`
2. Receives `tool` role message with JSON result
3. Produces follow-up tool calls based on the observation

Format verified:
```json
{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_enemy", "arguments": {}}}]}
{"role": "tool", "content": "{\"land\": 25, \"air\": 3}"}
```

Latency: ~5s per iteration (warm model), so 3 iterations ≈ 15s total — well within 30s budget.

**Alternatives considered**:
- Inline observation results in user message → loses native tool-call structure, model doesn't track which tool returned what
- Separate API calls per observation → more HTTP overhead, harder to correlate
- OpenAI-compatible endpoint → Ollama's native /api/chat has better tool support

## R2: Pipe Protocol for Observation Queries

**Decision**: Reuse existing named pipe with a new message type `observation_request` / `observation_result`.

**Rationale**: Current pipe protocol is:
- Lua → Python: `{"type": "snapshot", "data": {...}}`
- Python → Lua: `{"type": "command", "data": {...}}`

For observations, add:
- Python → Lua: `{"type": "observation_request", "tool": "get_enemy_army", "args": {...}}`
- Lua → Python: `{"type": "observation_result", "tool": "get_enemy_army", "data": {...}}`

The pipe is already bidirectional (LLMBridge.Send/Receive). Lua polls with `LLMBridge.Receive()` in the decision thread, so it can handle interleaved observation requests during the same cycle.

**Key constraint**: Lua sim thread runs at 10 ticks/s. An observation query must complete within 1-2 ticks (100-200ms). All FA APIs used (GetThreatAtPosition, GetListOfUnits, GetUnitsAroundPoint) are synchronous and fast (<1ms each).

**Alternatives considered**:
- Separate pipe for observations → unnecessary complexity, only one consumer
- HTTP callback from Lua → Lua has no HTTP client in sim layer
- Pre-compute all observations in snapshot → defeats the purpose of on-demand queries

## R3: Observation Tool Definitions

**Decision**: 5 observation tools, each maps to existing FA Lua APIs.

| Tool | FA API | Return |
|------|--------|--------|
| `get_enemy_army` | `brain:GetListOfUnits(cats, false, false)` per enemy brain | `{land, air, navy, total, composition}` |
| `get_threat_at` | `brain:GetThreatAtPosition(pos, radius, true, type)` | `{overall, land, air, structures}` |
| `get_mass_points` | `ScenarioUtils.GetMarkers()` filtered by type | `{total, controlled, uncontrolled, contested}` |
| `get_my_factories` | `brain:GetListOfUnits(FACTORY)` | `{t1, t2, t3, building, idle}` |
| `get_map_control` | Grid sampling of `GetThreatAtPosition` | `{pct, zones: [{name, control}]}` |

**Alternatives considered**:
- More granular tools (get_t1_units, get_t2_units) → too many tools, LLM gets confused
- Fewer tools (just get_full_state) → defeats the purpose of selective observation

## R4: ReAct Loop Termination

**Decision**: Terminate on: `noop` call, no tool calls, max iterations reached (default: 3), or cycle timeout (default: 30s).

**Rationale**: Need bounded latency. 3 iterations × ~5s/iter = 15s typical. Budget 30s for slow responses. On final iteration, execute tools but don't re-query LLM.

**Alternatives considered**:
- Unbounded loop with timeout only → risk of infinite observe loops
- 1 iteration only (no ReAct) → loses the core feature
- 5 iterations → too slow for real-time game

## R5: Decision Memory Format

**Decision**: Rolling buffer of last 10 `DecisionSummary` objects, serialized into the system prompt.

Format per entry:
```
[Cycle #12, 8:30] Trigger: periodic
  Observed: enemy_army={land:25} → Decided: defend(radius=80)
  Result: 15 units rallied. Next cycle: enemy retreated.
```

**Rationale**: 10 entries × ~100 tokens each = ~1000 tokens. Qwen 3.5:9b has 32k context. System prompt + state + memory comfortably fits.

**Alternatives considered**:
- Full conversation history → context explosion, 32k limit hit in ~20 cycles
- Summary-only (no tool details) → loses actionable information
- External vector store → over-engineered for single-session memory

## R6: Observation vs Action Tool Separation

**Decision**: Observation tools are read-only (return data, no side effects). Action tools mutate game state (return execution results). The LLM can freely mix both in any iteration.

**Rationale**: Clean separation allows the ReAct loop to:
1. Execute observation tools → collect results → feed back immediately (no pipe round-trip for game commands)
2. Execute action tools → collect results → feed back as confirmation
3. The LLM naturally does: observe → observe → act → observe result → done

**Key insight**: Observation tools are resolved server-side via a pipe request-response. Action tools are buffered and sent as a batch to Lua after the loop completes. This avoids partial execution of multi-step plans.

**Revision**: Actually, action tools should also execute immediately and return results within the loop — otherwise the feedback is meaningless. Execute everything immediately.
