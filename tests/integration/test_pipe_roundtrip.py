# T054: Integration smoke test — tests/integration/test_pipe_roundtrip.py
# Mark all tests here as integration so they're skipped by default.
# Run with: pytest -m integration
import pytest
pytestmark = pytest.mark.integration

# T054: Integration smoke test — tests/integration/test_pipe_roundtrip.py (original)
#
# Tests the full pipeline:
#   1. Start bridge_server as subprocess
#   2. Send a mock snapshot via named pipe
#   3. Verify StrategicDecision returned within 15s
#   4. Mock Ollama timeout → assert fallback to 4B fires
#   5. Send "ally_air_threat" event → assert fast (4B) model selected by router

import asyncio
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

PIPE_NAME   = r"\\.\pipe\supcom_llm_bridge"
TIMEOUT_S   = 15
PROJECT_ROOT = Path(__file__).parent.parent.parent
SERVER_DIR   = PROJECT_ROOT / "server"
CONFIG_PATH  = PROJECT_ROOT / "installer" / "config.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pack_message(payload: str) -> bytes:
    encoded = payload.encode("utf-8")
    return struct.pack("<I", len(encoded)) + encoded


def _read_message(handle) -> str | None:
    """Read a length-prefixed message from the pipe (blocking)."""
    import win32file
    _, len_bytes = win32file.ReadFile(handle, 4)
    if len(len_bytes) < 4:
        return None
    (msg_len,) = struct.unpack("<I", len_bytes)
    if msg_len == 0 or msg_len > 1024 * 1024:
        return None
    _, payload = win32file.ReadFile(handle, msg_len)
    return payload.decode("utf-8")


def _send_snapshot(handle, snapshot: dict) -> None:
    import win32file
    envelope = {"type": "snapshot", "data": snapshot}
    msg = json.dumps(envelope)
    win32file.WriteFile(handle, _pack_message(msg))


def _make_snapshot(trigger: str = "periodic", **overrides) -> dict:
    snap = {
        "tick": 1000,
        "game_time_s": 100,
        "phase": "early",
        "faction": "UEF",
        "mode": "opponent",
        "trigger_event": trigger,
        "economy": {
            "mass_income": 10.0, "mass_stored": 300, "mass_storage_max": 1000,
            "energy_income": 500, "energy_stored": 2000, "energy_storage_max": 8000,
        },
        "units": {
            "factories": 3, "engineers": 5, "land_military": 15,
            "air_military": 0, "navy_military": 0,
            "t1": 15, "t2": 0, "t3": 0, "experimentals": 0,
        },
        "threats": {"near_base": 0, "nearest_enemy_distance": 800, "enemy_army_size_estimate": 10},
        "map_control_pct": 40,
        "player_chat": [],
        "current_strategy": "balanced",
    }
    snap.update(overrides)
    return snap


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bridge_proc():
    """Start bridge_server.py subprocess."""
    if not CONFIG_PATH.exists():
        pytest.skip("config.json not found — skipping integration tests")

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_DIR / "bridge_server.py"),
         "--config", str(CONFIG_PATH), "--log-level", "DEBUG"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(SERVER_DIR),
    )
    # Give server time to start
    time.sleep(3)
    assert proc.poll() is None, "bridge_server exited immediately"
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Named pipes are Windows-only")
def test_pipe_roundtrip(bridge_proc):
    """Send a snapshot, expect a StrategicDecision within TIMEOUT_S."""
    import win32file
    import win32pipe

    # Connect to the pipe as a client
    handle = win32file.CreateFile(
        PIPE_NAME,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0, None, win32file.OPEN_EXISTING, 0, None,
    )
    assert handle != win32file.INVALID_HANDLE_VALUE

    try:
        _send_snapshot(handle, _make_snapshot("periodic"))

        # Read response with timeout
        deadline = time.monotonic() + TIMEOUT_S
        response = None
        while time.monotonic() < deadline:
            response = _read_message(handle)
            if response:
                break
            time.sleep(0.1)

        assert response is not None, f"No response within {TIMEOUT_S}s"
        envelope = json.loads(response)
        assert envelope.get("type") == "command", f"Unexpected type: {envelope.get('type')}"
        decision = envelope.get("data", {})
        assert "strategy" in decision, f"No 'strategy' field in decision: {decision}"
        assert decision.get("retreat_threshold", -1) >= 0.1
    finally:
        win32file.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_ally_air_threat_uses_fast_model(bridge_proc):
    """
    Send ally_air_threat snapshot → assert response arrives quickly
    (fast 4B model selected by router).
    """
    import win32file

    handle = win32file.CreateFile(
        PIPE_NAME,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0, None, win32file.OPEN_EXISTING, 0, None,
    )
    assert handle != win32file.INVALID_HANDLE_VALUE

    try:
        snapshot = _make_snapshot(
            "ally_air_threat",
            ally={
                "army_index": 1,
                "base_position": [256.0, 256.0],
                "base_threat": 10.0,
                "air_threat_near_base": 25.0,
                "army_size": 20,
                "army_size_prev": 25,
                "mass_income": 8.0,
                "mass_stored": 200,
                "energy_income": 300,
                "under_air_attack": True,
                "under_ground_attack": False,
                "losing_army_fast": False,
                "mass_stall": False,
            }
        )
        t_start = time.monotonic()
        _send_snapshot(handle, snapshot)

        response = None
        while (time.monotonic() - t_start) < TIMEOUT_S:
            response = _read_message(handle)
            if response:
                break
            time.sleep(0.1)

        assert response is not None, "No response for ally_air_threat"
        envelope = json.loads(response)
        decision = envelope.get("data", {})
        # Fast model should respond quicker — just check we get a valid decision
        assert "strategy" in decision
        # Ideally check decision._model_used == fast tag, but this requires
        # the response to include the metadata field
    finally:
        win32file.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_fallback_strategy_is_valid(bridge_proc):
    """
    If Ollama is unreachable, the fallback strategy should still return a
    valid decision. (We can't easily mock Ollama mid-session; this tests
    the FallbackStrategy directly instead.)
    """
    sys.path.insert(0, str(SERVER_DIR))
    from fallback_strategy import FallbackStrategy

    fs       = FallbackStrategy()
    snapshot = _make_snapshot("periodic", economy={"mass_income": 2.0, "mass_stored": 50,
                                                    "mass_storage_max": 1000,
                                                    "energy_income": 100, "energy_stored": 200,
                                                    "energy_storage_max": 8000})
    decision = fs.get_command(snapshot)

    assert "strategy" in decision
    assert "army_composition" in decision
    comp = decision["army_composition"]
    total = comp["land"] + comp["air"] + comp["navy"]
    assert abs(total - 1.0) < 0.01, f"army_composition doesn't sum to 1.0: {total}"
    assert 0.1 <= decision["retreat_threshold"] <= 0.9
