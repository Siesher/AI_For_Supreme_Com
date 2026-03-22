# T017: State Processor — server/state_processor.py
#
# Converts a GameStateSnapshot dict into an LLM prompt string.
# Target: ≤800 tokens total (system + state + instruction).
#
# Qwen3.5 /no_think directive: prepended to every system prompt so the model
# skips chain-of-thought tokens and outputs JSON directly.

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Playstyle prompts (US5 — T046 wires these in)
# ---------------------------------------------------------------------------
PLAYSTYLE_PROMPTS: dict[str, str] = {
    "rush":     "Prioritize early T1 attack within 4 minutes. Build fast, attack faster.",
    "balanced": "Balance economy and military for sustainable long-term play.",
    "turtle":   "Maximize base defense before attacking. PDs, shields, then army.",
    "air":      "Prioritize air factories and gunships for early map control.",
    "naval":    "Expand to water. Build destroyers for sea control and coastal bombardment.",
}

# ---------------------------------------------------------------------------
# Difficulty prompts (US5 — T045)
# ---------------------------------------------------------------------------
DIFFICULTY_PROMPTS: dict[str, str] = {
    "easy":   "Play conservatively, react slowly, and occasionally make suboptimal decisions.",
    "normal": "Play competently at a typical human skill level.",
    "hard":   "Optimize every decision. React instantly. Maximize economy efficiency.",
}


class StateProcessor:
    """Builds LLM prompts from GameStateSnapshot dicts."""

    def __init__(self, config: dict) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_prompt(
        self,
        snapshot: dict[str, Any],
        config: dict[str, Any],
        history: list[dict],
        ally_mode: bool = False,
    ) -> str:
        """
        Return a complete prompt string for the LLM.
        Includes: /no_think directive, system context, game state,
        recent chat, and JSON output instruction.
        """
        bot_cfg       = config.get("bot", {})
        mode          = snapshot.get("mode", "opponent")
        difficulty    = bot_cfg.get("difficulty", "normal")
        playstyle     = bot_cfg.get("playstyle", "balanced")
        chat_language = bot_cfg.get("chat_language", "auto")

        # Detect language from player chat if auto
        language = self._detect_language(snapshot.get("player_chat", []), chat_language)

        system_prompt = self._build_system_prompt(mode, difficulty, playstyle, language, ally_mode)
        state_block   = self._build_state_block(snapshot)
        chat_block    = self._build_chat_block(snapshot.get("player_chat", []), history)
        ally_block    = self._build_ally_block(snapshot.get("ally")) if ally_mode else ""
        instruction   = self._build_instruction(language)

        return "\n\n".join(filter(None, [
            system_prompt,
            state_block,
            ally_block,
            chat_block,
            instruction,
        ]))

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
        self, mode: str, difficulty: str, playstyle: str, language: str, ally_mode: bool
    ) -> str:
        diff_text   = DIFFICULTY_PROMPTS.get(difficulty, DIFFICULTY_PROMPTS["normal"])
        play_text   = PLAYSTYLE_PROMPTS.get(playstyle, PLAYSTYLE_PROMPTS["balanced"])
        lang_note   = "Russian" if language == "ru" else "English"
        mode_desc   = (
            "cooperate with the human player as a teammate"
            if mode == "ally"
            else "defeat the human player as an opponent"
        )

        ally_extra = ""
        if ally_mode:
            ally_extra = (
                "\nYou are a human teammate. When reacting to threats, describe what you just did "
                "and why in chat_message. Be brief and natural "
                "(e.g. 'Вижу авиацию у тебя — перехватчики летят'). "
                "Never be silent when reacting to threats."
            )

        return (
            "/no_think\n"
            "You are an AI commander playing Supreme Commander: Forged Alliance as the UEF faction.\n"
            f"Your role: {mode_desc}.\n"
            f"Difficulty: {difficulty}. {diff_text}\n"
            f"Playstyle: {playstyle}. {play_text}\n"
            f"Always respond in {lang_note}.\n"
            f"Always respond with valid JSON matching the StrategicDecision schema."
            f"{ally_extra}"
        )

    def _build_state_block(self, snapshot: dict) -> str:
        tick        = snapshot.get("tick", 0)
        game_time_s = snapshot.get("game_time_s", 0)
        phase       = snapshot.get("phase", "early")
        trigger     = snapshot.get("trigger_event", "periodic")
        map_ctrl    = snapshot.get("map_control_pct", 50)
        strategy    = snapshot.get("current_strategy", "balanced")

        eco  = snapshot.get("economy", {})
        units = snapshot.get("units", {})
        thr   = snapshot.get("threats", {})

        lines = [
            f"=== GAME STATE (Tick {tick}, ~{game_time_s // 60}:{game_time_s % 60:02d} into game) ===",
            f"Phase: {phase} | Map control: {map_ctrl}% | Trigger: {trigger}",
            "",
            "Economy:",
            f"  Mass:   {eco.get('mass_stored', 0):.0f}/{eco.get('mass_storage_max', 1000):.0f} stored"
            f" | +{eco.get('mass_income', 0):.1f}/s",
            f"  Energy: {eco.get('energy_stored', 0):.0f}/{eco.get('energy_storage_max', 8000):.0f} stored"
            f" | +{eco.get('energy_income', 0):.1f}/s",
            "",
            "Military (UEF):",
            f"  Factories: {units.get('factories', 0)} | Engineers: {units.get('engineers', 0)}",
            f"  Land: {units.get('land_military', 0)} (T1:{units.get('t1', 0)} T2:{units.get('t2', 0)}"
            f" T3:{units.get('t3', 0)})"
            f" | Air: {units.get('air_military', 0)} | Navy: {units.get('navy_military', 0)}",
            f"  Experimentals: {units.get('experimentals', 0)}",
            "",
            "Situation:",
            f"  Threat near base: {thr.get('near_base', 0):.0f}",
            f"  Nearest enemy: {thr.get('nearest_enemy_distance', 9999):.0f} units away",
            f"  Enemy army estimate: {thr.get('enemy_army_size_estimate', 0)}",
            f"  Current strategy: {strategy}",
        ]
        return "\n".join(lines)

    def _build_ally_block(self, ally: dict | None) -> str:
        if not ally:
            return ""
        lines = [
            "=== ALLY STATUS ===",
            f"  Air threat at ally base: {ally.get('air_threat_near_base', 0):.0f}"
            f" ({'ALERT' if ally.get('under_air_attack') else 'OK'})",
            f"  Ground threat at ally base: {ally.get('base_threat', 0):.0f}"
            f" ({'ALERT' if ally.get('under_ground_attack') else 'OK'})",
            f"  Ally army: {ally.get('army_size', 0)} units"
            f" ({'LOSING FAST' if ally.get('losing_army_fast') else 'stable'})",
            f"  Ally mass income: {ally.get('mass_income', 0):.1f}/s"
            f"{' (STALL)' if ally.get('mass_income', 0) < 3 else ''}",
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

    def _build_instruction(self, language: str) -> str:
        lang_hint = "в коротком естественном стиле" if language == "ru" else "briefly and naturally"
        return (
            "=== DECISION REQUIRED ===\n"
            "Respond ONLY with valid JSON (no markdown, no explanation):\n"
            '{"strategy": "string", "build_priority": ["string"], '
            '"army_composition": {"land": 0.0, "air": 0.0, "navy": 0.0}, '
            '"attack_direction": "north|south|east|west|none|null", '
            '"retreat_threshold": 0.3, '
            f'"chat_message": "string or null — {lang_hint}, max 100 chars", '
            '"reasoning": "string, max 200 chars"}'
        )
