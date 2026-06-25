"""Voice I/O for the SupCom LLM AI bot (Phase 1).

Local STT (faster-whisper + Silero VAD) in, local TTS (Piper) out, gated by a
mouse side-button. A spoken utterance is turned into a synthetic snapshot by the
bridge; the bot's chat replies are spoken. Voice never crashes the bridge: any
failure degrades to "voice off".
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections import deque
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


class SpeechSpeaker:
    """Serializes TTS playback; drops oldest pending text past max_pending."""

    def __init__(
        self,
        synth_fn: Callable[[str], bytes],
        play_fn: Callable[[bytes], None],
        max_pending: int = 2,
        on_speaking: Callable[[], None] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._synth = synth_fn
        self._play = play_fn
        self._max_pending = max_pending
        self._on_speaking = on_speaking
        self._on_done = on_done
        self._queue: deque[str] = deque()
        self._wake = asyncio.Event()
        self._abort = False

    def enqueue(self, text: str) -> None:
        """Queue text for TTS playback (drops oldest past max_pending).

        Must be called from the asyncio event-loop thread: the wake Event's
        set/clear is not safe across threads.
        """
        if not text:
            return
        self._queue.append(text)
        while len(self._queue) > self._max_pending:
            dropped = self._queue.popleft()
            log.info("TTS backlog full; dropped oldest: %r", dropped)
        self._wake.set()

    def pending(self) -> int:
        return len(self._queue)

    def peek_texts(self) -> list[str]:
        return list(self._queue)

    def stop_current(self) -> None:
        self._abort = True

    async def run(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            text = self._queue.popleft()
            self._abort = False
            if self._on_speaking:
                self._on_speaking()
            try:
                pcm = await asyncio.to_thread(self._synth, text)
                if not self._abort:
                    await asyncio.to_thread(self._play, pcm)
            except Exception as exc:  # never let TTS crash the loop
                log.warning("TTS synth/play failed: %s", exc)
            finally:
                if self._on_done:
                    self._on_done()


class SpeechListener:
    """Turns finished speech segments into gated, filtered Utterances."""

    def __init__(self, io: VoiceIO, transcribe_fn: Callable[[bytes], str]) -> None:
        self._io = io
        self._transcribe = transcribe_fn
        self.utterances: asyncio.Queue = asyncio.Queue(maxsize=16)

    async def on_segment(self, pcm: bytes, t0: float, t1: float) -> bool:
        if not self._io.accepting_speech():
            return False  # gate closed / echo-guard
        try:
            text = await asyncio.to_thread(self._transcribe, pcm)
        except Exception as exc:
            log.warning("STT failed: %s", exc)
            return False
        text = (text or "").strip()
        if not self._io.should_emit(text):
            return False
        # Re-check the gate after the (possibly slow) transcription.
        if not self._io.accepting_speech():
            return False
        try:
            self.utterances.put_nowait(Utterance(text=text, t0=t0, t1=t1))
        except asyncio.QueueFull:
            log.warning("Utterance queue full; dropping: %r", text)
            return False
        return True


class VoiceSession:
    """Facade the bridge talks to: utterances in, speak() out."""

    def __init__(
        self, io: VoiceIO, listener: SpeechListener, speaker: SpeechSpeaker
    ) -> None:
        self.io = io
        self.listener = listener
        self.speaker = speaker

    @property
    def utterances(self) -> asyncio.Queue:
        return self.listener.utterances

    def speak(self, text: str) -> None:
        self.speaker.enqueue(text)

    async def run(self) -> None:
        # Phase 1: the speaker worker; the real mic loop is added by _init_backends.
        await self.speaker.run()


def _init_backends(cfg: VoiceConfig):
    """Construct real STT/TTS/audio backends. Raises on any failure.

    Returns a tuple (transcribe_fn, synth_fn, play_fn, start_mic_fn). Implemented
    fully in Task 8 (real backends); Task 6 only needs this symbol to exist so the
    factory's failure path is testable.
    """
    raise RuntimeError("real backends not wired yet (Task 8)")


def build_voice_session(cfg: VoiceConfig) -> VoiceSession | None:
    """Build a VoiceSession or return None (voice-off) on any failure."""
    if not cfg.enabled:
        log.info("Voice disabled in config (voice.enabled=false)")
        return None
    try:
        transcribe_fn, synth_fn, play_fn, _start_mic = _init_backends(cfg)
    except Exception as exc:
        log.warning("Voice backends unavailable, running without voice: %s", exc)
        return None
    io = VoiceIO(cfg)
    speaker = SpeechSpeaker(
        synth_fn,
        play_fn,
        max_pending=cfg.tts_max_pending,
        on_speaking=io.begin_speaking,
        on_done=io.end_speaking,
    )
    io._stop_speaking_cb = speaker.stop_current
    listener = SpeechListener(io, transcribe_fn)
    return VoiceSession(io, listener, speaker)


def build_voice_snapshot(latest_snapshot: dict | None, text: str) -> dict:
    """Build a synthetic snapshot carrying a spoken utterance as player_chat.

    Clones the latest real snapshot (for economy/units/threats context) and
    stamps player_chat + trigger_event so the decision loop runs immediately.
    """
    if latest_snapshot:
        snap = copy.deepcopy(latest_snapshot)
    else:
        snap = {"tick": 0, "game_time_s": 0, "phase": "early", "mode": "ally"}
    snap["player_chat"] = [text]
    snap["trigger_event"] = "player_chat"
    return snap
