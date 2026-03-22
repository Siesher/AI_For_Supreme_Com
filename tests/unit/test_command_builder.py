# Unit tests: CommandBuilder
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

import pytest
from command_builder import CommandBuilder

@pytest.fixture
def cb():
    return CommandBuilder()

def test_valid_input_passthrough(cb):
    raw = {
        "strategy": "land_rush",
        "build_priority": ["land", "t2_mex"],
        "army_composition": {"land": 0.7, "air": 0.2, "navy": 0.1},
        "attack_direction": "north",
        "retreat_threshold": 0.35,
        "chat_message": "Атакую с севера",
        "reasoning": "Mass advantage, pushing north",
    }
    out = cb.build(raw)
    assert out["strategy"] == "land_rush"
    assert out["retreat_threshold"] == 0.35
    assert out["chat_message"] == "Атакую с севера"

def test_none_input_returns_defaults(cb):
    out = cb.build(None)
    assert out["strategy"] == "balanced"
    assert out["retreat_threshold"] == 0.3
    comp = out["army_composition"]
    assert abs(comp["land"] + comp["air"] + comp["navy"] - 1.0) < 0.01

def test_invalid_strategy_defaults_to_balanced(cb):
    out = cb.build({"strategy": "INVALID_STRATEGY"})
    assert out["strategy"] == "balanced"

def test_army_composition_renormalised(cb):
    raw = {"army_composition": {"land": 2.0, "air": 1.0, "navy": 1.0}}
    out = cb.build(raw)
    comp = out["army_composition"]
    total = comp["land"] + comp["air"] + comp["navy"]
    assert abs(total - 1.0) < 0.01

def test_army_composition_zero_falls_back(cb):
    raw = {"army_composition": {"land": 0.0, "air": 0.0, "navy": 0.0}}
    out = cb.build(raw)
    comp = out["army_composition"]
    assert abs(comp["land"] + comp["air"] + comp["navy"] - 1.0) < 0.01

def test_retreat_threshold_clamped(cb):
    assert cb.build({"retreat_threshold": 0.0})["retreat_threshold"] == 0.1
    assert cb.build({"retreat_threshold": 1.0})["retreat_threshold"] == 0.9
    assert cb.build({"retreat_threshold": 0.5})["retreat_threshold"] == 0.5

def test_chat_message_truncated_at_200(cb):
    long_msg = "А" * 300
    out = cb.build({"chat_message": long_msg})
    assert len(out["chat_message"]) <= 200

def test_chat_message_empty_string_becomes_none(cb):
    out = cb.build({"chat_message": "   "})
    assert out["chat_message"] is None

def test_invalid_attack_direction_set_to_none(cb):
    out = cb.build({"attack_direction": "diagonal"})
    assert out["attack_direction"] is None

def test_valid_attack_directions(cb):
    for d in ("north", "south", "east", "west", "none", None):
        out = cb.build({"attack_direction": d})
        assert out["attack_direction"] == d

def test_reasoning_truncated_at_500(cb):
    out = cb.build({"reasoning": "x" * 600})
    assert len(out["reasoning"]) <= 500

def test_build_priority_filters_invalid(cb):
    raw = {"build_priority": ["land", "INVALID", "t2_mex"]}
    out = cb.build(raw)
    assert "INVALID" not in out["build_priority"]
    assert "land" in out["build_priority"]
    assert "t2_mex" in out["build_priority"]

def test_build_priority_all_invalid_falls_back(cb):
    out = cb.build({"build_priority": ["GARBAGE", "JUNK"]})
    assert out["build_priority"] == ["land"]
