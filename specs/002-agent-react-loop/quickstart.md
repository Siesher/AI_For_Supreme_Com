# Quickstart: Agent ReAct Loop

## Prerequisites
- Ollama running with `qwen3.5:9b` loaded
- Python 3.12 with venv at `.venv/`
- SupCom:FA with FAF client

## New Config Fields

Add to `installer/config.json` under `bot`:
```json
{
  "bot": {
    "react_max_iterations": 3,
    "react_cycle_timeout_s": 30,
    "decision_memory_size": 10
  }
}
```

## New Files
| File | Purpose |
|------|---------|
| `server/react_loop.py` | ReAct loop engine — orchestrates observe/act iterations |
| `server/decision_memory.py` | Rolling buffer of past decisions for LLM context |
| `mod/lua/AI/ObservationHandlers.lua` | Lua-side handlers for observation tool queries |

## Modified Files
| File | Changes |
|------|---------|
| `server/tools.py` | Add 5 observation tool definitions |
| `server/llm_client.py` | Accept accumulated messages list, return tool_calls |
| `server/bridge_server.py` | Replace direct LLM call with ReAct loop |
| `server/state_processor.py` | Inject decision memory into system prompt |
| `mod/lua/AI/LLMAIBrain.lua` | Handle observation_request messages, return results |

## Testing

1. Start Ollama: `ollama serve`
2. Start bridge: `python server/bridge_server.py --config installer/config.json`
3. Launch SupCom:FA via FAF, add AI to lobby
4. Check decision log: `%ProgramData%\FAForever\logs\llm_ai_decisions.log`
5. Verify multi-step cycles appear in log (observation → action → feedback)
