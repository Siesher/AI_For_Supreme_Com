# Agent Knowledge Base — SupCom LLM AI Bot

## Ollama /api/chat Tool Calling (researched 2026-03-29)

### Multi-turn format
- Send FULL message history on every request: [system, user, assistant(tool_calls), tool, assistant(tool_calls), tool, ...]
- Append BOTH the assistant message (with tool_calls) AND the tool result before the next call
- Loop until assistant response has no tool_calls

### tool role message format (Ollama native)
```json
{"role": "tool", "content": "<result string>"}
```
- No tool_call_id needed (Ollama differs from OpenAI here)
- "tool_name" field is optional/informational only
- arguments in replayed assistant messages must stay as dict objects, NOT re-serialized strings

### Qwen 3.5 tool calling bugs
- Broken in Ollama 0.17.6–0.18.2: model emits raw XML `<function=...>` text instead of structured JSON
- Safe version: Ollama 0.17.5
- Fix: PR #15022 (not in stable release as of 2026-03-29)
- Root cause: wrong template pipeline (Hermes JSON format wired to model trained on Coder XML format)
- Mitigation already in project: `"think": false` prevents the unclosed </think> variant of the bug
- Symptom detection: llm_client.py logs "LLM returned text instead of tools" with XML content

### Qwen 3.5 context window
- qwen3.5:4b and qwen3.5:9b: 262,144 native tokens, ~1M with YaRN
- Practical limit at Q4_K_M quantization: ~32K (VRAM constraint)
- Game state prompts ~500-800 tokens/turn, so 20-turn history is ~16K — well within limits

### Current project gap
- state_processor.py returns only [system, user] — no history replay
- llm_client.py does single-shot request — no agent loop
- For multi-turn: wrap _query_model in a while loop that appends assistant+tool messages and re-calls
- 2-3 turn loops feasible within 20s poll interval (3-5s per qwen3.5:4b call)

## Project File Map
- server/llm_client.py — Ollama HTTP client, single-shot tool call dispatch
- server/state_processor.py — Builds [system, user] message pair from game snapshot
- server/tools.py — GAME_TOOLS list (tool definitions for /api/chat)
- server/bridge_server.py — Main asyncio orchestrator
