# T048: Decision Logger — server/decision_logger.py
#
# Writes DecisionLogEntry JSONL to the configured log path.
# Each entry is one JSON object per line (JSONL format).

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class DecisionLogger:
    """Appends one DecisionLogEntry per LLM decision to a JSONL log file."""

    def __init__(self, config: dict[str, Any]) -> None:
        debug_cfg = config.get("debug", {})
        self._enabled   = bool(debug_cfg.get("log_enabled", True))
        raw_path        = debug_cfg.get(
            "log_path",
            r"%ProgramData%\FAForever\logs\llm_ai_decisions.log"
        )
        self._log_path  = Path(os.path.expandvars(raw_path))
        self._file      = None  # opened lazily on first write

        if self._enabled:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            log.info("Decision log: %s", self._log_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        snapshot:      dict[str, Any],
        decision:      dict[str, Any],
        latency_ms:    int,
        fallback_used: bool,
    ) -> None:
        """Append one DecisionLogEntry to the JSONL log file."""
        if not self._enabled:
            return

        entry = self._build_entry(snapshot, decision, latency_ms, fallback_used)
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Failed to write decision log: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_entry(
        self,
        snapshot:      dict[str, Any],
        decision:      dict[str, Any],
        latency_ms:    int,
        fallback_used: bool,
    ) -> dict[str, Any]:
        """Build the DecisionLogEntry dict per data-model.md entity 7."""
        eco   = snapshot.get("economy", {})
        units = snapshot.get("units",   {})
        thr   = snapshot.get("threats", {})

        # Condensed snapshot (subset of fields to keep log small)
        snapshot_summary = {
            "tick":          snapshot.get("tick"),
            "game_time_s":   snapshot.get("game_time_s"),
            "phase":         snapshot.get("phase"),
            "trigger_event": snapshot.get("trigger_event"),
            "mass_income":   eco.get("mass_income"),
            "land_military": units.get("land_military"),
            "near_base_thr": thr.get("near_base"),
            "factories":     units.get("factories"),
        }

        # Estimate token counts (rough heuristic: 1 token ≈ 4 chars)
        prompt_tokens   = len(decision.get("reasoning", "")) // 4
        response_tokens = len(json.dumps(decision)) // 4

        # ReAct iteration details
        iterations = decision.get("_iterations", [])
        react_summary = []
        for it in iterations:
            react_summary.append({
                "iter": it.get("iteration_num"),
                "tools": [tc.get("name") for tc in it.get("tool_calls", [])],
                "results": [
                    {"tool": r.get("tool_name"), "ok": r.get("success")}
                    for r in it.get("tool_results", [])
                ],
                "ms": it.get("latency_ms"),
            })

        return {
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "game_tick":          snapshot.get("tick", 0),
            "game_time_s":        snapshot.get("game_time_s", 0),
            "trigger_event":      snapshot.get("trigger_event", "periodic"),
            "model_used":         decision.get("_model_used", "unknown"),
            "snapshot_summary":   snapshot_summary,
            "prompt_tokens":      prompt_tokens,
            "response_tokens":    response_tokens,
            "response_latency_ms": latency_ms,
            "react_iterations":   react_summary if react_summary else None,
            "decision": {
                k: v for k, v in decision.items()
                if not k.startswith("_")
            },
            "fallback_used":      fallback_used,
            "reflex_actions_this_tick": snapshot.get("reflex_actions", []),
        }
