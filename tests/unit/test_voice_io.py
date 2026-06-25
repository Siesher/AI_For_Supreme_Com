# Unit tests: voice_io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

from voice_io import VoiceConfig, VoiceIO, VoiceState, load_voice_config


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




def _io(mode="toggle", **kw):

    return VoiceIO(VoiceConfig(gate_mode=mode, stt_min_chars=kw.get("min_chars", 2)))


def test_toggle_gate_cycles_listening():
    io = _io("toggle")
    assert io.state == VoiceState.IDLE
    io.on_button_press()  # toggle ON
    assert io.state == VoiceState.LISTENING
    assert io.accepting_speech() is True
    io.on_button_release()  # release ignored in toggle
    assert io.state == VoiceState.LISTENING
    io.on_button_press()  # toggle OFF
    assert io.state == VoiceState.IDLE
    assert io.accepting_speech() is False


def test_push_gate_holds():
    io = _io("push")
    io.on_button_press()
    assert io.state == VoiceState.LISTENING
    io.on_button_release()
    assert io.state == VoiceState.IDLE


def test_speaking_suppresses_listening():
    io = _io("toggle")
    io.on_button_press()  # LISTENING
    io.begin_speaking()
    assert io.state == VoiceState.SPEAKING
    assert io.accepting_speech() is False  # echo-guard
    io.end_speaking()
    assert io.state == VoiceState.LISTENING  # gate still on -> back to listening


def test_end_speaking_returns_idle_when_gate_off():
    io = _io("toggle")
    io.begin_speaking()  # gate never turned on
    io.end_speaking()
    assert io.state == VoiceState.IDLE


def test_barge_in_stops_speaking_and_listens():
    stopped = []

    io = VoiceIO(
        VoiceConfig(gate_mode="toggle"), stop_speaking_cb=lambda: stopped.append(1)
    )
    io.begin_speaking()
    io.on_button_press()  # barge-in
    assert stopped == [1]
    assert io.state == VoiceState.LISTENING
    assert io.gate_on is True


def test_should_emit_filters_short_text():
    io = _io("toggle", min_chars=2)
    assert io.should_emit("ок") is True
    assert io.should_emit(" a ") is False
    assert io.should_emit("") is False
