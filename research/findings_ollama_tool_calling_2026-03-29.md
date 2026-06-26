# Ollama /api/chat Multi-Turn Tool Calling — Research Findings
**Date**: 2026-03-29
**Researcher**: ML Research Agent (claude-sonnet-4-6)
**Project context**: SupCom LLM AI Bot — `server/llm_client.py` + `server/state_processor.py`

---

## Executive Summary

Ollama's `/api/chat` endpoint fully supports multi-turn tool calling by accumulating the entire message history including assistant `tool_calls` messages and `role: "tool"` result messages in each subsequent request. Qwen 3.5 (4B and 9B) officially supports native tool calling as of late March 2026 with a 262,144-token context window, but a serious bug in Ollama versions 0.17.6–0.18.2 causes these models to emit raw XML tool-call text instead of structured JSON — version 0.17.5 is currently the recommended safe downgrade until PR #15022's fix ships in a stable release. The current project sends only a flat `[system, user]` message pair each turn and discards history, which means it cannot run an agent-style tool-calling loop, but the fix is straightforward.

---

## 1. Multi-Turn Conversation Structure

Yes, Ollama supports the following exact conversation shape in a single `/api/chat` request.

### Full message array structure

```json
POST /api/chat
{
  "model": "qwen3.5:9b",
  "messages": [
    {
      "role": "system",
      "content": "/no_think\nYou are ..."
    },
    {
      "role": "user",
      "content": "Current game state: ..."
    },
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "function": {
            "name": "set_strategy",
            "arguments": { "strategy": "rush" }
          }
        }
      ]
    },
    {
      "role": "tool",
      "content": "Strategy set to rush."
    },
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "function": {
            "name": "send_chat",
            "arguments": { "message": "Going rush, build T1 tanks!" }
          }
        }
      ]
    },
    {
      "role": "tool",
      "content": "Chat message sent."
    }
  ],
  "tools": [ ... ],
  "stream": false
}
```

The model continues after each `tool` result and stops when it produces a final `content` response without `tool_calls`, or when `done_reason: "stop"` appears with no pending tool calls.

### Key structural rules

- The assistant message that contains `tool_calls` must have `"content": ""` (empty string is fine, null is also accepted).
- The `tool` role message needs only `"role": "tool"` and `"content"` with the result string. `"tool_name"` is optional but recommended for clarity.
- There is **no** `tool_call_id` matching required in Ollama's native API (unlike OpenAI). Results are matched by position/order, not by ID.
- You must append **both** the assistant message with `tool_calls` AND the tool result message to history before the next request. If you only append the tool result without the preceding assistant message, the conversation will be incoherent.

### Standard agent loop (Python, raw httpx — matches project style)

```python
import httpx, json

async def run_tool_loop(base_url: str, model: str, initial_messages: list, tools: list):
    messages = list(initial_messages)  # copy so caller's list is not mutated

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.3, "num_predict": 300},
                }
            )
            resp.raise_for_status()
            data = resp.json()
            assistant_msg = data["message"]          # {"role": "assistant", "content": "", "tool_calls": [...]}

            messages.append(assistant_msg)           # STEP 1: append the full assistant message

            tool_calls = assistant_msg.get("tool_calls", [])
            if not tool_calls:
                break                                # Model is done, no more tool calls

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]  # dict, NOT a JSON string
                result  = dispatch_tool(fn_name, fn_args)

                messages.append({                    # STEP 2: append tool result
                    "role": "tool",
                    "content": str(result),          # must be string
                })

    return messages  # full history including all turns
```

---

## 2. Exact "tool" Role Message Format

Ollama's native `/api/chat` tool result message:

```json
{
  "role": "tool",
  "content": "<result string here>"
}
```

Optional but harmless:
```json
{
  "role": "tool",
  "tool_name": "set_strategy",
  "content": "Strategy set to rush."
}
```

### What is NOT required (Ollama differs from OpenAI here)

| Field | OpenAI | Ollama |
|-------|--------|--------|
| `tool_call_id` | Required | Not supported / ignored |
| `name` | Required | Not required |
| `tool_name` | N/A | Optional, informational only |

### Gotcha: arguments type in history

When the model returns `tool_calls`, the `arguments` field is a **parsed JSON object** (dict), not a JSON string. When you include that assistant message back in the history, keep `arguments` as an object — do not re-serialize it to a string, or Ollama will fail to parse the replay.

---

## 3. Qwen 3.5 (4B and 9B) Tool Calling Support

### Official status (March 2026)

Ollama officially added Qwen 3.5 small model series (0.8B, 2B, 4B, 9B) with the announcement:
> "All models support native tool calling, thinking, and multimodal capabilities in Ollama."

### Training format detail

Qwen 3.5 was trained on the **Qwen3-Coder XML format** for tool calls:
```
<function=tool_name><parameter=key>value</parameter></function>
```
Ollama translates this to/from its standard JSON `tool_calls` format transparently. Users do not need to handle the XML format manually.

### Known bugs — CRITICAL for this project

| Issue | Affected Ollama versions | Models affected | Status |
|-------|--------------------------|-----------------|--------|
| Prints raw XML tool call instead of structured JSON | 0.17.6, 0.17.7, 0.17.8-rc3, 0.18, 0.18.2 | qwen3.5:9b confirmed; likely 4B too | Fixed in PR #15022, not yet in a stable release as of 2026-03-29 |
| Wrong tool format pipeline (Hermes JSON vs Coder XML) | < 0.17.3 | All qwen3.5 variants | Fixed in 0.17.3 |
| Unclosed `</think>` tag corrupts conversation turns | Related to 0.17.6+ changes | qwen3.5 with `think=true` | Fixed in PR #15022 |
| Missing generation prompt after tool calls | Same batch | qwen3.5 | Fixed in PR #15022 |

### Current recommendation

- **Use Ollama 0.17.5** as the safe stable version for qwen3.5 tool calling.
- Alternatively, monitor the Ollama releases page for the version that includes PR #15022.
- `"think": false` (already set in project's `llm_client.py`) mitigates the unclosed `</think>` bug even on broken versions.

### Bug symptom to watch for

If `llm_client.py` logs "LLM returned text instead of tools" with content like `<function=set_strategy>...`, that is the XML leak bug. The current fallback (`noop` with XML as reasoning) will silently swallow it — which is better than crashing but means the intended commands are never executed.

---

## 4. Context Window

| Model | Native context | Extended (YaRN) | Practical limit for tool calling |
|-------|---------------|-----------------|----------------------------------|
| qwen3.5:4b | 262,144 tokens | ~1,010,000 tokens | ~32K tokens (VRAM constraint at Q4_K_M) |
| qwen3.5:9b | 262,144 tokens | ~1,010,000 tokens | ~32K tokens (VRAM constraint at Q4_K_M) |
| qwen3:4b (older) | 32,768 tokens | 131,072 (YaRN) | 8K–16K safe |

### Practical notes for this project

- The game state prompt is small (~500–800 tokens per turn). Even with 20 turns of history, you are well under 32K tokens.
- The 262K native window means you could theoretically include the entire game log in context, but VRAM limits the actual KV cache size at Q4_K_M quantization.
- Ollama's `num_predict: 300` (max_tokens in this project) only limits the output length — it has no effect on how much context is accepted.
- Qwen 3.5 docs note: "maintain at least 128K context length to preserve thinking capabilities." Since we use `think: false`, this is not a constraint here.

---

## 5. Comparison Table

| Capability | Ollama Support | Qwen 3.5 (4B/9B) Specific |
|------------|---------------|--------------------------|
| Multi-turn tool calling | Full support | Yes, with Ollama 0.17.5 |
| Sequential tool calls (multiple tool_calls in one response) | Yes | Yes |
| Chained turns (tool result → more tool calls) | Yes | Yes (when working) |
| tool_call_id matching | No / not needed | Not applicable |
| Streaming + tool calls | Yes (Ollama blog) | Not tested for this project |
| Parallel tool calls (multiple tools in same response) | Yes | Partially — can be unreliable |
| Context window for history | 262K native | ~32K practical |

---

## 6. Applicability to Current Project

### Current architecture (single-shot)

`state_processor.py:build_messages()` returns exactly `[system, user]` every call. There is no history replay. `llm_client.py:query()` sends this flat array once and reads `tool_calls` from the single response.

This works but means:
- The model cannot "think aloud" and then make a second tool call informed by the first result.
- No agent loop is possible without changes.

### To enable multi-turn within a single game tick

1. `LLMClient.query()` would need to accept and return an accumulated `messages` list.
2. After getting a response with `tool_calls`, dispatch each tool, collect results, append both the assistant message and tool result messages, then call `_query_model` again.
3. Loop until `tool_calls` is empty or a max-iterations guard triggers.
4. Return the final assistant content message alongside any tool calls.

The project's current design (where `bridge_server.py` orchestrates one request per event) is compatible with wrapping `query()` in a tool loop — the tool dispatch happens server-side against `GAME_TOOLS`, and only the final resolved commands are sent back through the Named Pipe to Lua.

### Is multi-turn worth adding?

For simple "pick a strategy and send a chat" tasks — no. For complex reactive scenarios where the model needs to check a fact (e.g., "what is the ally's status?") before deciding on a response — yes. Given the latency budget (20s / 60s poll intervals), a 2–3 turn loop at ~3–5s per call is feasible.

---

## Key References

- [Ollama Tool Calling Documentation](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama API Reference — /api/chat](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [Ollama Blog: Tool Support](https://ollama.com/blog/tool-support)
- [Ollama Blog: Streaming + Tool Calling](https://ollama.com/blog/streaming-tool)
- [GitHub Issue #14745: qwen3.5:9b prints tool call as text](https://github.com/ollama/ollama/issues/14745)
- [GitHub Issue #14493: Qwen 3.5 tool calling non-functional](https://github.com/ollama/ollama/issues/14493)
- [Ollama library: qwen3.5:9b](https://ollama.com/library/qwen3.5:9b)
- [HuggingFace: Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)
- [HuggingFace: Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Qwen3.5 context window — HackerNoon](https://hackernoon.com/qwen35-9b-a-small-model-with-a-massive-context-window)
- [Ollama Qwen3.5 small series announcement](https://x.com/ollama/status/2028510184788926567)
