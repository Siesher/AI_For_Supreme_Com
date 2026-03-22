# T035/T036: LLM Router — server/llm_router.py
#
# route(snapshot) -> model_tag
# Decides whether to use the deep (8B) or fast (4B) model based on:
#   - trigger_event type
#   - chat complexity heuristic
#   - difficulty setting (easy → always 4B, hard → always 8B)

from __future__ import annotations

import re
from typing import Any

# Strategy/complexity keywords (Russian + English)
_COMPLEX_KEYWORDS = re.compile(
    r"атак|оборон|стратег|тактик|атаку|план|defend|attack|plan|strateg|tactic|coordin",
    re.IGNORECASE,
)

# Events that require deep model reasoning
_DEEP_EVENTS = {"army_lost", "phase_change", "deep_periodic"}
# Events that prefer fast model
_FAST_EVENTS = {"ally_under_attack", "ally_air_threat", "ally_economy_stall", "periodic"}


def _is_complex_chat(messages: list[str]) -> bool:
    """Return True if any player message looks like a complex strategic request."""
    for msg in messages:
        if len(msg) > 60:
            return True
        if _COMPLEX_KEYWORDS.search(msg):
            return True
    return False


class LLMRouter:
    """Routes each snapshot to the appropriate Ollama model."""

    def __init__(self, config: dict[str, Any]) -> None:
        llm_cfg         = config.get("llm", {})
        bot_cfg         = config.get("bot", {})
        self._deep_tag  = llm_cfg.get("model_deep", "qwen3.5:9b")
        self._fast_tag  = llm_cfg.get("model_fast", "qwen3.5:4b")
        self._difficulty = bot_cfg.get("difficulty", "normal")

    def route(self, snapshot: dict[str, Any]) -> str:
        """Return the model tag to use for this snapshot."""
        difficulty = self._difficulty

        # Difficulty overrides (T045)
        if difficulty == "easy":
            return self._fast_tag   # easy always uses 4B
        if difficulty == "hard":
            return self._deep_tag   # hard always uses 8B

        # Normal: route by trigger + chat complexity
        trigger     = snapshot.get("trigger_event", "periodic")
        player_chat = snapshot.get("player_chat", [])

        # Ally events → fast model (reflex commentary, time-critical)
        if trigger in _FAST_EVENTS:
            return self._fast_tag

        # Player chat with complex content → deep model
        if trigger == "player_chat" and _is_complex_chat(player_chat):
            return self._deep_tag

        # Army_lost / phase_change / deep_periodic → deep model
        if trigger in _DEEP_EVENTS:
            return self._deep_tag

        # Default: fast model for routine queries
        return self._fast_tag
