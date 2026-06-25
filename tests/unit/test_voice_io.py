# Unit tests: voice_io
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

from voice_io import (
    SpeechListener,
    SpeechSpeaker,
    Utterance,
    VoiceConfig,
    VoiceIO,
    VoiceState,
    load_voice_config,
)


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


def _listener(text, mode="toggle"):
    io = VoiceIO(VoiceConfig(gate_mode=mode))
    lis = SpeechListener(io, transcribe_fn=lambda pcm: text)
    return io, lis


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


def test_speaker_drops_oldest_over_max_pending():
    spk = SpeechSpeaker(synth_fn=lambda t: b"", play_fn=lambda p: None, max_pending=2)
    spk.enqueue("one")
    spk.enqueue("two")
    spk.enqueue("three")  # backlog exceeds 2 -> "one" dropped
    assert spk.pending() == 2
    assert spk.peek_texts() == ["two", "three"]


def test_speaker_run_synthesizes_and_plays_in_order():
    played = []
    spk = SpeechSpeaker(
        synth_fn=lambda t: t.encode("utf-8"),
        play_fn=lambda pcm: played.append(pcm.decode("utf-8")),
        max_pending=5,
    )
    spk.enqueue("alpha")
    spk.enqueue("beta")

    async def drive():
        task = asyncio.create_task(spk.run())
        # let the worker drain both items
        for _ in range(50):
            if len(played) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert played == ["alpha", "beta"]


def test_speaker_brackets_callbacks():
    events = []
    spk = SpeechSpeaker(
        synth_fn=lambda t: b"x",
        play_fn=lambda pcm: events.append("play"),
        on_speaking=lambda: events.append("begin"),
        on_done=lambda: events.append("end"),
    )
    spk.enqueue("hi")

    async def drive():
        task = asyncio.create_task(spk.run())
        for _ in range(50):
            if "end" in events:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert events == ["begin", "play", "end"]


def test_speaker_stop_current_skips_play():
    played = []
    done = []
    spk = SpeechSpeaker(
        synth_fn=lambda t: b"x",
        play_fn=lambda p: played.append(p),
        on_done=lambda: done.append(1),
    )

    def synth_then_abort(text):
        spk.stop_current()  # abort before play
        return b"x"

    spk._synth = synth_then_abort
    spk.enqueue("hi")

    async def drive():
        task = asyncio.create_task(spk.run())
        for _ in range(50):
            if done:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert played == []  # play skipped due to abort
    assert done == [1]  # on_done still fired


def test_listener_emits_when_listening():
    io, lis = _listener("атакуй сюда")
    io.on_button_press()  # LISTENING
    emitted = asyncio.run(lis.on_segment(b"pcm", 1.0, 2.0))
    assert emitted is True
    utt = lis.utterances.get_nowait()
    assert isinstance(utt, Utterance)
    assert utt.text == "атакуй сюда"
    assert utt.t0 == 1.0 and utt.t1 == 2.0


def test_listener_drops_when_not_listening():
    io, lis = _listener("привет")  # gate never on -> IDLE
    emitted = asyncio.run(lis.on_segment(b"pcm", 0.0, 1.0))
    assert emitted is False
    assert lis.utterances.empty()


def test_listener_drops_short_text():
    io, lis = _listener("a")  # below default min_chars=2
    io.on_button_press()
    emitted = asyncio.run(lis.on_segment(b"pcm", 0.0, 1.0))
    assert emitted is False
    assert lis.utterances.empty()
