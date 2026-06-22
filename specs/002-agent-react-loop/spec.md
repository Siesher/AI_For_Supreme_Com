# Feature Specification: Agent ReAct Loop

**Feature Branch**: `002-agent-react-loop`
**Created**: 2026-03-29
**Status**: Draft
**Input**: User description: "Add observation tools and ReAct feedback loop to the LLM agent — observation tools, tool feedback, decision memory"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent Queries Game State Before Acting (Priority: P1)

The AI bot receives a periodic game state trigger. Instead of immediately issuing commands based on a static snapshot, the agent first calls observation tools (e.g., `get_enemy_army`, `get_threat_at`) to gather targeted intelligence. Based on the observations, it then decides which action tools to call. This produces better-informed decisions because the agent sees exactly what it needs, not a generic dump.

**Why this priority**: This is the core capability that makes the agent proactive rather than reactive. Without observation tools, the agent is blind to details not included in the pre-built snapshot.

**Independent Test**: Start a game, wait for a decision cycle. Verify in the decision log that the agent called at least one observation tool before calling an action tool. Confirm the observation result influenced the action choice (e.g., scouted enemy army → chose defend instead of attack).

**Acceptance Scenarios**:

1. **Given** a periodic decision cycle triggers, **When** the agent processes the snapshot, **Then** the agent may call observation tools to request additional game information before issuing action commands.
2. **Given** the agent calls `get_enemy_army`, **When** the Lua side resolves the query, **Then** the result (unit counts by type) is returned to the Python server within the same decision cycle.
3. **Given** the agent calls `get_threat_at` for a specific map region, **When** the Lua side computes threat, **Then** a numeric threat value is returned and the agent uses it in subsequent tool calls.

---

### User Story 2 - Action Tool Feedback Loop (Priority: P1)

After the agent issues an action tool (e.g., `attack`), the execution result is returned to the LLM. The agent can then issue follow-up tools based on the outcome. For example: agent calls `attack(land, full)` → Lua returns `{success: true, units_sent: 15}` → agent calls `chat("Attacking with 15 tanks!")`. This loop runs up to 3 iterations per decision cycle.

**Why this priority**: Without feedback, the agent operates blind — it never knows if its commands succeeded or failed. Feedback enables multi-step reasoning within a single cycle.

**Independent Test**: Trigger an attack command. Verify the decision log shows: (1) action tool call, (2) execution result returned, (3) follow-up tool call based on the result. Confirm the loop terminates within 3 iterations.

**Acceptance Scenarios**:

1. **Given** the agent calls an action tool, **When** the game executes it and returns a result, **Then** the result is appended to the conversation and the LLM is queried again for follow-up.
2. **Given** the agent has already iterated 3 times in one decision cycle, **When** the LLM returns more tool calls, **Then** the system executes them without re-querying (terminal iteration).
3. **Given** the agent calls `noop` at any iteration, **When** the server processes it, **Then** the loop terminates immediately (no further iterations).

---

### User Story 3 - Decision Memory Across Cycles (Priority: P2)

The server maintains a rolling history of the last N decisions with their outcomes. This history is included in the conversation context so the agent can learn from prior actions within the same game session. For example, if a previous attack failed because the enemy had superior forces, the agent sees this and avoids repeating the same mistake.

**Why this priority**: Memory transforms the agent from stateless to session-aware. However, it builds on top of the ReAct loop (P1) — without feedback, there are no outcomes to remember.

**Independent Test**: Play a game for 5+ minutes. Verify the decision log shows that later decisions reference earlier outcomes. Confirm the memory window does not exceed the configured limit (default: last 10 decisions).

**Acceptance Scenarios**:

1. **Given** 5 decision cycles have completed, **When** a new cycle starts, **Then** the conversation context includes a summary of the last 5 decisions and their outcomes.
2. **Given** the memory buffer is full (N decisions), **When** a new decision completes, **Then** the oldest entry is evicted (FIFO).
3. **Given** a decision resulted in a negative outcome (e.g., army lost after attack), **When** the agent sees this in memory, **Then** it adjusts strategy accordingly (observable in decision log reasoning).

---

### Edge Cases

- What happens when an observation tool times out (Lua doesn't respond within the pipe timeout)?
  → The server skips the observation and proceeds with available information.
- What happens when the ReAct loop hits 3 iterations and the LLM still wants to observe?
  → All pending tool calls from the final iteration are executed, but no further LLM queries are made.
- What happens when the game state changes significantly between iterations of the same cycle?
  → Each observation tool returns real-time data; the agent naturally adapts within the loop.
- What happens when memory grows too large for the context window?
  → Memory is capped at N entries; older entries are summarized or evicted.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support observation tools that query live game state: `get_enemy_army`, `get_threat_at`, `get_mass_points`, `get_my_factories`, `get_map_control`.
- **FR-002**: Observation tools MUST be executed on the Lua side and return structured results to the Python server via the named pipe within the same decision cycle.
- **FR-003**: System MUST implement a ReAct loop: after each LLM response containing tool calls, execute the tools, collect results, and re-query the LLM with the accumulated context.
- **FR-004**: The ReAct loop MUST terminate after a configurable maximum number of iterations (default: 3).
- **FR-005**: The ReAct loop MUST terminate immediately if the LLM calls `noop` or returns no tool calls.
- **FR-006**: Action tools (`attack`, `defend`, `scout`, etc.) MUST return structured execution results (success/failure, units affected, etc.).
- **FR-007**: System MUST maintain a rolling decision memory of the last N decisions (default: 10) with outcomes.
- **FR-008**: Decision memory MUST be included in the LLM conversation context for subsequent decision cycles.
- **FR-009**: The total decision cycle time (all iterations combined) MUST NOT exceed a configurable timeout (default: 30 seconds).
- **FR-010**: If any iteration times out, the system MUST fall back to the rule-based fallback strategy.

### Key Entities

- **DecisionCycle**: A complete observe-think-act loop (1–3 iterations). Contains: trigger event, iterations list, total latency, final outcome.
- **Iteration**: One LLM query + tool execution within a cycle. Contains: messages sent, tool calls received, tool results returned.
- **ToolResult**: Structured result from executing a tool (observation or action). Contains: tool name, success flag, data payload.
- **DecisionMemory**: Rolling buffer of past DecisionCycles with summarized outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The agent calls at least one observation tool in 50%+ of decision cycles (measured over a 10-minute game).
- **SC-002**: Multi-step decision cycles (2+ iterations) occur in at least 30% of cycles, demonstrating the feedback loop is actively used.
- **SC-003**: The total decision cycle latency (all iterations) stays under 30 seconds for 95% of cycles.
- **SC-004**: The agent demonstrates session learning: in games lasting 15+ minutes, decisions in the second half reference or adapt based on earlier outcomes (verified via decision log).
- **SC-005**: No regression in bot responsiveness — single-iteration cycles complete within the same latency envelope as the current one-shot system.

## Assumptions

- The named pipe supports synchronous request-response within a single decision cycle (Lua can process observation queries and return results before the pipe timeout).
- Qwen 3.5 models handle multi-turn tool-calling conversations (system + user + assistant + tool results + assistant) correctly via Ollama's /api/chat endpoint.
- The Lua sim thread can execute observation queries without blocking the game for more than 1-2 ticks (100-200ms per query).
- Decision memory of 10 entries fits comfortably within Qwen 3.5's context window alongside the system prompt and current state.
