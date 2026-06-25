"""Voice I/O for the SupCom LLM AI bot (Phase 1).

Local STT (faster-whisper + Silero VAD) in, local TTS (Piper) out, gated by a
mouse side-button. A spoken utterance is turned into a synthetic snapshot by the
bridge; the bot's chat replies are spoken. Voice never crashes the bridge: any
failure degrades to "voice off".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class VoiceConfig:
    enabled: bool = True
    stt_model: str = "small"
    stt_language: str = "ru"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_min_speech_ms: int = 400
    stt_min_chars: int = 2
    tts_engine: str = "piper"
    tts_voice: str = "ru_RU-irina-medium"
    tts_max_pending: int = 2
    vad_silence_timeout_ms: int = 800
    gate_mode: str = "toggle"  # "toggle" | "push"
    gate_mouse_button: str = "x1"  # x1=M4, x2=M5
    echo_guard: bool = True
    input_device: object | None = None
    output_device: object | None = None
    debug_dump_audio: bool = False


def load_voice_config(config: dict) -> VoiceConfig:
    """Flatten the nested config["voice"] block into a VoiceConfig."""
    v = config.get("voice", {}) or {}
    stt = v.get("stt", {}) or {}
    tts = v.get("tts", {}) or {}
    vad = v.get("vad", {}) or {}
    gate = v.get("gate", {}) or {}
    return VoiceConfig(
        enabled=bool(v.get("enabled", True)),
        stt_model=stt.get("model", "small"),
        stt_language=stt.get("language", "ru"),
        stt_device=stt.get("device", "cpu"),
        stt_compute_type=stt.get("compute_type", "int8"),
        stt_min_speech_ms=int(stt.get("min_speech_ms", 400)),
        stt_min_chars=int(stt.get("min_chars", 2)),
        tts_engine=tts.get("engine", "piper"),
        tts_voice=tts.get("voice", "ru_RU-irina-medium"),
        tts_max_pending=int(tts.get("max_pending", 2)),
        vad_silence_timeout_ms=int(vad.get("silence_timeout_ms", 800)),
        gate_mode=gate.get("mode", "toggle"),
        gate_mouse_button=gate.get("mouse_button", "x1"),
        echo_guard=bool(v.get("echo_guard", True)),
        input_device=v.get("input_device"),
        output_device=v.get("output_device"),
        debug_dump_audio=bool(v.get("debug_dump_audio", False)),
    )


class VoiceState:
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


@dataclass
class Utterance:
    text: str
    t0: float
    t1: float


class VoiceIO:
    """Pure state machine for gate + echo-guard + barge-in.

    Audio adapters call into this; tests drive it directly with no hardware.
    """

    def __init__(
        self, cfg: VoiceConfig, stop_speaking_cb: Callable[[], None] | None = None
    ) -> None:
        self.cfg = cfg
        self.state = VoiceState.IDLE
        self.gate_on = False
        self._stop_speaking_cb = stop_speaking_cb

    def on_button_press(self) -> None:
        if self.state == VoiceState.SPEAKING:
            # Barge-in: abort TTS, start listening.
            if self._stop_speaking_cb:
                self._stop_speaking_cb()
            self.gate_on = True
            self.state = VoiceState.LISTENING
            return
        if self.cfg.gate_mode == "toggle":
            self.gate_on = not self.gate_on
        else:  # push
            self.gate_on = True
        self.state = VoiceState.LISTENING if self.gate_on else VoiceState.IDLE

    def on_button_release(self) -> None:
        if self.cfg.gate_mode != "push":
            return  # toggle ignores release
        self.gate_on = False
        if self.state == VoiceState.LISTENING:
            self.state = VoiceState.IDLE

    def begin_speaking(self) -> None:
        self.state = VoiceState.SPEAKING

    def end_speaking(self) -> None:
        self.state = VoiceState.LISTENING if self.gate_on else VoiceState.IDLE

    def accepting_speech(self) -> bool:
        return self.state == VoiceState.LISTENING

    def should_emit(self, text: str) -> bool:
        return len(text.strip()) >= self.cfg.stt_min_chars
