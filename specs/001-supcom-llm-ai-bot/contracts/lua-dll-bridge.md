# Contract: Lua ↔ DLL Bridge Interface

**Component**: `supcom_llm_bridge.dll`
**Direction**: Game (Lua Sim layer) ↔ DLL ↔ Python server

---

## Lua-Callable Functions (registered by DLL)

### `LLMBridge.Send(json_string)`
Enqueues a game state snapshot for delivery to the Python server. Non-blocking.

| Parameter | Type | Description |
|---|---|---|
| `json_string` | string | UTF-8 JSON of `GameStateSnapshot` |

- Returns: `nil`
- Errors: silently discards if queue full (max 2 items; old snapshots are stale)
- Thread safety: lock-free SPSC queue (Lua sim → DLL background thread)

---

### `LLMBridge.Receive()`
Returns the latest `StrategicDecision` from the Python server, if available.

- Returns: `string` (UTF-8 JSON) or `nil` if no new command
- Behavior: consumes and removes the command from the queue (next call returns nil until new command arrives)
- Thread safety: lock-free SPSC queue (DLL background thread → Lua sim)

---

### `LLMBridge.IsConnected()`
Reports whether the named pipe connection to the Python server is active.

- Returns: `boolean`
- Use: Lua AI brain calls this to decide whether to use fallback strategy

---

### `LLMBridge.GetStatus()`
Returns a status string for the debug overlay.

- Returns: `string` — one of `"connected"`, `"connecting"`, `"disconnected"`, `"error"`

---

## Named Pipe Protocol

**Pipe name**: `\\.\pipe\supcom_llm_bridge`
**Encoding**: UTF-8
**Framing**: Length-prefixed messages

```
Message frame:
┌──────────────┬──────────────────────────┐
│ Length (4B)  │ JSON payload (Length B)   │
│ little-endian│ UTF-8 encoded            │
└──────────────┴──────────────────────────┘
```

**Direction Game→Python** (GameStateSnapshot):
```json
{
  "type": "snapshot",
  "data": { /* GameStateSnapshot fields */ }
}
```

**Direction Python→Game** (StrategicDecision):
```json
{
  "type": "command",
  "data": { /* StrategicDecision fields */ }
}
```

**Heartbeat** (Python→Game, every 10s):
```json
{ "type": "heartbeat" }
```

---

## Error Handling

| Scenario | DLL Behaviour | Lua Behaviour |
|---|---|---|
| Pipe disconnected | Background thread retries every 3s | `IsConnected()` returns false → fallback |
| Python server not running | DLL waits in connect loop | Bot starts in fallback mode |
| Malformed JSON from Python | Discard message, log warning | Previous strategy continues |
| Send queue full | Drop oldest snapshot | No action needed |
