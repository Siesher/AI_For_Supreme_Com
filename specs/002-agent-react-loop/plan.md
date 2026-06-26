# Implementation Plan: Agent ReAct Loop

**Branch**: `002-agent-react-loop` | **Date**: 2026-03-29 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-agent-react-loop/spec.md`

## Summary

Transform the LLM agent from one-shot tool calling to a multi-step ReAct loop. The agent can now:
(1) call observation tools to query live game state before acting,
(2) receive execution feedback from action tools, and
(3) accumulate decision memory across cycles for session-wide learning.

Architecture: Python server orchestrates the loop (observe → decide → execute → feedback → repeat, max 3 iterations). Lua side handles observation queries synchronously via the named pipe. Tool results feed back into the LLM conversation as `tool` role messages.

## Technical Context

**Language/Version**: Python 3.12 (server), Lua 5.0 (SupCom mod)
**Primary Dependencies**: httpx (Ollama client), asyncio (pipe server), Ollama API /api/chat with tools
**Storage**: In-memory rolling buffer (decision memory); JSONL file (decision log)
**Testing**: Manual integration testing via game sessions + decision log analysis
**Target Platform**: Windows 11 (SupCom:FA is Win32)
**Project Type**: Single — dual-layer (Python server + Lua mod)
**Performance Goals**: Full ReAct cycle (3 iterations) < 30s; single iteration ~5s
**Constraints**: Pipe round-trip < 500ms per observation; max 3 iterations per cycle; Lua 5.0 syntax
**Scale/Scope**: Single game session, 1 LLM agent, 1 named pipe connection

## Constitution Check

*No constitution.md found — no gates to enforce.*

## Project Structure

### Documentation (this feature)

```text
specs/002-agent-react-loop/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output (pipe message contracts)
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
server/
├── tools.py             # MODIFY — add observation tool definitions
├── llm_client.py        # MODIFY — support multi-turn conversations
├── bridge_server.py     # MODIFY — ReAct loop orchestration
├── state_processor.py   # MODIFY — decision memory in messages
├── react_loop.py        # NEW — ReAct loop engine (extracted from bridge_server)
└── decision_memory.py   # NEW — rolling decision buffer

mod/lua/AI/
├── LLMAIBrain.lua       # MODIFY — observation handlers + result feedback
└── ObservationHandlers.lua  # NEW — Lua functions for observation tools
```

**Structure Decision**: Extend existing server/ and mod/lua/AI/ directories. Two new Python modules (react_loop.py, decision_memory.py) and one new Lua module (ObservationHandlers.lua). No structural changes to the project layout.

## Complexity Tracking

No constitution violations to justify.
