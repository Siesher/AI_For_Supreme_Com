"""Unit tests for FileIPCServer command-counter resync.

The game's Lua UI keeps its own command counter across a bridge restart, so the
bridge must derive the next index from the game log rather than blindly reset to
0 (which would desync a mid-match restart). These tests pin that behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

from file_ipc import FileIPCServer  # noqa: E402


@pytest.fixture()
def server(tmp_path: Path) -> FileIPCServer:
    """A FileIPCServer pointed at isolated temp log/ipc dirs."""
    return FileIPCServer(log_dir=tmp_path / "logs", ipc_dir=tmp_path / "ipc")


def _write_log(tmp_path: Path, name: str, lines: list[str]) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_fresh_match_log_resolves_to_zero(
    server: FileIPCServer, tmp_path: Path
) -> None:
    """A log with no command activity → counter 0 (fresh match)."""
    p = _write_log(
        tmp_path,
        "game_1.log",
        [
            "info: [LLMBotUI] gamemain hook loading",
            "info: [LLMBotUI] poll thread started (load-fallback)",
        ],
    )
    assert server._derive_counter_from_log(p) == 0


def test_waiting_line_sets_next_index(server: FileIPCServer, tmp_path: Path) -> None:
    """`waiting for cmd_N` → game wants N next."""
    p = _write_log(
        tmp_path,
        "game_1.log",
        ["info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0071.lua"],
    )
    assert server._derive_counter_from_log(p) == 71


def test_imported_line_sets_next_index(server: FileIPCServer, tmp_path: Path) -> None:
    """`Command imported: cmd_N` → game consumed N, wants N+1."""
    p = _write_log(
        tmp_path,
        "game_1.log",
        ["info: [LLMBotUI] Command imported: /mods/x/ipc/cmd_0070.lua (44 chars)"],
    )
    assert server._derive_counter_from_log(p) == 71


def test_mid_match_takes_max(server: FileIPCServer, tmp_path: Path) -> None:
    """Realistic interleaving: highest implied index wins."""
    lines = [
        "info: [LLMBotUI] Command imported: /mods/x/ipc/cmd_0068.lua (109 chars)",
        "info: [LLMBotUI] Command imported: /mods/x/ipc/cmd_0069.lua (44 chars)",
        "info: [LLMBotUI] Command imported: /mods/x/ipc/cmd_0070.lua (44 chars)",
        "info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0071.lua",
        "info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0071.lua",
    ]
    p = _write_log(tmp_path, "game_1.log", lines)
    assert server._derive_counter_from_log(p) == 71


def test_non_botui_cmd_refs_ignored(server: FileIPCServer, tmp_path: Path) -> None:
    """Only [LLMBotUI] lines count — stray cmd_*.lua mentions are ignored."""
    p = _write_log(
        tmp_path,
        "game_1.log",
        [
            "info: some other subsystem mentions cmd_9999.lua in passing",
            "info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0005.lua",
        ],
    )
    assert server._derive_counter_from_log(p) == 5


def test_resync_picks_latest_log(server: FileIPCServer, tmp_path: Path) -> None:
    """Resync uses the newest game log by mtime (the active match)."""
    import os
    import time

    old = _write_log(
        tmp_path,
        "game_old.log",
        ["info: [LLMBotUI] Command imported: /mods/x/ipc/cmd_0200.lua (44 chars)"],
    )
    new = _write_log(
        tmp_path,
        "game_new.log",
        ["info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0003.lua"],
    )
    # Force new > old in mtime regardless of write order.
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    server._resync_counter_from_latest_log()
    assert server._cmd_counter == 3


def test_cleanup_resyncs_not_zero(server: FileIPCServer, tmp_path: Path) -> None:
    """cleanup() deletes stale files but resyncs the counter to the game."""
    _write_log(
        tmp_path,
        "game_1.log",
        ["info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0071.lua"],
    )
    # Leftover stale command files from a previous bridge run.
    (server.ipc_dir / "cmd_0000.lua").write_text("LLMCmd = '{}'\n", encoding="utf-8")
    (server.ipc_dir / "cmd_0001.lua").write_text("LLMCmd = '{}'\n", encoding="utf-8")

    server.cleanup()

    assert list(server.ipc_dir.glob("cmd_*.lua")) == []  # stale files removed
    assert server._cmd_counter == 71  # resynced to the game, not 0


def test_next_write_uses_resynced_index(server: FileIPCServer, tmp_path: Path) -> None:
    """After resync, the next written file matches what the game waits for."""
    _write_log(
        tmp_path,
        "game_1.log",
        ["info: [LLMBotUI] poll: waiting for /mods/x/ipc/cmd_0071.lua"],
    )
    server.cleanup()
    server.write_command({"tool_calls": []})
    assert (server.ipc_dir / "cmd_0071.lua").exists()
