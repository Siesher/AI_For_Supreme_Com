# T017: State Processor — server/state_processor.py
#
# Converts a GameStateSnapshot dict into messages for /api/chat.
# Returns a list of {"role": ..., "content": ...} dicts.
#
# Qwen3.5 /no_think directive: prepended to system prompt.

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Playstyle prompts
# ---------------------------------------------------------------------------
PLAYSTYLE_PROMPTS: dict[str, str] = {
    "rush": "Prioritize early T1 attack within 4 minutes. Build fast, attack faster.",
    "balanced": "Balance economy and military for sustainable long-term play.",
    "turtle": "Maximize base defense before attacking. PDs, shields, then army.",
    "air": "Prioritize air factories and gunships for early map control.",
    "naval": "Expand to water. Build destroyers for sea control.",
}

DIFFICULTY_PROMPTS: dict[str, str] = {
    "easy": "Play conservatively, react slowly.",
    "normal": "Play competently at a typical human skill level.",
    "hard": "Optimize every decision. React instantly. Maximize efficiency.",
}

PHASE_PROMPTS: dict[str, str] = {
    "early": "EARLY GAME: your job is to expand mass and pump T1 land via build_units; "
    "scout once to find the enemy; attack only with 15+ land; reclaim if mass income is low.",
    "mid": "MID GAME: Expand, tech T2, mixed army+AA, push map control, T2 PD at key spots.",
    "late": "LATE GAME: Tech T3/experimentals, deny enemy mass, full army pushes, protect economy.",
}


class StateProcessor:
    """Builds LLM chat messages from GameStateSnapshot dicts."""

    def __init__(self, config: dict) -> None:
        self._config = config

    def build_messages(
        self,
        snapshot: dict[str, Any],
        config: dict[str, Any],
        history: list[dict],
        ally_mode: bool = False,
        decision_memory=None,
    ) -> list[dict[str, str]]:
        """
        Return a list of chat messages for Ollama /api/chat.
        System prompt + game state as user message.
        """
        bot_cfg = config.get("bot", {})
        mode = snapshot.get("mode", "opponent")
        difficulty = bot_cfg.get("difficulty", "normal")
        playstyle = bot_cfg.get("playstyle", "balanced")
        chat_language = bot_cfg.get("chat_language", "auto")

        phase = snapshot.get("phase", "early")

        language = self._detect_language(snapshot.get("player_chat", []), chat_language)
        system_prompt = self._build_system_prompt(
            mode, difficulty, playstyle, language, ally_mode, phase
        )

        # Append decision memory to system prompt
        if decision_memory:
            memory_block = decision_memory.to_prompt_block()
            if memory_block:
                system_prompt += "\n\n" + memory_block

        state_block = self._build_state_block(snapshot)
        chat_block = self._build_chat_block(snapshot.get("player_chat", []), history)
        ally_block = self._build_ally_block(snapshot.get("ally")) if ally_mode else ""

        user_content = "\n\n".join(filter(None, [state_block, ally_block, chat_block]))

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    # Keep old API for backwards compat (bridge_server calls build_prompt)
    def build_prompt(
        self,
        snapshot: dict[str, Any],
        config: dict[str, Any],
        history: list[dict],
        ally_mode: bool = False,
    ) -> str:
        msgs = self.build_messages(snapshot, config, history, ally_mode)
        return "\n\n".join(m["content"] for m in msgs)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_language(self, chat_messages: list, setting: str) -> str:
        if setting != "auto":
            return setting
        for msg in chat_messages:
            if self._has_cyrillic(str(msg)):
                return "ru"
        return "en"

    @staticmethod
    def _has_cyrillic(text: str) -> bool:
        return any("\u0400" <= ch <= "\u04ff" for ch in text)

    def _build_system_prompt(
        self,
        mode: str,
        difficulty: str,
        playstyle: str,
        language: str,
        ally_mode: bool,
        phase: str = "early",
    ) -> str:
        diff_text = DIFFICULTY_PROMPTS.get(difficulty, DIFFICULTY_PROMPTS["normal"])
        play_text = PLAYSTYLE_PROMPTS.get(playstyle, PLAYSTYLE_PROMPTS["balanced"])
        phase_text = PHASE_PROMPTS.get(phase, PHASE_PROMPTS["early"])
        lang_note = "Russian" if language == "ru" else "English"
        mode_desc = (
            "cooperate with the human player as a teammate"
            if mode == "ally"
            else "defeat the human player as an opponent"
        )

        ally_extra = ""
        if ally_mode:
            ally_extra = (
                "\nYou are a human teammate. When reacting to threats, describe what you did "
                "and why via the chat tool. Be brief and natural."
            )

        return (
            "/no_think\n"
            f"SupCom:FA AI (UEF). Role: {mode_desc}.\n"
            f"{difficulty} difficulty. {diff_text} Playstyle: {playstyle}. {play_text}\n"
            f"Chat language: {lang_note}.\n"
            f"{phase_text}\n"
            "You DRIVE the macro game: grow economy, build army, expand, and attack to WIN. "
            "The base AI only micro-executes your orders.\n"
            "Every cycle emit at least one ACTION tool "
            "(build_units/attack/defend/reclaim/set_strategy/expand), not just observations. "
            "Pick the single highest-impact action:\n"
            "- low mass income or mass stalled -> build_units engineers, or reclaim\n"
            "- under ~15 land units -> build_units land\n"
            "- enemy has air and you lack anti-air -> build_units anti_air\n"
            "- 15+ land and enemy weak or far -> attack or expand\n"
            "- enemy at your base -> defend\n"
            "Scout AT MOST once, only when the enemy position is unknown. NEVER scout twice in a row.\n"
            "Avoid repeating an action that made no progress in DECISION HISTORY."
            f"{ally_extra}"
        )

    def _build_state_block(self, snapshot: dict) -> str:
        # Coerce None → default (snapshot may have null values from broken Lua encode)
        tick = snapshot.get("tick") or 0
        game_time_s = snapshot.get("game_time_s") or 0
        phase = snapshot.get("phase") or "early"
        trigger = snapshot.get("trigger_event") or "periodic"
        map_ctrl = snapshot.get("map_control_pct") or 50
        strategy = snapshot.get("current_strategy") or "balanced"

        eco = snapshot.get("economy") or {}
        units = snapshot.get("units") or {}
        thr = snapshot.get("threats") or {}

        def _num(d: dict, key: str, default: float) -> float:
            """Get numeric value with None-coercion."""
            v = d.get(key)
            return v if isinstance(v, (int, float)) else default

        # Derived interpretation tags so a /no_think model does not have to infer
        # economy health from raw numbers (the #1 reason it never builds).
        mass_inc = _num(eco, "mass_income", 0)
        mass_st = _num(eco, "mass_stored", 0)
        mass_max = _num(eco, "mass_storage_max", 1000)
        en_st = _num(eco, "energy_stored", 0)
        en_max = _num(eco, "energy_storage_max", 8000)
        engineers = _num(units, "engineers", 0)

        if mass_inc < 5:
            mass_tag = "  [LOW MASS INCOME -> expand mexes / reclaim]"
        elif mass_max > 0 and mass_st >= 0.85 * mass_max:
            mass_tag = "  [MASS FLOATING -> build more units / factories]"
        else:
            mass_tag = ""
        energy_tag = (
            "  [ENERGY FLOATING -> build / tech up]"
            if (en_max > 0 and en_st >= 0.95 * en_max)
            else ""
        )
        idle_tag = (
            "  [IDLE BUILD POWER -> grow economy]"
            if (engineers >= 5 and mass_inc < 5)
            else ""
        )

        lines = [
            f"=== GAME STATE (Tick {tick}, ~{game_time_s // 60}:{game_time_s % 60:02d}) ===",
            f"Phase: {phase} | Map control: {map_ctrl}% | Trigger: {trigger}",
            "",
            "Economy:",
            f"  Mass:   {_num(eco, 'mass_stored', 0):.0f}/{_num(eco, 'mass_storage_max', 1000):.0f}"
            f" | +{_num(eco, 'mass_income', 0):.1f}/s{mass_tag}",
            f"  Energy: {_num(eco, 'energy_stored', 0):.0f}/{_num(eco, 'energy_storage_max', 8000):.0f}"
            f" | +{_num(eco, 'energy_income', 0):.1f}/s{energy_tag}",
            "",
            "Military:",
            f"  Factories: {_num(units, 'factories', 0):.0f}"
            f" | Engineers: {_num(units, 'engineers', 0):.0f}{idle_tag}",
            f"  Land: {_num(units, 'land_military', 0):.0f}"
            f" (T1:{_num(units, 't1', 0):.0f} T2:{_num(units, 't2', 0):.0f}"
            f" T3:{_num(units, 't3', 0):.0f})"
            f" | Air: {_num(units, 'air_military', 0):.0f} | Navy: {_num(units, 'navy_military', 0):.0f}",
            "",
            "Situation:",
            f"  Threat near base: {_num(thr, 'near_base', 0):.0f}",
            f"  Nearest enemy: {_num(thr, 'nearest_enemy_distance', 9999):.0f} units away",
            f"  Enemy army estimate: {_num(thr, 'enemy_army_size_estimate', 0):.0f}",
            f"  Current strategy: {strategy}",
        ]
        return "\n".join(lines)

    def _build_ally_block(self, ally: dict | None) -> str:
        if not ally:
            return ""
        lines = [
            "=== ALLY STATUS ===",
            f"  Air threat: {ally.get('air_threat_near_base', 0):.0f}"
            f" ({'ALERT' if ally.get('under_air_attack') else 'OK'})",
            f"  Ground threat: {ally.get('base_threat', 0):.0f}"
            f" ({'ALERT' if ally.get('under_ground_attack') else 'OK'})",
            f"  Army: {ally.get('army_size', 0)} units"
            f" ({'LOSING FAST' if ally.get('losing_army_fast') else 'stable'})",
        ]
        return "\n".join(lines)

    def _build_chat_block(self, player_chat: list, history: list) -> str:
        if not player_chat and not history:
            return ""
        lines = ["Recent communication:"]
        for entry in history[-3:]:
            role = entry.get("role", "?")
            text = entry.get("text", "")
            lines.append(f"  [{role}]: {text}")
        for msg in player_chat[-5:]:
            lines.append(f"  [player]: {msg}")
        return "\n".join(lines)
