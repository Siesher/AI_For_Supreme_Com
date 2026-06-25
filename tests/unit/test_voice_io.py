# Unit tests: voice_io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

from voice_io import load_voice_config


def test_voice_config_defaults_when_missing():
    cfg = load_voice_config({})
    assert cfg.enabled is True
    assert cfg.stt_model == "small"
    assert cfg.stt_language == "ru"
    assert cfg.tts_voice == "ru_RU-irina-medium"
    assert cfg.tts_max_pending == 2
    assert cfg.gate_mode == "toggle"
    assert cfg.gate_mouse_button == "x1"


def test_voice_config_reads_nested_block():
    raw = {
        "voice": {
            "enabled": False,
            "stt": {"model": "medium", "language": "ru", "min_chars": 3},
            "tts": {"voice": "ru_RU-dmitri-medium", "max_pending": 1},
            "vad": {"silence_timeout_ms": 600},
            "gate": {"mode": "push", "mouse_button": "x2"},
        }
    }
    cfg = load_voice_config(raw)
    assert cfg.enabled is False
    assert cfg.stt_model == "medium"
    assert cfg.stt_min_chars == 3
    assert cfg.tts_voice == "ru_RU-dmitri-medium"
    assert cfg.tts_max_pending == 1
    assert cfg.vad_silence_timeout_ms == 600
    assert cfg.gate_mode == "push"
    assert cfg.gate_mouse_button == "x2"
