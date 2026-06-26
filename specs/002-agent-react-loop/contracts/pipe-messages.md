# Pipe Message Contracts

**Protocol**: Windows Named Pipe `\\.\pipe\supcom_llm_bridge`
**Framing**: 4-byte LE uint32 length prefix + UTF-8 JSON payload

## Existing Messages (unchanged)

### Lua → Python: Game State Snapshot
```json
{"type": "snapshot", "data": {
    "tick": 1200,
    "game_time_s": 120,
    "economy": {...},
    "units": {...},
    "threats": {...},
    ...
}}
```

### Python → Lua: Command (action tool results batch)
```json
{"type": "command", "data": {
    "tool_calls": [
        {"name": "attack", "args": {"unit_type": "land", "force_size": "full"}},
        {"name": "chat", "args": {"message": "Attacking!"}}
    ],
    "_model_used": "qwen3.5:9b",
    "_latency_ms": 4800
}}
```

## New Messages

### Python → Lua: Observation Request
Sent during a ReAct iteration when the LLM calls an observation tool.

```json
{"type": "observation_request", "request_id": "obs_001", "tool": "get_enemy_army", "args": {}}
```

```json
{"type": "observation_request", "request_id": "obs_002", "tool": "get_threat_at", "args": {"position": "enemy_base"}}
```

```json
{"type": "observation_request", "request_id": "obs_003", "tool": "get_mass_points", "args": {}}
```

```json
{"type": "observation_request", "request_id": "obs_004", "tool": "get_my_factories", "args": {}}
```

```json
{"type": "observation_request", "request_id": "obs_005", "tool": "get_map_control", "args": {}}
```

### Lua → Python: Observation Result
Returned synchronously after processing an observation request.

```json
{"type": "observation_result", "request_id": "obs_001", "tool": "get_enemy_army", "success": true, "data": {
    "land": 25, "air": 3, "navy": 0, "total": 28, "has_experimentals": false
}}
```

Error case:
```json
{"type": "observation_result", "request_id": "obs_002", "tool": "get_threat_at", "success": false, "error": "no enemy base found"}
```

### Python → Lua: Action Execute (within ReAct loop)
Same as command but sent immediately for feedback, not batched.

```json
{"type": "action_execute", "request_id": "act_001", "tool": "attack", "args": {"unit_type": "land", "force_size": "full"}}
```

### Lua → Python: Action Result
```json
{"type": "action_result", "request_id": "act_001", "tool": "attack", "success": true, "data": {
    "units_sent": 15, "unit_type": "land"
}}
```

## Message Flow (ReAct Cycle)

```
Lua                          Python                        LLM (Ollama)
 |                              |                              |
 |--snapshot------------------->|                              |
 |                              |--messages+tools------------->|
 |                              |<--tool_calls(get_enemy)------|
 |<--observation_request--------|                              |
 |--observation_result--------->|                              |
 |                              |--messages+tool_result------->|
 |                              |<--tool_calls(attack,chat)----|
 |<--action_execute(attack)-----|                              |
 |--action_result(15 units)---->|                              |
 |<--action_execute(chat)-------|                              |
 |--action_result(sent)-------->|                              |
 |                              |--messages+tool_results------>|
 |                              |<--tool_calls(noop)-----------|
 |                              |  [loop terminates]           |
```
