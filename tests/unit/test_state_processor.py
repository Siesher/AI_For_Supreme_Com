# Unit tests: StateProcessor
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

import pytest
from state_processor import StateProcessor

CFG = {
    "llm":  {"model_deep": "qwen3.5:9b", "model_fast": "qwen3.5:4b"},
    "bot":  {"difficulty": "normal", "playstyle": "balanced",
             "chat_language": "auto", "poll_interval_fast_s": 20},
    "bridge": {"timeout_s": 10},
}

def _snap(**overrides):
    base = {
        "tick": 1200, "game_time_s": 120, "phase": "early",
        "faction": "UEF", "mode": "opponent", "trigger_event": "periodic",
        "economy": {"mass_income": 8.0, "mass_stored": 200, "mass_storage_max": 1000,
                    "energy_income": 400, "energy_stored": 1500, "energy_storage_max": 8000},
        "units": {"factories": 2, "engineers": 4, "land_military": 8,
                  "air_military": 0, "navy_military": 0,
                  "t1": 8, "t2": 0, "t3": 0, "experimentals": 0},
        "threats": {"near_base": 0, "nearest_enemy_distance": 900, "enemy_army_size_estimate": 5},
        "map_control_pct": 35, "player_chat": [], "current_strategy": "balanced",
    }
    base.update(overrides)
    return base

@pytest.fixture
def sp():
    return StateProcessor(CFG)

def test_no_think_at_start(sp):
    prompt = sp.build_prompt(_snap(), CFG, [])
    assert prompt.startswith("/no_think"), "Prompt must begin with /no_think"

def test_prompt_contains_game_state(sp):
    prompt = sp.build_prompt(_snap(), CFG, [])
    assert "GAME STATE" in prompt
    assert "Economy" in prompt
    assert "Military" in prompt

def test_prompt_contains_phase_and_strategy(sp):
    prompt = sp.build_prompt(_snap(), CFG, [])
    assert "EARLY GAME" in prompt
    assert "observe" in prompt.lower() or "act" in prompt.lower()

def test_russian_chat_sets_russian_language(sp):
    snap = _snap(player_chat=["Атакуй с севера"])
    prompt = sp.build_prompt(snap, CFG, [])
    assert "Russian" in prompt or "ru" in prompt.lower() or "коротком" in prompt

def test_english_chat_keeps_english(sp):
    snap = _snap(player_chat=["attack north"])
    prompt = sp.build_prompt(snap, CFG, [])
    assert "English" in prompt or "briefly" in prompt

def test_auto_language_default_is_english(sp):
    prompt = sp.build_prompt(_snap(), CFG, [])
    assert "English" in prompt or "briefly" in prompt

def test_difficulty_injected(sp):
    for diff in ("easy", "normal", "hard"):
        cfg = {**CFG, "bot": {**CFG["bot"], "difficulty": diff}}
        prompt = sp.build_prompt(_snap(), cfg, [])
        assert diff in prompt.lower()

def test_playstyle_injected(sp):
    for style in ("rush", "turtle", "air"):
        cfg = {**CFG, "bot": {**CFG["bot"], "playstyle": style}}
        prompt = sp.build_prompt(_snap(), cfg, [])
        assert style in prompt.lower()

def test_ally_mode_includes_ally_block(sp):
    ally = {
        "army_index": 1, "base_position": [256, 256],
        "base_threat": 10, "air_threat_near_base": 20,
        "army_size": 15, "army_size_prev": 20, "mass_income": 4.0,
        "mass_stored": 100, "energy_income": 200,
        "under_air_attack": True, "under_ground_attack": False,
        "losing_army_fast": False, "mass_stall": False,
    }
    prompt = sp.build_prompt(_snap(ally=ally), CFG, [], ally_mode=True)
    assert "ALLY STATUS" in prompt
    assert "ALERT" in prompt   # air_threat > 15

def test_prompt_includes_player_chat(sp):
    snap = _snap(player_chat=["Строй танки", "Иди на север"])
    prompt = sp.build_prompt(snap, CFG, [])
    assert "Строй танки" in prompt

def test_has_cyrillic_detection(sp):
    assert sp._has_cyrillic("Привет мир")
    assert not sp._has_cyrillic("Hello world")
    assert not sp._has_cyrillic("")
    assert sp._has_cyrillic("Attack северная сторона")
