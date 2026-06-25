"""Voice I/O for the SupCom LLM AI bot (Phase 1).

Local STT (faster-whisper + Silero VAD) in, local TTS (Piper) out, gated by a
mouse side-button. A spoken utterance is turned into a synthetic snapshot by the
bridge; the bot's chat replies are spoken. Voice never crashes the bridge: any
failure degrades to "voice off".
"""

from __future__ import annotations

import logging
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
