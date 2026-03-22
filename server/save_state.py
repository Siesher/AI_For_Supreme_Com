# T049: Save State — server/save_state.py
#
# Persists and restores BotSaveState (data-model.md entity 6).
# Hooks into game save/load via pipe message types "save"/"load".

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_SAVE_DIR_DEFAULT = r"%APPDATA%\FAForever\saves"


class SaveState:
    """Manages save/load of bot state alongside FAF game saves."""

    def __init__(self, config: dict[str, Any]) -> None:
        save_dir = os.path.expandvars(_SAVE_DIR_DEFAULT)
        self._save_dir  = Path(save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)

        # In-memory performance metrics
        self._total_llm_queries   = 0
        self._fallback_activations = 0
        self._latency_samples: list[int] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, data: dict[str, Any], save_name: str = "default") -> None:
        """
        Persist BotSaveState to disk.
        Called when bridge_server receives a {"type":"save"} message.
        """
        state_file = self._state_path(save_name)
        avg_latency = (
            sum(self._latency_samples) / len(self._latency_samples)
            if self._latency_samples else 0
        )
        bot_save_state = {
            "save_tick":           data.get("tick", 0),
            "save_timestamp":      datetime.now(timezone.utc).isoformat(),
            "current_strategy":    data.get("current_strategy"),
            "conversation_history": data.get("conversation_history", []),
            "llm_context_window":  data.get("llm_context_window", ""),
            "performance_metrics": {
                "total_llm_queries":     self._total_llm_queries,
                "fallback_activations":  self._fallback_activations,
                "avg_response_ms":       round(avg_latency, 1),
            },
        }
        try:
            with state_file.open("w", encoding="utf-8") as f:
                json.dump(bot_save_state, f, ensure_ascii=False, indent=2)
            log.info("Saved bot state to %s", state_file)
        except OSError as exc:
            log.error("Failed to save bot state: %s", exc)

    def load(self, save_name: str = "default") -> Optional[dict[str, Any]]:
        """
        Restore BotSaveState from disk.
        Called when bridge_server receives a {"type":"load"} message.
        Returns the state dict, or None if no save exists.
        """
        state_file = self._state_path(save_name)
        if not state_file.exists():
            log.info("No saved state for '%s' at %s", save_name, state_file)
            return None
        try:
            with state_file.open(encoding="utf-8") as f:
                state = json.load(f)
            log.info("Loaded bot state from %s (tick=%s)", state_file, state.get("save_tick"))
            return state
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to load bot state: %s", exc)
            return None

    def record_query(self, latency_ms: int, fallback_used: bool) -> None:
        """Update performance metrics after each LLM query."""
        self._total_llm_queries += 1
        if fallback_used:
            self._fallback_activations += 1
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > 200:
            self._latency_samples = self._latency_samples[-200:]

    # ------------------------------------------------------------------
    def _state_path(self, save_name: str) -> Path:
        safe_name = "".join(c for c in save_name if c.isalnum() or c in "._-")
        return self._save_dir / f"llm_bot_state_{safe_name}.json"
