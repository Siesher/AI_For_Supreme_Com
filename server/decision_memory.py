# Decision memory — server/decision_memory.py
#
# Rolling buffer of past decision cycles for LLM context.
# Included in the system prompt so the agent can learn from prior actions
# within a single game session.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class DecisionSummary:
    """Compact representation of one completed ReAct cycle."""

    cycle_id: int
    game_time_s: int
    trigger: str
    observations: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    outcome: str = ""
    score: float = 0.0  # -1.0 (bad) to +1.0 (good), 0 = neutral


class DecisionMemory:
    """Rolling buffer of DecisionSummary entries."""

    def __init__(self, max_entries: int = 10) -> None:
        self._max_entries = max_entries
        self._entries: list[DecisionSummary] = []
        self._next_cycle_id = 1

    @property
    def entries(self) -> list[DecisionSummary]:
        return list(self._entries)

    def next_cycle_id(self) -> int:
        cid = self._next_cycle_id
        self._next_cycle_id += 1
        return cid

    def add_cycle(self, summary: DecisionSummary) -> None:
        """Append a completed cycle summary, evicting oldest if full."""
        self._entries.append(summary)
        if len(self._entries) > self._max_entries:
            self._entries.pop(0)
        log.debug("Memory: added cycle #%d (%d/%d entries)",
                  summary.cycle_id, len(self._entries), self._max_entries)

    def score_last_cycle(self, prev_snapshot: dict[str, Any], curr_snapshot: dict[str, Any]) -> None:
        """Score the most recent cycle by comparing game state before/after.

        Positive score = situation improved, negative = deteriorated.
        """
        if not self._entries:
            return

        prev_eco = prev_snapshot.get("economy", {})
        curr_eco = curr_snapshot.get("economy", {})
        prev_units = prev_snapshot.get("units", {})
        curr_units = curr_snapshot.get("units", {})
        prev_threats = prev_snapshot.get("threats", {})
        curr_threats = curr_snapshot.get("threats", {})

        score = 0.0

        # Army growth: +0.3 if army grew, -0.3 if shrank significantly
        prev_army = prev_units.get("land_military", 0) + prev_units.get("air_military", 0)
        curr_army = curr_units.get("land_military", 0) + curr_units.get("air_military", 0)
        if prev_army > 0:
            army_ratio = curr_army / prev_army
            if army_ratio > 1.1:
                score += 0.3
            elif army_ratio < 0.6:
                score -= 0.3

        # Threat reduction: +0.2 if base threat went down
        prev_threat = prev_threats.get("near_base", 0)
        curr_threat = curr_threats.get("near_base", 0)
        if prev_threat > 5 and curr_threat < prev_threat * 0.5:
            score += 0.2
        elif curr_threat > prev_threat + 10:
            score -= 0.2

        # Economy health: +0.2 if mass income improved
        prev_mass = prev_eco.get("mass_income", 0)
        curr_mass = curr_eco.get("mass_income", 0)
        if curr_mass > prev_mass * 1.15:
            score += 0.2
        elif prev_mass > 0 and curr_mass < prev_mass * 0.7:
            score -= 0.2

        # Map control: +0.1 if expanded
        prev_ctrl = prev_snapshot.get("map_control_pct", 50)
        curr_ctrl = curr_snapshot.get("map_control_pct", 50)
        if curr_ctrl > prev_ctrl + 3:
            score += 0.1
        elif curr_ctrl < prev_ctrl - 5:
            score -= 0.1

        # Clamp to [-1, 1]
        score = max(-1.0, min(1.0, score))
        self._entries[-1].score = score
        log.debug("Scored cycle #%d: %.2f", self._entries[-1].cycle_id, score)

    def to_prompt_block(self) -> str:
        """Format memory as a concise text block for the system prompt.

        Only includes the 5 most recent entries to save tokens (~50% reduction
        when the buffer is full). The full buffer is still kept for scoring.
        """
        if not self._entries:
            return ""

        # Cap at 5 most recent entries for prompt (full buffer kept for scoring)
        recent = self._entries[-5:]
        lines = ["=== DECISION HISTORY (score: -1=bad, +1=good) ==="]
        for e in recent:
            mins = e.game_time_s // 60
            secs = e.game_time_s % 60
            obs_str = ", ".join(e.observations) if e.observations else "none"
            act_str = ", ".join(e.actions) if e.actions else "none"
            score_str = f"{e.score:+.1f}" if e.score != 0.0 else "n/a"
            lines.append(
                f"[#{e.cycle_id} {mins}:{secs:02d} score={score_str}] "
                f"Trigger: {e.trigger} | Observed: {obs_str} | "
                f"Actions: {act_str} | Outcome: {e.outcome}"
            )
        return "\n".join(lines)

    @staticmethod
    def build_summary(
        cycle_id: int,
        game_time_s: int,
        trigger: str,
        iterations: list[dict[str, Any]],
    ) -> DecisionSummary:
        """Build a DecisionSummary from raw iteration data."""
        observations: list[str] = []
        actions: list[str] = []
        last_results: list[str] = []

        for it in iterations:
            for tc in it.get("tool_calls", []):
                name = tc.get("name", "")
                args = tc.get("args", {})
                if tc.get("is_observation"):
                    observations.append(name)
                elif name != "noop":
                    args_brief = ", ".join(f"{k}={v}" for k, v in args.items())
                    actions.append(f"{name}({args_brief})" if args_brief else name)

            for tr in it.get("tool_results", []):
                if tr.get("success") and tr.get("tool_name") not in ("noop", "chat"):
                    data = tr.get("data", {})
                    brief = ", ".join(f"{k}={v}" for k, v in list(data.items())[:3])
                    last_results.append(f"{tr['tool_name']}: {brief}")

        outcome = "; ".join(last_results[-3:]) if last_results else "no feedback"

        return DecisionSummary(
            cycle_id=cycle_id,
            game_time_s=game_time_s,
            trigger=trigger,
            observations=observations[:5],
            actions=actions[:5],
            outcome=outcome[:200],
        )
