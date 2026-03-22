# Unit tests: FallbackStrategy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

import pytest
from fallback_strategy import FallbackStrategy

@pytest.fixture
def fs():
    return FallbackStrategy()

def _snap(**kwargs):
    base = {
        "economy": {"mass_income": 10.0, "mass_stored": 300, "mass_storage_max": 1000,
                    "energy_income": 500, "energy_stored": 2000, "energy_storage_max": 8000},
        "units":   {"factories": 4, "engineers": 5, "land_military": 10,
                    "air_military": 0, "navy_military": 0,
                    "t1": 10, "t2": 0, "t3": 0, "experimentals": 0},
        "threats": {"near_base": 0, "nearest_enemy_distance": 800, "enemy_army_size_estimate": 5},
        "mode": "opponent",
    }
    base.update(kwargs)
    return base

def _valid(decision):
    """Assert a decision is structurally valid."""
    assert "strategy" in decision
    comp = decision.get("army_composition", {})
    total = comp.get("land", 0) + comp.get("air", 0) + comp.get("navy", 0)
    assert abs(total - 1.0) < 0.01, f"army_composition sums to {total}"
    rt = decision.get("retreat_threshold", -1)
    assert 0.1 <= rt <= 0.9

def test_base_under_attack_returns_defend(fs):
    snap = _snap(threats={"near_base": 60, "nearest_enemy_distance": 200, "enemy_army_size_estimate": 30})
    out = fs.get_command(snap)
    assert out["strategy"] == "defend"
    _valid(out)

def test_few_factories_returns_build(fs):
    snap = _snap(units={"factories": 1, "engineers": 2, "land_military": 5,
                         "air_military": 0, "navy_military": 0,
                         "t1": 5, "t2": 0, "t3": 0, "experimentals": 0})
    out = fs.get_command(snap)
    assert out["strategy"] == "build_factories"
    _valid(out)

def test_mass_stall_returns_reclaim(fs):
    snap = _snap(economy={"mass_income": 1.5, "mass_stored": 50, "mass_storage_max": 1000,
                           "energy_income": 200, "energy_stored": 500, "energy_storage_max": 8000})
    out = fs.get_command(snap)
    assert out["strategy"] == "reclaim"
    _valid(out)

def test_large_army_returns_attack(fs):
    snap = _snap(units={"factories": 5, "engineers": 8, "land_military": 25,
                         "air_military": 0, "navy_military": 0,
                         "t1": 25, "t2": 0, "t3": 0, "experimentals": 0})
    out = fs.get_command(snap)
    assert out["strategy"] == "attack"
    _valid(out)

def test_default_returns_balanced(fs):
    # Normal state — no stall, no threat, moderate army
    out = fs.get_command(_snap())
    assert out["strategy"] == "balanced"
    _valid(out)

def test_priority_order_defend_over_build(fs):
    # Both factories < 3 AND near_base threat > 50 → defend wins (higher priority)
    snap = _snap(
        units={"factories": 1, "engineers": 1, "land_military": 1,
               "air_military": 0, "navy_military": 0,
               "t1": 1, "t2": 0, "t3": 0, "experimentals": 0},
        threats={"near_base": 80, "nearest_enemy_distance": 100, "enemy_army_size_estimate": 50},
    )
    out = fs.get_command(snap)
    assert out["strategy"] == "defend"

def test_always_returns_valid_structure(fs):
    """Run with empty/minimal snapshot — should never raise."""
    out = fs.get_command({})
    _valid(out)
