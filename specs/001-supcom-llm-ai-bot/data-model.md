# Data Model: SupCom:FA LLM AI Bot

**Phase**: 1 — Design
**Date**: 2026-02-18

---

## Entities & Schemas

### 1. GameStateSnapshot
*Produced by*: `lua/AI/GameStateCollector.lua` every ~15-20s or on trigger event
*Consumed by*: Python `StateProcessor` → LLM prompt builder

```
GameStateSnapshot
├── tick                : integer          — game simulation tick
├── game_time_s         : integer          — elapsed game seconds
├── phase               : enum             — "early" | "mid" | "late"
├── faction             : string           — "UEF" (MVP: always UEF)
├── mode                : enum             — "enemy" | "ally"
├── trigger_event       : enum | null      — "army_lost" | "enemy_detected" |
│                                            "economy_stall" | "phase_change" |
│                                            "player_chat" | "periodic" |
│                                            "ally_under_attack" | "ally_air_threat" |
│                                            "ally_economy_stall" | null
├── ally                : AllyState | null — present only in ally mode (see entity 8)
├── economy
│   ├── mass_income     : float            — mass/sec
│   ├── mass_stored     : float
│   ├── mass_storage_max: float
│   ├── energy_income   : float            — energy/sec
│   ├── energy_stored   : float
│   └── energy_storage_max: float
├── units
│   ├── factories       : integer
│   ├── engineers       : integer
│   ├── land_military   : integer
│   ├── air_military    : integer
│   ├── navy_military   : integer
│   ├── t1              : integer          — total T1 military units
│   ├── t2              : integer
│   ├── t3              : integer
│   └── experimentals   : integer
├── threats
│   ├── near_base       : float            — threat value near HQ
│   ├── nearest_enemy_distance: float      — distance in game units
│   └── enemy_army_size_estimate: integer
├── map_control_pct     : integer          — 0-100 estimated map control
├── player_chat         : string[]         — recent unprocessed player messages
└── current_strategy    : string           — last active strategy name
```

**Validation rules**:
- `tick` ≥ 0
- `game_time_s` ≥ 0
- `mass_income`, `energy_income` ≥ 0
- `map_control_pct` in [0, 100]
- `player_chat` max 10 messages (rolling buffer)

---

### 2. StrategicDecision
*Produced by*: Python `CommandBuilder` after LLM inference
*Consumed by*: `lua/AI/StrategyExecutor.lua`

```
StrategicDecision
├── strategy            : string           — human-readable strategy name
│                                            e.g. "land_rush", "tech_up", "turtle"
├── build_priority      : string[]         — ordered list of build targets
│                                            e.g. ["t2_factory", "shield", "t2_mex"]
├── army_composition
│   ├── land            : float            — 0.0-1.0, fraction of production
│   ├── air             : float
│   └── navy            : float            — sum must equal 1.0
├── attack_direction    : string | null    — cardinal or null ("north","south","east","west","none")
├── retreat_threshold   : float            — 0.0-1.0, army strength fraction to trigger retreat
├── chat_message        : string | null    — message to send in-game (null = silent)
├── reasoning           : string           — LLM reasoning for debug overlay/log
└── timestamp           : integer          — game tick when decision was made
```

**Validation rules**:
- `army_composition` values sum to 1.0 (±0.01 tolerance)
- `retreat_threshold` in [0.1, 0.9]
- `chat_message` max 200 characters
- `reasoning` max 500 characters

---

### 3. ChatMessage
*Produced by*: Player (via game chat) or Bot (via Lua SimCallback)
*Consumed by*: Python `StateProcessor` (player→bot) / Lua `ChatHandler` (bot→player)

```
ChatMessage
├── sender              : enum             — "player" | "bot"
├── content             : string           — raw text in Russian or English
├── language            : enum             — "ru" | "en" (auto-detected)
├── game_tick           : integer
└── processed           : boolean          — false until routed to LLM
```

**Validation rules**:
- `content` not empty, max 500 characters
- `language` auto-detected by presence of Cyrillic characters

---

### 4. BotConfiguration
*Produced by*: `installer/config.json` (user edits via setup script)
*Consumed by*: Python bridge server on startup; Lua mod on game init

```
BotConfiguration
├── llm
│   ├── model           : string           — Ollama model tag, default "qwen2.5:7b-instruct-q5_K_M"
│   ├── base_url        : string           — default "http://localhost:11434"
│   ├── temperature     : float            — default 0.3
│   └── max_tokens      : integer          — default 256
├── game
│   ├── game_path       : string           — "C:\Program Files (x86)\Supreme Commander"
│   ├── faf_mods_path   : string           — "%APPDATA%\FAForever\mods"
│   └── faction         : string           — "UEF" (MVP only)
├── bot
│   ├── difficulty      : enum             — "easy" | "normal" | "hard"
│   ├── playstyle       : enum             — "rush" | "balanced" | "turtle" | "air" | "naval"
│   ├── chat_enabled    : boolean          — default true
│   ├── chat_language   : enum            — "auto" | "ru" | "en"
│   ├── poll_interval_fast_s: integer      — fast model baseline interval, default 20
└── poll_interval_deep_s: integer      — deep model baseline interval, default 60
├── bridge
│   ├── pipe_name       : string           — default "\\\\.\\pipe\\supcom_llm_bridge"
│   └── timeout_s       : integer          — LLM response timeout, default 10
├── debug
│   ├── overlay_enabled : boolean          — default true
│   ├── log_enabled     : boolean          — default true
│   └── log_path        : string           — default "%ProgramData%\FAForever\logs\llm_ai_decisions.log"
└── version             : string           — config schema version
```

---

### 5. TacticalOrder
*Produced by*: `lua/AI/TacticalMicro.lua` (no LLM involvement)
*Consumed by*: Game engine via Lua issue commands

```
TacticalOrder
├── type        : enum    — "move" | "attack" | "retreat" | "assist" |
│                           "reclaim" | "patrol" | "ferry" | "build"
├── unit_group  : string  — platoon identifier or category tag
├── target      : Position | EntityRef | null
└── priority    : integer — 1 (urgent) to 5 (background)
```

*Note*: TacticalOrders are ephemeral — generated and consumed within the sim tick. Not persisted.

---

### 8. AllyState
*Produced by*: `lua/AI/AllyMonitor.lua` every 5 game-ticks
*Consumed by*: `ReflexLayer.lua` (immediate action) + `GameStateCollector.lua` (LLM context)

```
AllyState
├── army_index          : integer          — FAF army index of the human player
├── base_position       : [float, float]   — player's ACU starting position [x, z]
├── base_threat         : float            — threat value at ally base (GetThreatAtPosition)
├── air_threat_near_base: float            — air-specific threat within 500 units of ally base
├── army_size           : integer          — ally's total military unit count
├── army_size_prev      : integer          — army size 10 ticks ago (for loss detection)
├── mass_income         : float            — ally's mass income rate
├── mass_stored         : float
├── energy_income       : float
├── under_air_attack    : boolean          — air_threat_near_base > 15
├── under_ground_attack : boolean          — base_threat > 30
└── losing_army_fast    : boolean          — army_size < army_size_prev * 0.6
```

**Derived triggers** (evaluated by AllyMonitor each tick):
- `under_air_attack = true` → ReflesLayer fires `SCRAMBLE_INTERCEPTORS`
- `under_ground_attack = true` → ReflexLayer fires `SEND_GROUND_SUPPORT`
- `losing_army_fast = true` → ReflexLayer fires `SEND_ARMY_SUPPORT`
- `mass_income < 3 AND mass_stored < 100` for 30s → ReflexLayer fires `SHARE_MASS`

---

### 9. ReflexEvent
*Produced by*: `lua/AI/AllyMonitor.lua`
*Consumed by*: `lua/AI/ReflexLayer.lua` — executed within 1-3 ticks (< 1 second)

```
ReflexEvent
├── type        : enum    — "SCRAMBLE_INTERCEPTORS" | "SEND_GROUND_SUPPORT" |
│                           "SEND_ARMY_SUPPORT" | "SHARE_MASS" | "SHARE_ENERGY"
├── target      : [float, float]   — ally base position
├── priority    : integer — 1 (critical) or 2 (helpful)
└── tick        : integer — when event was generated
```

*Note*: ReflexEvents bypass the LLM entirely. Execution is pure Lua game commands.
After acting on a ReflexEvent, AllyMonitor sends a `"ally_under_attack"` trigger snapshot
to the Python server so the LLM can provide contextual chat commentary and strategic adaptation.

---

### 6. BotSaveState
*Produced by*: Lua mod on game save
*Consumed by*: Python server on game load

```
BotSaveState
├── save_tick           : integer
├── save_timestamp      : string           — ISO 8601
├── current_strategy    : StrategicDecision (last issued)
├── conversation_history: ChatMessage[]    — last 20 messages
├── llm_context_window  : string           — serialized LLM context for resumption
└── performance_metrics
    ├── total_llm_queries : integer
    ├── fallback_activations: integer
    └── avg_response_ms   : float
```

**Persistence**: `%APPDATA%\FAForever\saves\llm_bot_state_{save_name}.json`

---

### 7. DecisionLogEntry
*Produced by*: Python `DecisionLogger` after each LLM interaction
*Stored in*: JSONL file (one JSON object per line)

```
DecisionLogEntry
├── timestamp           : string           — ISO 8601
├── game_tick           : integer
├── game_time_s         : integer
├── trigger_event       : string
├── snapshot_summary    : object           — condensed GameStateSnapshot
├── prompt_tokens       : integer
├── response_tokens     : integer
├── response_latency_ms : integer
├── decision            : StrategicDecision
└── fallback_used       : boolean
```

---

## State Transitions

### Bot Operational State Machine
```
[INIT]
  │ DLL loaded, pipe connected
  ▼
[CONNECTING]
  │ Python server started, Ollama available
  ▼
[ACTIVE] ◄──────────────────────────────┐
  │                                     │
  ├─ periodic tick (45s) → [LLM_QUERY]  │
  ├─ critical event     → [LLM_QUERY]   │
  └─ player chat        → [LLM_QUERY]   │
                              │          │
                    LLM response OK ─────┘
                              │
                    LLM timeout/fail
                              │
                              ▼
                         [FALLBACK]
                              │ LLM reconnects
                              └──────────────► [ACTIVE]

[ACTIVE] ──── game save ──► [PERSISTING]
                                  │ state written
                                  ▼
                              [ACTIVE]

[ACTIVE] ──── game end ──► [SHUTDOWN]
```

### Game Phase Transitions (triggers immediate LLM query)
```
"early"  →  "mid"  : first T2 factory built or game_time_s > 600
"mid"    →  "late" : first T3 unit or experimental started, or game_time_s > 1800
```

---

## Component Data Flow

```
┌─────────────────────────────────────────────────────────┐
│  Supreme Commander: FA (Game Process)                    │
│                                                          │
│  GameStateCollector ──► JSON ──► LLMBridge.Send()       │
│         ▲                              │                 │
│         │                         [DLL pipe write]       │
│  StrategyExecutor                      │                 │
│         ▲                              │                 │
│  LLMBridge.Receive() ◄── JSON ◄── [DLL pipe read]        │
│         │                                                │
│  TacticalMicro (independent, real-time)                  │
│  ChatHandler (reads/writes via SimCallbacks)              │
└─────────────────────────────────────────────────────────┘
                         Named Pipe
┌─────────────────────────────────────────────────────────┐
│  Python Bridge Server                                    │
│                                                          │
│  PipeServer ──► StateProcessor ──► LLMClient            │
│                                        │                │
│                                    Ollama REST           │
│                                    (localhost:11434)     │
│                                        │                │
│                 CommandBuilder ◄── LLM response          │
│                      │                                   │
│            PipeServer.write() + DecisionLogger           │
└─────────────────────────────────────────────────────────┘
```
