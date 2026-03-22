# Contract: Python Bridge Server Internal API

**Component**: `server/bridge_server.py`
**Runtime**: Python 3.11, asyncio

---

## Module Interfaces

### PipeServer
Manages the Windows Named Pipe connection with the DLL.

```python
class PipeServer:
    async def start() -> None
        """Open named pipe server, begin read/write loops."""

    async def read_snapshot() -> GameStateSnapshot | None
        """Return next snapshot from DLL, or None if unavailable."""

    async def write_command(cmd: StrategicDecision) -> None
        """Send command to DLL. Non-blocking."""

    def is_connected() -> bool
```

---

### StateProcessor
Converts a `GameStateSnapshot` into an LLM prompt string.

```python
class StateProcessor:
    def build_prompt(
        snapshot: GameStateSnapshot,
        config: BotConfiguration,
        history: list[ChatMessage]
    ) -> str
        """
        Returns a complete prompt string for the LLM.
        Includes: system context, game state summary,
        recent chat, current strategy, and instruction
        to respond with JSON StrategicDecision.
        Max ~800 tokens to leave room for response.
        """
```

**System prompt template** (injected once):
```
You are an AI commander playing Supreme Commander: Forged Alliance as the UEF faction.
Your role: make high-level strategic decisions based on the current game state.
Mode: {mode} (enemy = defeat the player; ally = cooperate with the player).
Difficulty: {difficulty}.
Playstyle preference: {playstyle}.

Always respond with valid JSON matching the StrategicDecision schema.
Keep chat_message short (<100 chars), friendly, and in {language}.
```

---

### LLMClient
Sends prompts to Ollama and returns parsed responses.

```python
class LLMClient:
    async def query(prompt: str) -> StrategicDecision | None
        """
        POST to Ollama /api/generate with format="json".
        Returns parsed StrategicDecision or None on timeout/error.
        Timeout: config.bridge.timeout_s (default 10s).
        """
```

**Ollama request**:
```python
{
    "model": config.llm.model,
    "prompt": prompt,
    "format": "json",
    "stream": False,
    "options": {
        "temperature": config.llm.temperature,  # 0.3
        "num_predict": config.llm.max_tokens,   # 256
        "stop": ["}"]                           # ensure JSON termination
    }
}
```

---

### FallbackStrategy
Generates rule-based commands when LLM is unavailable.

```python
class FallbackStrategy:
    def get_command(snapshot: GameStateSnapshot) -> StrategicDecision
        """
        Simple heuristic rules:
        - If mass_stored > 500 and factories < 5: build factories
        - If land_military > 20: attack
        - If near_base threat > 50: retreat and defend
        - If mass_income < 5: reclaim
        Always returns a valid StrategicDecision.
        """
```

---

### DecisionLogger
Writes decision log entries to JSONL file.

```python
class DecisionLogger:
    def log(
        snapshot: GameStateSnapshot,
        decision: StrategicDecision,
        latency_ms: int,
        fallback_used: bool
    ) -> None
        """Appends one DecisionLogEntry as JSON line."""
```

---

## LLM Prompt Example

```
=== GAME STATE (Tick 12450, ~6:55 into game) ===
Phase: mid | Map control: 38% | Mode: enemy

Economy:
  Mass: 342/1000 stored | +12.4/s income
  Energy: 4200/8000 stored | +980/s income

Military (UEF):
  Factories: 3 | Engineers: 8
  Army: 24 land (18 T1, 6 T2), 0 air
  Threat near base: 0

Situation:
  Nearest enemy: 820 units away
  Trigger: army_lost (last attack failed)

Recent player messages: none

Current strategy: land_push

=== DECISION REQUIRED ===
Respond with JSON: { "strategy", "build_priority", "army_composition",
"attack_direction", "retreat_threshold", "chat_message", "reasoning" }
```

---

## Event Triggers (when Python receives snapshot)

| `trigger_event` value | Action |
|---|---|
| `"periodic"` | Normal LLM query |
| `"army_lost"` | Priority LLM query (interrupt any pending) |
| `"enemy_detected"` | Priority LLM query |
| `"economy_stall"` | Priority LLM query |
| `"phase_change"` | Priority LLM query |
| `"player_chat"` | LLM query with chat context injected |
