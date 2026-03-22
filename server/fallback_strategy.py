# T020: Fallback Strategy — server/fallback_strategy.py
#
# Rule-based decision maker used when the LLM is unavailable (timeout,
# Ollama not running, pipe disconnected).
# Always returns a valid StrategicDecision dict.

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class FallbackStrategy:
    """Rule-based strategy that runs without any LLM inference."""

    def get_command(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate simple heuristics and return a valid StrategicDecision.
        Rules (in priority order):
          1. near_base threat > 50  → defend
          2. factories < 3          → build_factories
          3. mass_income < 3        → reclaim
          4. land_military > 20     → attack
          5. default                → balanced
        """
        economy = snapshot.get("economy", {})
        units   = snapshot.get("units",   {})
        threats = snapshot.get("threats", {})
        mode    = snapshot.get("mode", "opponent")

        mass_income   = economy.get("mass_income",   0)
        factories     = units.get("factories",       0)
        land_military = units.get("land_military",   0)
        near_base_thr = threats.get("near_base",     0)

        # Priority 1: base under heavy attack
        if near_base_thr > 50:
            return self._make(
                strategy="defend",
                build_priority=["t2_pd", "shield"],
                comp={"land": 0.6, "air": 0.3, "navy": 0.1},
                direction="none",
                retreat=0.5,
                chat=None,
                reason="Fallback: base under attack, switching to defense",
            )

        # Priority 2: not enough production
        if factories < 3:
            return self._make(
                strategy="build_factories",
                build_priority=["t1_mex", "land"],
                comp={"land": 0.8, "air": 0.1, "navy": 0.1},
                direction=None,
                retreat=0.3,
                chat=None,
                reason="Fallback: need more factories",
            )

        # Priority 3: mass starved
        if mass_income < 3:
            return self._make(
                strategy="reclaim",
                build_priority=["t1_mex", "t2_mex"],
                comp={"land": 0.7, "air": 0.2, "navy": 0.1},
                direction=None,
                retreat=0.3,
                chat=None,
                reason="Fallback: mass income critical, reclaiming",
            )

        # Priority 4: army large enough to push
        if land_military > 20:
            return self._make(
                strategy="attack",
                build_priority=["land"],
                comp={"land": 0.8, "air": 0.1, "navy": 0.1},
                direction="nearest_enemy",
                retreat=0.3,
                chat=None,
                reason="Fallback: sufficient army — attacking",
            )

        # Default: balanced growth
        return self._make(
            strategy="balanced",
            build_priority=["land", "t2_mex"],
            comp={"land": 0.7, "air": 0.2, "navy": 0.1},
            direction=None,
            retreat=0.3,
            chat=None,
            reason="Fallback: balanced growth (LLM unavailable)",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _make(
        strategy: str,
        build_priority: list[str],
        comp: dict[str, float],
        direction: str | None,
        retreat: float,
        chat: str | None,
        reason: str,
    ) -> dict[str, Any]:
        log.info("Fallback strategy: %s (%s)", strategy, reason)
        return {
            "strategy":          strategy,
            "build_priority":    build_priority,
            "army_composition":  comp,
            "attack_direction":  direction,
            "retreat_threshold": retreat,
            "chat_message":      chat,
            "reasoning":         reason,
        }
