# Ollama /api/chat Multi-Turn Tool Contract

**Endpoint**: `POST http://localhost:11434/api/chat`

## Single Iteration (current system)

```json
{
  "model": "qwen3.5:9b",
  "messages": [
    {"role": "system", "content": "/no_think\n...system prompt..."},
    {"role": "user", "content": "...game state..."}
  ],
  "tools": [...tool definitions...],
  "stream": false,
  "think": false,
  "options": {"temperature": 0.3, "num_predict": 300}
}
```

## Multi-Turn ReAct (new)

After iteration 1 returns tool_calls, append assistant + tool messages and re-query:

```json
{
  "model": "qwen3.5:9b",
  "messages": [
    {"role": "system", "content": "/no_think\n...system prompt with decision memory..."},
    {"role": "user", "content": "...game state snapshot..."},
    {"role": "assistant", "content": "", "tool_calls": [
      {"function": {"name": "get_enemy_army", "arguments": {}}}
    ]},
    {"role": "tool", "content": "{\"land\":25,\"air\":3,\"total\":28}"},
    {"role": "assistant", "content": "", "tool_calls": [
      {"function": {"name": "attack", "arguments": {\"unit_type\":\"land\",\"force_size\":\"full\"}}},
      {"function": {"name": "chat", "arguments": {\"message\":\"Attacking!\"}}}
    ]},
    {"role": "tool", "content": "{\"success\":true,\"units_sent\":15}"},
    {"role": "tool", "content": "{\"success\":true,\"message\":\"Attacking!\"}"}
  ],
  "tools": [...all tools (observation + action)...],
  "stream": false,
  "think": false,
  "options": {"temperature": 0.3, "num_predict": 300}
}
```

## Tool Definitions (full list for ReAct)

Observation tools (read-only):
- `get_enemy_army` — enemy unit composition
- `get_threat_at` — threat level at position
- `get_mass_points` — mass point control status
- `get_my_factories` — factory status and production
- `get_map_control` — overall map control percentage

Action tools (mutate game state):
- `attack` — send units to attack
- `defend` — rally units to defend base
- `scout` — send scout unit
- `set_strategy` — change strategic posture
- `build_units` — adjust factory priorities
- `reclaim` — send engineers to reclaim
- `chat` — send chat message
- `noop` — do nothing (terminates loop)

## Verified Behavior

Tested 2026-03-29 with Qwen 3.5:9b via Ollama:
- Single tool call: ~3.5s
- Multi-turn (observation → action): ~4.8s
- Model correctly interprets tool results and adjusts subsequent calls
- `/no_think` + `think: false` suppresses chain-of-thought tokens
