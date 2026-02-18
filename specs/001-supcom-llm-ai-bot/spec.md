# Feature Specification: SupCom:FA LLM AI Bot

**Feature Branch**: `001-supcom-llm-ai-bot`
**Created**: 2026-02-16
**Status**: Draft
**Input**: User description: "AI-бот для Supreme Commander: Forged Alliance, который может играть в игру как союзник или противник игрока, коммуницировать через внутриигровой чат на естественном языке, и запускаться локально на ПК. Гибридный подход: LLM для стратегии и общения, Lua для тактического микроконтроля."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - AI Bot Plays a Skirmish Game Against the Player (Priority: P1)

The player launches a Supreme Commander: Forged Alliance skirmish match and selects the LLM AI Bot as an opponent. The AI bot plays a complete game from start to finish: builds a base, manages economy (mass and energy), produces military units, expands across the map, and attacks the player. The bot makes strategic decisions (build order, army composition, expansion timing, when to tech up) guided by a locally running language model, while tactical unit control (micro, target prioritization, formation movement) is handled by Lua scripts running inside the game engine.

**Why this priority**: This is the core value proposition. Without the ability to play a complete game, no other feature matters. Playing as an opponent is the most straightforward mode since no coordination with the player is needed.

**Independent Test**: Can be fully tested by starting a 1v1 skirmish game against the AI bot and observing that it builds a functional base, produces units, and engages in combat. Delivers a playable AI opponent experience.

**Acceptance Scenarios**:

1. **Given** a new skirmish game with the AI bot as opponent, **When** the game starts, **Then** the bot begins building a base (factory, power generators, mass extractors) within the first 60 seconds of game time
2. **Given** the AI bot has an established base, **When** it accumulates sufficient military units, **Then** it launches attacks against the player's base or expansions
3. **Given** the AI bot is under attack, **When** enemy units threaten its base, **Then** it responds by producing defensive units and repositioning forces
4. **Given** a game in progress, **When** the AI bot's strategy is failing (e.g., losing army repeatedly), **Then** the LLM adapts the strategy (switches from rush to turtle, changes army composition)
5. **Given** the game reaches mid-to-late stage, **When** sufficient economy is available, **Then** the bot techs up to higher-tier units and considers experimental units

---

### User Story 2 - AI Bot Communicates with Player via In-Game Chat (Priority: P2)

The player can communicate with the AI bot through the game's built-in chat system. The bot understands messages in natural language (Russian and English) and responds contextually. As an ally, the bot reports its plans, warns about threats, acknowledges player requests, and coordinates attacks. As an opponent, the bot can engage in contextual banter or be configured to stay silent.

**Why this priority**: Communication is what distinguishes this AI from existing rule-based bots. It transforms the experience from playing against a script to interacting with an intelligent entity. However, it requires the core gameplay (P1) to function first.

**Independent Test**: Can be tested by sending chat messages to the bot during a game and verifying it responds with contextually appropriate messages. Delivers a conversational AI companion/opponent experience.

**Acceptance Scenarios**:

1. **Given** the AI bot is an ally, **When** the player sends "attack the enemy base from the south", **Then** the bot acknowledges the message and adjusts its attack direction accordingly
2. **Given** the AI bot is an ally, **When** it detects a large enemy force approaching, **Then** it proactively warns the player via chat (e.g., "Large enemy army approaching from the north")
3. **Given** the AI bot is an opponent, **When** the player sends a chat message, **Then** the bot may respond with contextual game-related commentary
4. **Given** a player sends a message in Russian or English, **When** the bot processes the message, **Then** it responds in the same language the player used
5. **Given** the AI bot is an ally, **When** the player asks "what are you building?", **Then** the bot responds with a summary of its current production and plans

---

### User Story 3 - AI Bot Plays as an Allied Teammate (Priority: P2)

The player launches a team game (e.g., 2v2 or 3v3) with the AI bot as an ally against AI or human opponents. The bot coordinates with the player: shares resources when asked, avoids building in the player's territory, responds to strategic requests, and focuses on complementary strategies (e.g., if the player goes air, the bot focuses on land).

**Why this priority**: Playing as an ally requires all P1 capabilities plus coordination logic, making it a natural extension. Same priority as chat since both enhance the core experience in different ways.

**Independent Test**: Can be tested by starting a 2v2 game with the bot as ally and observing coordination behavior. Delivers a cooperative gameplay experience.

**Acceptance Scenarios**:

1. **Given** a team game with the bot as ally, **When** the game starts, **Then** the bot builds its base without encroaching on the player's expansion areas
2. **Given** the bot is an ally, **When** the player requests "send me mass" via chat, **Then** the bot shares mass resources if it has surplus
3. **Given** the bot is an ally, **When** the player is being attacked, **Then** the bot sends reinforcements to assist the player's defense
4. **Given** a team game, **When** the bot observes the player's army composition, **Then** it adjusts its own production to complement (avoids duplicating the same unit types excessively)

---

### User Story 4 - Local Setup and Configuration (Priority: P3)

The player installs and configures the AI bot system on their local PC. This includes: installing the Lua game mod, setting up the local LLM service, configuring the connection between the game and the LLM, and adjusting bot behavior settings (difficulty, communication style, preferred strategies). The setup process is guided and does not require programming knowledge.

**Why this priority**: Configuration and ease of setup are important for usability but secondary to core gameplay and communication features. Advanced users (the initial target audience) can tolerate a more manual setup process initially.

**Independent Test**: Can be tested by following the setup guide on a clean Windows PC with Supreme Commander: FA installed. Delivers a working installation of the complete system.

**Acceptance Scenarios**:

1. **Given** a PC with SupCom:FA installed, **When** the user follows the setup guide, **Then** all components (game mod, LLM service, bridge) are installed and functional within 30 minutes
2. **Given** the system is installed, **When** the user launches a game with the AI bot, **Then** the bot connects to the local LLM and begins playing without manual intervention
3. **Given** the configuration interface, **When** the user adjusts difficulty settings, **Then** the bot's behavior reflects the chosen difficulty in the next game
4. **Given** a system with insufficient hardware for larger LLM models, **When** the user runs the setup, **Then** the system recommends an appropriate model size and warns about potential performance limitations

---

### User Story 5 - Adjustable Difficulty and Playstyle (Priority: P3)

The player can configure the AI bot's difficulty level and preferred playstyle before starting a game. Difficulty affects the bot's decision speed, economy management efficiency, and micro quality. Playstyle presets (aggressive rush, balanced, defensive turtle, naval focus, air superiority) influence the LLM's strategic decision-making.

**Why this priority**: Enhances replayability and accommodates players of different skill levels. Depends on all core features being stable first.

**Independent Test**: Can be tested by playing multiple games with different difficulty/playstyle settings and observing distinct behavior patterns. Delivers customizable AI opponent/ally experience.

**Acceptance Scenarios**:

1. **Given** the player selects "easy" difficulty, **When** the game is played, **Then** the bot plays noticeably slower and makes less optimal economic decisions compared to "hard" difficulty
2. **Given** the player selects "aggressive rush" playstyle, **When** the game begins, **Then** the bot prioritizes early military production and attacks within the first few minutes
3. **Given** the player selects "defensive turtle" playstyle, **When** the game progresses, **Then** the bot focuses on base defense, shields, and artillery before committing to attacks

---

### Edge Cases

- What happens when the LLM service is unavailable or crashes mid-game? The bot falls back to a basic rule-based AI behavior to keep the game playable, and notifies the player via chat that LLM mode is degraded.
- What happens when the LLM response is too slow (>5 seconds)? The bot continues executing the last known strategy and queues the LLM response for the next decision cycle.
- What happens when the player sends nonsensical or abusive chat messages? The bot responds gracefully or ignores the message; it does not crash or produce harmful output.
- What happens when the game map has no AI markers? The bot generates pathfinding data dynamically or uses a fallback navigation system (similar to existing community approaches for marker generation).
- What happens when the bot's economy completely stalls (zero mass/energy income)? The bot recognizes the stall state, prioritizes reclaiming wreckage, and adjusts its build plan to restore economy.
- What happens when running on low-end hardware where LLM inference is very slow? The bot reduces the frequency of LLM queries and relies more heavily on Lua-based heuristic decisions.

## Clarifications

### Session 2026-02-17

- Q: Target game version — vanilla SupCom:FA or FAF (Forged Alliance Forever)? → A: FAF (Forged Alliance Forever). Base game installed at `C:\Program Files (x86)\Supreme Commander`. The mod targets the FAF Lua codebase and requires the FAF client.
- Q: How many factions should the bot support? → A: One faction for MVP — UEF (ОФЗ). Remaining factions (Aeon, Cybran, Seraphim) to be added in later iterations.
- Q: How often should the bot query the LLM for strategic decisions? → A: Hybrid approach — periodic baseline query every ~45 seconds plus immediate queries on critical events (army lost, enemy detected, economy stall, game phase change).
- Q: Minimum hardware requirements? → A: Target hardware — Ryzen 9 9950X, 32GB RAM, RTX 2080 (8GB VRAM). Minimum spec: 16GB RAM, GPU with 6-8GB VRAM. LLM models up to 7B parameters for GPU inference. CPU fallback supported for systems without compatible GPU.
- Q: Should the bot log its decisions for debugging? → A: Yes — full file logging of all LLM strategic decisions and key tactical events, plus an in-game UI overlay showing the bot's current strategy, game state assessment, and decision reasoning in real time.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a game AI that can play Supreme Commander: Forged Alliance from game start to game end, including base building, economy management, unit production, and combat. MVP scope: UEF faction only; other factions (Aeon, Cybran, Seraphim) are planned for future iterations
- **FR-002**: The system MUST support two operational modes: allied teammate and enemy opponent
- **FR-003**: The system MUST enable bidirectional natural language communication between the player and the AI bot through the game's chat system
- **FR-004**: The system MUST support both Russian and English languages for chat communication
- **FR-005**: The system MUST run entirely on the player's local PC without requiring internet connectivity during gameplay
- **FR-006**: The system MUST use a locally hosted language model for strategic decision-making and chat responses
- **FR-007**: The system MUST handle real-time tactical unit control (micro) without depending on LLM response times. The LLM is queried on a hybrid schedule: periodic baseline every ~45 seconds plus immediate queries on critical game events (army lost, enemy detected, economy stall, game phase transition)
- **FR-008**: The system MUST provide a communication bridge between the game's Lua environment and the external LLM service
- **FR-009**: The system MUST fall back to rule-based AI behavior when the LLM service is unavailable or unresponsive
- **FR-010**: The system MUST not interfere with the game's simulation determinism (restricted to single-player and private games only)
- **FR-011**: The system MUST allow configuration of bot difficulty level and playstyle preferences before game start
- **FR-012**: The system MUST be compatible with standard Supreme Commander: Forged Alliance maps (with and without AI markers)
- **FR-013**: As an ally, the bot MUST coordinate with the player by avoiding building in the player's territory and responding to resource sharing requests
- **FR-014**: The system MUST provide a guided setup process for installing all components on a Windows PC
- **FR-015**: The system MUST handle game state transitions gracefully (player defeat/victory, game pause, save/load) with full state persistence — on save, the bot preserves its current strategy, conversation history, and tactical context; on load, it resumes exactly where it left off
- **FR-016**: The system MUST log all LLM strategic decisions and key tactical events to a file for post-game analysis and debugging
- **FR-017**: The system MUST provide an in-game UI overlay displaying the bot's current strategy, game state assessment, and decision reasoning in real time (toggleable by the player)

### Key Entities

- **Game State Snapshot**: A periodic capture of the current game situation including unit positions, economy status, threat map, player chat messages, and map control. This is the primary input for the LLM's decision-making.
- **Strategic Decision**: A high-level directive from the LLM specifying build orders, army composition targets, expansion priorities, attack timing, and retreat conditions. Translated into concrete game commands by the Lua layer.
- **Chat Message**: A text communication between the player and the AI bot, containing natural language in Russian or English. Includes sender identity, timestamp, and game context.
- **Bot Configuration**: Player-defined settings including difficulty level, playstyle preset, communication language preference, and LLM model selection.
- **Tactical Order**: A real-time unit command generated by the Lua micro-control layer, such as move, attack, retreat, assist, or reclaim. Executed independently of LLM response times.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The AI bot completes 90% of skirmish games without crashing or becoming non-functional
- **SC-002**: The AI bot builds a functional base (at least 1 factory, 3 mass extractors, 3 power generators) within the first 3 minutes of game time on standard maps
- **SC-003**: The bot responds to player chat messages within 10 seconds during normal gameplay
- **SC-004**: The bot's gameplay is competitive with existing community AI mods (comparable to at least baseline adaptive AI level) on standard 1v1 maps
- **SC-005**: The system runs without noticeable game slowdown (maintains the game's simulation speed at +0 or above) on the target hardware (16GB+ RAM, GPU with 6-8GB+ VRAM) with a 7B-parameter LLM model
- **SC-006**: A user with basic PC skills can install and configure the system within 30 minutes following the setup guide
- **SC-007**: The bot demonstrates distinct behavior when configured with different playstyle presets (measurable difference in army composition and timing across at least 3 playstyle options)
- **SC-008**: The bot correctly interprets and acts on at least 70% of simple player commands (e.g., "attack north", "build more air units", "send mass")
- **SC-009**: When the LLM service fails, the bot continues playing using fallback behavior within 5 seconds with no game crash
- **SC-010**: The bot supports at least 5 different standard maps without requiring map-specific configuration
- **SC-011**: Decision logs are written to a file that can be reviewed after the game, containing timestamped LLM queries, responses, and resulting game actions
- **SC-012**: The in-game debug overlay is toggleable and displays the bot's current strategic state without causing noticeable performance degradation
