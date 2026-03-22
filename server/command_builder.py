# T019: Command Builder — server/command_builder.py
#
# Validates and normalises a raw LLM response dict into a clean StrategicDecision.
# Fills missing fields with safe defaults. Returns a validated dict always.

from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger(__name__)

_VALID_STRATEGIES = {
    "land_rush", "tech_up", "turtle", "balanced",
    "air", "naval", "defend", "build_factories", "reclaim", "attack",
}
_VALID_DIRECTIONS = {"north", "south", "east", "west", "none", None}
_VALID_BUILD_PRIORITIES = {
    "land", "air", "navy", "t1", "t2", "t3",
    "t2_factory", "t3_factory", "shield", "t2_mex", "t1_mex",
    "pgen", "t2_pgen", "arty", "t2_pd",
}


class CommandBuilder:
    """Validates and sanitises the raw LLM dict into a clean StrategicDecision."""

    def build(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        """
        Validate all StrategicDecision fields.
        Returns a guaranteed-valid dict even if raw is None or malformed.
        """
        if not isinstance(raw, dict):
            log.warning("CommandBuilder.build: raw is not a dict (%s). Using defaults.", type(raw))
            return self._defaults()

        decision = dict(raw)  # shallow copy

        # 1. strategy
        strategy = decision.get("strategy", "balanced")
        if not isinstance(strategy, str) or strategy not in _VALID_STRATEGIES:
            log.debug("Invalid strategy '%s', defaulting to 'balanced'.", strategy)
            decision["strategy"] = "balanced"

        # 2. build_priority (list of strings)
        bp = decision.get("build_priority", ["land"])
        if not isinstance(bp, list):
            bp = [str(bp)] if bp else ["land"]
        decision["build_priority"] = [
            p for p in bp
            if isinstance(p, str) and p in _VALID_BUILD_PRIORITIES
        ] or ["land"]

        # 3. army_composition (land+air+navy = 1.0 ± 0.01)
        comp = decision.get("army_composition", {})
        if not isinstance(comp, dict):
            comp = {}
        land  = float(comp.get("land",  0.7))
        air   = float(comp.get("air",   0.2))
        navy  = float(comp.get("navy",  0.1))
        total = land + air + navy
        if not math.isclose(total, 1.0, abs_tol=0.01) or total == 0:
            # Re-normalise
            if total > 0:
                land, air, navy = land / total, air / total, navy / total
            else:
                land, air, navy = 0.7, 0.2, 0.1
        decision["army_composition"] = {
            "land": round(land, 3),
            "air":  round(air,  3),
            "navy": round(navy, 3),
        }

        # 4. attack_direction
        direction = decision.get("attack_direction")
        if direction not in _VALID_DIRECTIONS:
            log.debug("Invalid attack_direction '%s', setting to None.", direction)
            decision["attack_direction"] = None

        # 5. retreat_threshold — must be in [0.1, 0.9]
        rt = decision.get("retreat_threshold", 0.3)
        try:
            rt = float(rt)
            rt = max(0.1, min(0.9, rt))
        except (TypeError, ValueError):
            rt = 0.3
        decision["retreat_threshold"] = round(rt, 2)

        # 6. chat_message — max 200 chars, or None
        chat = decision.get("chat_message")
        if chat is not None:
            chat = str(chat).strip()
            if len(chat) > 200:
                chat = chat[:197] + "…"
            if not chat:
                chat = None
        decision["chat_message"] = chat

        # 7. reasoning — max 500 chars
        reasoning = decision.get("reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)
        decision["reasoning"] = reasoning[:500]

        return decision

    # ------------------------------------------------------------------
    def _defaults(self) -> dict[str, Any]:
        return {
            "strategy":          "balanced",
            "build_priority":    ["land"],
            "army_composition":  {"land": 0.7, "air": 0.2, "navy": 0.1},
            "attack_direction":  None,
            "retreat_threshold": 0.3,
            "chat_message":      None,
            "reasoning":         "default — input was invalid",
        }
