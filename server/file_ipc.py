"""File-based IPC between Python bridge and SupCom FAF game.

FAF sandboxes BOTH sim and UI Lua — no io, no os, no loadlib.
Available channels:
  Outbound (snapshot → Python):
    Sim → Sync.LLMSnapshot → UI OnSync → LOG("[LLM_SNAP]json") → game log → Python
  Inbound (command → game):
    Python → writes cmd_NNNN.lua to mod dir → UI import() → SimCallback → Sim

This module implements the Python side:
  - Tails the game log to extract [LLM_SNAP] lines
  - Writes command Lua files for the UI to import
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Game log directory
FAF_LOG_DIR = Path(os.environ.get("APPDATA", "")) / "Forged Alliance Forever" / "logs"
# Mod IPC directory (VFS-accessible)
MOD_IPC_DIR = Path(r"C:\FAFData\mods\supcom-llm-ai-bot\ipc")

SNAP_PATTERN = re.compile(r"info: \[LLM_SNAP\](.+)")
# Game UI logs command-file polling; we mine these to resync the command
# counter when the bridge restarts mid-match. The game's Lua state keeps its
# own counter across a bridge restart, so a blind reset to 0 desyncs it.
#   [LLMBotUI] Command imported: .../cmd_0070.lua (44 chars)  -> consumed 0070
#   [LLMBotUI] poll: waiting for .../cmd_0071.lua             -> wants 0071 next
CMD_REF_PATTERN = re.compile(r"cmd_(\d+)\.lua")


class FileIPCServer:
    """Python-side IPC: tails game log for snapshots, writes Lua command files."""

    def __init__(
        self,
        log_dir: Path = FAF_LOG_DIR,
        ipc_dir: Path = MOD_IPC_DIR,
    ) -> None:
        self.log_dir = log_dir
        self.ipc_dir = ipc_dir
        self._log_file: Optional[Path] = None
        self._log_handle = None
        self._log_pos: int = 0
        self._cmd_counter: int = 0

        self._ensure_ipc_dir()

    def _ensure_ipc_dir(self) -> None:
        self.ipc_dir.mkdir(parents=True, exist_ok=True)
        log.info("File IPC: cmd dir = %s", self.ipc_dir)

    def cleanup(self) -> None:
        """Remove stale command files and resync the command counter.

        Deletes leftover cmd_*.lua, then aligns ``_cmd_counter`` with the
        running game (if any) via the latest game log. A blind reset to 0 would
        desync on a mid-match bridge restart: the game's Lua state keeps its own
        counter across the restart, so it would keep waiting for cmd_NNNN while a
        fresh bridge wrote cmd_0000. A fresh match's log shows no command
        activity, so the counter correctly resolves to 0.
        """
        if self.ipc_dir.exists():
            for f in self.ipc_dir.glob("cmd_*.lua"):
                try:
                    f.unlink()
                except OSError:
                    pass
            log.info("File IPC: cleaned up old command files")
        self._resync_counter_from_latest_log()

    def _derive_counter_from_log(self, log_path: Path) -> int:
        """Derive the next command index the game UI expects, from a game log.

        Scans the whole log for the highest command index implied by the UI's
        poll lines (``Command imported: cmd_N`` → wants N+1; ``waiting for
        cmd_N`` → wants N). Command indices are monotonic within a match and
        each match gets its own game_*.log, so the maximum is authoritative.

        Args:
            log_path: Path to a ``game_*.log`` file.

        Returns:
            The command index the bridge should write next to stay aligned with
            the running game (0 if the log shows no command activity).
        """
        next_idx = 0
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "[LLMBotUI]" not in line:
                        continue
                    m = CMD_REF_PATTERN.search(line)
                    if not m:
                        continue
                    n = int(m.group(1))
                    candidate = n + 1 if "Command imported" in line else n
                    if candidate > next_idx:
                        next_idx = candidate
        except OSError as exc:
            log.warning("Counter resync: could not read %s: %s", log_path, exc)
        return next_idx

    def _resync_counter_from_latest_log(self) -> None:
        """Align ``_cmd_counter`` with the running game's current command index.

        Called at startup and on game-log rotation so a bridge restart mid-match
        (or a brand-new match) does not desync the file-based command counter.
        """
        latest = self._find_latest_game_log()
        derived = self._derive_counter_from_log(latest) if latest else 0
        if derived != self._cmd_counter:
            log.info(
                "File IPC: command counter resynced %d -> %d (from %s)",
                self._cmd_counter,
                derived,
                latest.name if latest else "<none>",
            )
        self._cmd_counter = derived

    # ------------------------------------------------------------------
    # Outbound: read snapshots from game log
    # ------------------------------------------------------------------

    def _find_latest_game_log(self) -> Optional[Path]:
        """Find the most recently modified game_*.log file."""
        if not self.log_dir.exists():
            return None
        logs = sorted(
            self.log_dir.glob("game_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return logs[0] if logs else None

    def _open_log(self) -> bool:
        """Open (or reopen) the latest game log and seek to end."""
        latest = self._find_latest_game_log()
        if not latest:
            return False

        if latest != self._log_file:
            # New game log detected (bridge start or new match). Resync the
            # command counter to the game's position BEFORE tailing, so a
            # mid-match restart stays aligned and a fresh match resets to 0.
            self._cmd_counter = self._derive_counter_from_log(latest)
            if self._log_handle:
                self._log_handle.close()
            self._log_file = latest
            self._log_handle = open(latest, "r", encoding="utf-8", errors="replace")
            # Seek to end — only read NEW lines
            self._log_handle.seek(0, 2)
            self._log_pos = self._log_handle.tell()
            log.info(
                "File IPC: tailing game log %s (cmd counter=%d)",
                latest.name,
                self._cmd_counter,
            )
            return True

        return self._log_handle is not None

    def read_snapshot(self) -> Optional[dict]:
        """Read the next [LLM_SNAP] line from the game log.

        Returns parsed snapshot dict, or None if no new snapshot.
        """
        if not self._log_handle:
            if not self._open_log():
                return None

        try:
            # Read new lines
            new_data = self._log_handle.read()
            if not new_data:
                # Check if log file was rotated (new game started)
                latest = self._find_latest_game_log()
                if latest and latest != self._log_file:
                    self._open_log()
                return None

            # Find [LLM_SNAP] lines — use the LAST one (most recent)
            last_snap = None
            for line in new_data.splitlines():
                m = SNAP_PATTERN.search(line)
                if m:
                    try:
                        last_snap = json.loads(m.group(1))
                    except json.JSONDecodeError as exc:
                        log.warning("Snapshot JSON error: %s", exc)

            return last_snap

        except OSError as exc:
            log.warning("Game log read error: %s", exc)
            self._log_handle = None
            return None

    # ------------------------------------------------------------------
    # Inbound: write command Lua files
    # ------------------------------------------------------------------

    def write_command(self, command: dict) -> bool:
        """Write a command as a Lua file that the game's UI can import().

        File: ipc/cmd_NNNN.lua  containing  return '<json>'
        The UI layer polls for these files using import().
        """
        try:
            json_str = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
            # Escape backslashes, single quotes and newlines for a Lua string literal.
            # NOTE: assign to a module global (LLMCmd), NOT `return` — FA's import()
            # returns the module ENV table and discards the chunk's return value, so
            # the UI reads the payload via import(path).LLMCmd.
            lua_safe = (
                json_str.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
            )
            lua_content = f"LLMCmd = '{lua_safe}'\n"

            filename = f"cmd_{self._cmd_counter:04d}.lua"
            filepath = self.ipc_dir / filename
            filepath.write_text(lua_content, encoding="utf-8")

            log.info("File IPC: wrote %s (%d bytes)", filename, len(json_str))
            self._cmd_counter += 1
            return True

        except OSError as exc:
            log.error("Command write error: %s", exc)
            return False

    @property
    def snapshot_path(self) -> Optional[Path]:
        """Current game log path being tailed."""
        return self._log_file

    @property
    def command_path(self) -> Path:
        """IPC directory for command files."""
        return self.ipc_dir

    def close(self) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


# ---------------------------------------------------------------------------
# Observation resolver — approximates live queries from snapshot data
# ---------------------------------------------------------------------------


def resolve_observation(tool_name: str, args: dict, snapshot: dict) -> dict:
    """Resolve an observation tool call from cached snapshot data."""
    if tool_name == "get_enemy_army":
        threats = snapshot.get("threats", {})
        estimate = threats.get("enemy_army_size_estimate", 0)
        return {
            "success": True,
            "data": {
                "land": int(estimate * 0.6),
                "air": int(estimate * 0.25),
                "navy": int(estimate * 0.15),
                "total": estimate,
                "has_experimentals": estimate > 30,
            },
        }

    if tool_name == "get_threat_at":
        threats = snapshot.get("threats", {})
        position = args.get("position", "own_base")
        if position == "own_base":
            near = threats.get("near_base", 0)
            return {
                "success": True,
                "data": {
                    "overall": near,
                    "land": int(near * 0.7),
                    "air": int(near * 0.3),
                    "structures": 0,
                },
            }
        dist = threats.get("nearest_enemy_distance", 9999)
        threat_est = max(0, 50 - dist // 20)
        return {
            "success": True,
            "data": {
                "overall": threat_est,
                "land": int(threat_est * 0.7),
                "air": int(threat_est * 0.3),
                "structures": 0,
            },
        }

    if tool_name == "get_mass_points":
        pct = snapshot.get("map_control_pct", 50)
        total = 20
        controlled = int(total * pct / 100)
        return {
            "success": True,
            "data": {
                "total": total,
                "controlled": controlled,
                "uncontrolled": total - controlled,
                "contested": 0,
            },
        }

    if tool_name == "get_my_factories":
        units = snapshot.get("units", {})
        fac = units.get("factories", 0)
        phase = snapshot.get("phase", "early")
        if phase == "late":
            t1, t2 = max(1, fac // 3), fac // 3
            t3 = fac - t1 - t2
        elif phase == "mid":
            t1 = max(1, fac // 2)
            t2, t3 = fac - t1, 0
        else:
            t1, t2, t3 = fac, 0, 0
        return {
            "success": True,
            "data": {
                "t1_land": t1,
                "t2_land": t2,
                "t3_land": t3,
                "t1_air": min(1, fac // 3),
                "t2_air": 0,
                "t3_air": 0,
                "idle": 0,
                "building": fac,
            },
        }

    if tool_name == "get_map_control":
        pct = snapshot.get("map_control_pct", 50)
        own_thresh, enemy_thresh = 55, 35
        return {
            "success": True,
            "data": {
                "pct": pct,
                "zones": [
                    {
                        "name": z,
                        "control": "own"
                        if pct > own_thresh
                        else ("enemy" if pct < enemy_thresh else "contested"),
                    }
                    for z in ("north", "south", "east", "west", "center")
                ],
            },
        }

    return {"success": False, "error": f"unknown observation: {tool_name}"}
