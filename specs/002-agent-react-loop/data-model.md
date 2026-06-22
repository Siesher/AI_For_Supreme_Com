# Data Model: Agent ReAct Loop

**Date**: 2026-03-29
**Feature**: 002-agent-react-loop

## Entities

### DecisionCycle

A complete ReAct loop from trigger to termination.

| Field | Type | Description |
|-------|------|-------------|
| cycle_id | int | Monotonic counter, per game session |
| trigger | string | What started this cycle: "periodic", "event", "chat" |
| game_time_s | int | Game time at cycle start |
| iterations | list[Iteration] | 1–3 iterations of observe/act |
| total_latency_ms | int | Wall-clock time for entire cycle |
| model_used | string | LLM model tag used |
| outcome_summary | string | Brief text: what happened as a result |

### Iteration

One LLM query + tool execution within a cycle.

| Field | Type | Description |
|-------|------|-------------|
| iteration_num | int | 1-indexed within the cycle |
| messages_sent | list[dict] | Messages sent to LLM (for logging) |
| tool_calls | list[ToolCall] | Tools the LLM requested |
| tool_results | list[ToolResult] | Results returned from execution |
| latency_ms | int | Time for this single iteration |

### ToolCall

A single tool invocation requested by the LLM.

| Field | Type | Description |
|-------|------|-------------|
| name | string | Tool name (e.g., "attack", "get_enemy_army") |
| args | dict | Arguments passed by the LLM |
| is_observation | bool | True if read-only observation tool |

### ToolResult

Result of executing a tool call.

| Field | Type | Description |
|-------|------|-------------|
| tool_name | string | Which tool was called |
| success | bool | Whether execution succeeded |
| data | dict | Result payload (tool-specific) |
| error | string | null | Error message if failed |

### DecisionMemory

Rolling buffer of past cycles.

| Field | Type | Description |
|-------|------|-------------|
| max_entries | int | Configurable, default 10 |
| entries | list[DecisionSummary] | Oldest-first |

### DecisionSummary

Compact representation of a past cycle for inclusion in LLM context.

| Field | Type | Description |
|-------|------|-------------|
| cycle_id | int | Reference to DecisionCycle |
| game_time_s | int | When this happened |
| trigger | string | What caused the cycle |
| observations | list[str] | Brief: "enemy_army: 25 land, 3 air" |
| actions | list[str] | Brief: "defend(radius=80)" |
| outcome | string | Brief: "15 units rallied, base held" |

## State Transitions

```
IDLE → CYCLE_START (trigger received)
CYCLE_START → ITERATION (build messages, query LLM)
ITERATION → EXECUTING_TOOLS (LLM returned tool_calls)
EXECUTING_TOOLS → COLLECTING_RESULTS (tools dispatched to Lua/local)
COLLECTING_RESULTS → ITERATION (results fed back, iteration < max)
COLLECTING_RESULTS → CYCLE_COMPLETE (noop called, or iteration >= max)
CYCLE_COMPLETE → MEMORY_UPDATE (summarize and store)
MEMORY_UPDATE → IDLE
```

## Observation Tool Return Schemas

### get_enemy_army
```json
{"land": 25, "air": 3, "navy": 0, "total": 28, "has_experimentals": false}
```

### get_threat_at
```json
{"overall": 45.0, "land": 30.0, "air": 15.0, "structures": 10.0}
```

### get_mass_points
```json
{"total": 20, "controlled": 8, "uncontrolled": 7, "contested": 5}
```

### get_my_factories
```json
{"t1_land": 3, "t2_land": 1, "t3_land": 0, "t1_air": 1, "idle": 2, "building": 3}
```

### get_map_control
```json
{"pct": 45, "zones": [{"name": "north", "control": "enemy"}, {"name": "center", "control": "contested"}, {"name": "south", "control": "own"}]}
```

## Action Tool Result Schemas

### attack
```json
{"success": true, "units_sent": 15, "unit_type": "land"}
```

### defend
```json
{"success": true, "units_assigned": 12, "radius": 80}
```

### scout
```json
{"success": true, "direction": "south", "scout_type": "land"}
```

### build_units
```json
{"success": true, "priority": "air", "urgency": "high"}
```

### reclaim
```json
{"success": true, "engineers_sent": 3, "area": "near_base"}
```

### set_strategy
```json
{"success": true, "strategy": "turtle", "previous": "balanced"}
```

### chat
```json
{"success": true, "message": "Defending!", "recipients": "allies"}
```
