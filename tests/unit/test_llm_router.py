# Unit tests: LLMRouter
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

import pytest
from llm_router import LLMRouter

DEEP_TAG = "qwen3.5:9b"
FAST_TAG = "qwen3.5:4b"

def _cfg(difficulty="normal"):
    return {
        "llm": {"model_deep": DEEP_TAG, "model_fast": FAST_TAG},
        "bot": {"difficulty": difficulty},
    }

def _snap(trigger="periodic", chat=None):
    return {
        "trigger_event": trigger,
        "player_chat": chat or [],
    }

# --- difficulty overrides ---

def test_easy_always_fast():
    router = LLMRouter(_cfg("easy"))
    for trigger in ("periodic", "army_lost", "phase_change", "player_chat"):
        assert router.route(_snap(trigger)) == FAST_TAG

def test_hard_always_deep():
    router = LLMRouter(_cfg("hard"))
    for trigger in ("periodic", "army_lost", "phase_change", "ally_air_threat"):
        assert router.route(_snap(trigger)) == DEEP_TAG

# --- normal routing ---

def test_ally_events_use_fast():
    router = LLMRouter(_cfg())
    for trigger in ("ally_under_attack", "ally_air_threat", "ally_economy_stall", "periodic"):
        assert router.route(_snap(trigger)) == FAST_TAG

def test_deep_events_use_deep():
    router = LLMRouter(_cfg())
    for trigger in ("army_lost", "phase_change", "deep_periodic"):
        assert router.route(_snap(trigger)) == DEEP_TAG

def test_simple_player_chat_uses_fast():
    router = LLMRouter(_cfg())
    snap = _snap("player_chat", chat=["ok", "yes"])
    assert router.route(snap) == FAST_TAG

def test_complex_player_chat_uses_deep():
    router = LLMRouter(_cfg())
    # Long message → complex
    snap = _snap("player_chat", chat=["Давай разработаем стратегию захвата севера карты"])
    assert router.route(snap) == DEEP_TAG

def test_strategy_keyword_triggers_deep():
    router = LLMRouter(_cfg())
    snap = _snap("player_chat", chat=["attack from the east"])
    assert router.route(snap) == DEEP_TAG

def test_russian_strategy_keyword_triggers_deep():
    router = LLMRouter(_cfg())
    snap = _snap("player_chat", chat=["атакуй с севера"])
    assert router.route(snap) == DEEP_TAG

def test_unknown_trigger_defaults_to_fast():
    router = LLMRouter(_cfg())
    assert router.route(_snap("unknown_event")) == FAST_TAG
