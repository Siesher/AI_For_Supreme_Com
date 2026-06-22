"""Tests for all 6 improvements: validation, dedup, scoring, adaptive poll, phase prompts."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

import pytest
from tools import validate_tool_call, validate_and_filter, deduplicate, ALL_TOOL_NAMES
from decision_memory import DecisionMemory, DecisionSummary
from state_processor import StateProcessor, PHASE_PROMPTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CFG = {"bot": {"difficulty": "normal", "playstyle": "balanced", "chat_language": "en"}}


def _snap(**overrides) -> dict:
    snap = {
        "tick": 1200, "game_time_s": 120, "phase": "early",
        "faction": "UEF", "mode": "opponent", "trigger_event": "periodic",
        "economy": {
            "mass_income": 8.0, "mass_stored": 200, "mass_storage_max": 1000,
            "energy_income": 400, "energy_stored": 1500, "energy_storage_max": 8000,
        },
        "units": {
            "factories": 2, "engineers": 4, "land_military": 8,
            "air_military": 0, "navy_military": 0,
            "t1": 8, "t2": 0, "t3": 0, "experimentals": 0,
        },
        "threats": {"near_base": 0, "nearest_enemy_distance": 900, "enemy_army_size_estimate": 5},
        "map_control_pct": 35, "player_chat": [], "current_strategy": "balanced",
    }
    snap.update(overrides)
    return snap


# ===========================================================================
# 1. Tool call validation
# ===========================================================================

class TestValidation:
    def test_valid_attack(self) -> None:
        ok, err = validate_tool_call({"name": "attack", "args": {"unit_type": "land"}})
        assert ok
        assert err == ""

    def test_unknown_tool_rejected(self) -> None:
        ok, err = validate_tool_call({"name": "nuke_everything", "args": {}})
        assert not ok
        assert "unknown tool" in err

    def test_missing_required_param(self) -> None:
        ok, err = validate_tool_call({"name": "attack", "args": {}})
        assert not ok
        assert "missing required" in err

    def test_invalid_enum_value(self) -> None:
        ok, err = validate_tool_call({"name": "attack", "args": {"unit_type": "submarine"}})
        assert not ok
        assert "invalid value" in err

    def test_valid_enum_values_pass(self) -> None:
        for unit_type in ["land", "air", "all"]:
            ok, _ = validate_tool_call({"name": "attack", "args": {"unit_type": unit_type}})
            assert ok, f"Expected valid for unit_type={unit_type}"

    def test_observation_no_params_valid(self) -> None:
        ok, _ = validate_tool_call({"name": "get_enemy_army", "args": {}})
        assert ok

    def test_noop_valid(self) -> None:
        ok, _ = validate_tool_call({"name": "noop", "args": {"reasoning": "all clear"}})
        assert ok

    def test_filter_drops_invalid(self) -> None:
        calls = [
            {"name": "attack", "args": {"unit_type": "land"}},
            {"name": "bogus", "args": {}},
            {"name": "scout", "args": {"direction": "north"}},
        ]
        result = validate_and_filter(calls)
        assert len(result) == 2
        assert result[0]["name"] == "attack"
        assert result[1]["name"] == "scout"

    def test_all_known_tools_registered(self) -> None:
        expected = {
            "get_enemy_army", "get_threat_at", "get_mass_points",
            "get_my_factories", "get_map_control",
            "attack", "defend", "scout", "set_strategy",
            "build_units", "reclaim", "chat", "noop",
        }
        assert ALL_TOOL_NAMES == expected


# ===========================================================================
# 2. Deduplication
# ===========================================================================

class TestDeduplication:
    def test_exact_duplicate_removed(self) -> None:
        calls = [
            {"name": "get_enemy_army", "args": {}},
            {"name": "get_enemy_army", "args": {}},
        ]
        result = deduplicate(calls)
        assert len(result) == 1

    def test_different_args_kept(self) -> None:
        calls = [
            {"name": "get_threat_at", "args": {"position": "own_base"}},
            {"name": "get_threat_at", "args": {"position": "enemy_base"}},
        ]
        result = deduplicate(calls)
        assert len(result) == 2

    def test_different_tools_kept(self) -> None:
        calls = [
            {"name": "get_enemy_army", "args": {}},
            {"name": "get_mass_points", "args": {}},
        ]
        result = deduplicate(calls)
        assert len(result) == 2

    def test_triple_duplicate(self) -> None:
        calls = [{"name": "noop", "args": {"reasoning": "wait"}}] * 3
        result = deduplicate(calls)
        assert len(result) == 1

    def test_order_preserved(self) -> None:
        calls = [
            {"name": "scout", "args": {"direction": "north"}},
            {"name": "attack", "args": {"unit_type": "land"}},
            {"name": "scout", "args": {"direction": "north"}},
        ]
        result = deduplicate(calls)
        assert len(result) == 2
        assert result[0]["name"] == "scout"
        assert result[1]["name"] == "attack"


# ===========================================================================
# 3. Decision scoring
# ===========================================================================

class TestDecisionScoring:
    def _make_memory_with_cycle(self) -> DecisionMemory:
        mem = DecisionMemory(max_entries=10)
        summary = DecisionSummary(
            cycle_id=1, game_time_s=120, trigger="periodic",
            observations=["get_enemy_army"], actions=["attack(unit_type=land)"],
        )
        mem._next_cycle_id = 2
        mem.add_cycle(summary)
        return mem

    def test_army_growth_positive_score(self) -> None:
        mem = self._make_memory_with_cycle()
        prev = _snap(units={"land_military": 10, "air_military": 0})
        curr = _snap(units={"land_military": 20, "air_military": 0})
        mem.score_last_cycle(prev, curr)
        assert mem.entries[-1].score > 0

    def test_army_loss_negative_score(self) -> None:
        mem = self._make_memory_with_cycle()
        prev = _snap(units={"land_military": 20, "air_military": 0})
        curr = _snap(units={"land_military": 5, "air_military": 0})
        mem.score_last_cycle(prev, curr)
        assert mem.entries[-1].score < 0

    def test_threat_reduction_positive(self) -> None:
        mem = self._make_memory_with_cycle()
        prev = _snap(threats={"near_base": 30, "nearest_enemy_distance": 200, "enemy_army_size_estimate": 10})
        curr = _snap(threats={"near_base": 5, "nearest_enemy_distance": 500, "enemy_army_size_estimate": 5})
        mem.score_last_cycle(prev, curr)
        assert mem.entries[-1].score > 0

    def test_economy_growth_positive(self) -> None:
        mem = self._make_memory_with_cycle()
        prev = _snap(economy={"mass_income": 5.0, "mass_stored": 100, "mass_storage_max": 1000,
                               "energy_income": 200, "energy_stored": 500, "energy_storage_max": 8000})
        curr = _snap(economy={"mass_income": 10.0, "mass_stored": 300, "mass_storage_max": 1000,
                               "energy_income": 400, "energy_stored": 1500, "energy_storage_max": 8000})
        mem.score_last_cycle(prev, curr)
        assert mem.entries[-1].score > 0

    def test_no_change_zero_score(self) -> None:
        mem = self._make_memory_with_cycle()
        snap = _snap()
        mem.score_last_cycle(snap, snap)
        assert mem.entries[-1].score == 0.0

    def test_score_in_prompt_block(self) -> None:
        mem = self._make_memory_with_cycle()
        mem._entries[-1].score = 0.5
        block = mem.to_prompt_block()
        assert "score=+0.5" in block

    def test_score_clamped(self) -> None:
        mem = self._make_memory_with_cycle()
        # Everything bad at once
        prev = _snap(
            units={"land_military": 30, "air_military": 10},
            threats={"near_base": 0, "nearest_enemy_distance": 900, "enemy_army_size_estimate": 5},
            economy={"mass_income": 15.0, "mass_stored": 500, "mass_storage_max": 1000,
                      "energy_income": 600, "energy_stored": 3000, "energy_storage_max": 8000},
            map_control_pct=60,
        )
        curr = _snap(
            units={"land_military": 5, "air_military": 0},
            threats={"near_base": 50, "nearest_enemy_distance": 100, "enemy_army_size_estimate": 40},
            economy={"mass_income": 3.0, "mass_stored": 50, "mass_storage_max": 1000,
                      "energy_income": 100, "energy_stored": 200, "energy_storage_max": 8000},
            map_control_pct=20,
        )
        mem.score_last_cycle(prev, curr)
        assert mem.entries[-1].score >= -1.0
        assert mem.entries[-1].score <= 1.0

    def test_empty_memory_score_noop(self) -> None:
        mem = DecisionMemory(max_entries=10)
        # Should not crash on empty memory
        mem.score_last_cycle(_snap(), _snap())


# ===========================================================================
# 4. Adaptive poll interval
# ===========================================================================

class TestAdaptivePolling:
    """Test _adaptive_poll_interval from bridge_server."""

    @pytest.fixture(autouse=True)
    def _import_func(self):
        from bridge_server import _adaptive_poll_interval
        self.fn = _adaptive_poll_interval

    def test_crisis_short_interval(self) -> None:
        snap = _snap(threats={"near_base": 30, "nearest_enemy_distance": 100, "enemy_army_size_estimate": 20})
        interval = self.fn(snap, {"bot": {"poll_interval_fast_s": 20}})
        assert interval <= 10

    def test_calm_long_interval(self) -> None:
        snap = _snap(
            threats={"near_base": 0, "nearest_enemy_distance": 1500, "enemy_army_size_estimate": 3},
            units={"land_military": 40, "air_military": 5, "factories": 4, "engineers": 6,
                    "navy_military": 0, "t1": 30, "t2": 10, "t3": 0, "experimentals": 0},
        )
        interval = self.fn(snap, {"bot": {"poll_interval_fast_s": 20}})
        assert interval >= 25

    def test_default_interval(self) -> None:
        snap = _snap()
        interval = self.fn(snap, {"bot": {"poll_interval_fast_s": 20}})
        assert interval == 20

    def test_priority_event_shortens(self) -> None:
        snap = _snap(trigger_event="army_lost")
        interval = self.fn(snap, {"bot": {"poll_interval_fast_s": 20}})
        assert interval < 20

    def test_elevated_threat(self) -> None:
        snap = _snap(threats={"near_base": 8, "nearest_enemy_distance": 350, "enemy_army_size_estimate": 12})
        interval = self.fn(snap, {"bot": {"poll_interval_fast_s": 20}})
        assert interval <= 12


# ===========================================================================
# 5. Phase prompts
# ===========================================================================

class TestPhasePrompts:
    @pytest.fixture
    def sp(self) -> StateProcessor:
        return StateProcessor(CFG)

    def test_early_phase_prompt(self, sp) -> None:
        snap = _snap(phase="early")
        msgs = sp.build_messages(snap, CFG, [])
        system = msgs[0]["content"]
        assert "EARLY GAME" in system
        assert "scout" in system.lower()

    def test_mid_phase_prompt(self, sp) -> None:
        snap = _snap(phase="mid")
        msgs = sp.build_messages(snap, CFG, [])
        system = msgs[0]["content"]
        assert "MID GAME" in system
        assert "T2" in system

    def test_late_phase_prompt(self, sp) -> None:
        snap = _snap(phase="late")
        msgs = sp.build_messages(snap, CFG, [])
        system = msgs[0]["content"]
        assert "LATE GAME" in system
        assert "T3" in system

    def test_unknown_phase_defaults_to_early(self, sp) -> None:
        snap = _snap(phase="unknown_phase")
        msgs = sp.build_messages(snap, CFG, [])
        system = msgs[0]["content"]
        assert "EARLY GAME" in system

    def test_all_phases_defined(self) -> None:
        assert set(PHASE_PROMPTS.keys()) == {"early", "mid", "late"}

    def test_scoring_instruction_in_prompt(self, sp) -> None:
        msgs = sp.build_messages(_snap(), CFG, [])
        system = msgs[0]["content"]
        assert "DECISION HISTORY" in system or "positive scores" in system
