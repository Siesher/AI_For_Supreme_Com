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
        # Set by build_voice_session when real backends are wired; None = no hardware.
        self._start_mic: Callable | None = None

    @property
    def utterances(self) -> asyncio.Queue:
        return self.listener.utterances

    def speak(self, text: str) -> None:
        self.speaker.enqueue(text)

    async def run(self) -> None:
        speaker_task = asyncio.create_task(self.speaker.run())
        try:
            await asyncio.gather(speaker_task, self._mic_loop())
        except asyncio.CancelledError:
            speaker_task.cancel()
            try:
                await speaker_task
            except asyncio.CancelledError:
                pass
            raise
        except Exception as exc:
            log.warning("Voice runtime stopped: %s", exc)
            speaker_task.cancel()

    async def _mic_loop(self) -> None:
        """Capture mic audio, run Silero VAD, emit speech segments to the listener.

        No-op when _start_mic is None (unit-test / voice-off paths); real capture
        runs only after build_voice_session injected hardware refs via start_mic_fn.
        All heavy library imports happen in _init_backends; here we use refs that
        were stashed on self by start_mic_fn.
        """
        import time

        if self._start_mic is None:
            # Backends absent (tests / voice disabled); idle coroutine.
            return

        # Library references injected by start_mic_fn inside _init_backends.
        sd = self._sd  # sounddevice module
        np = self._np  # numpy module
        SR = self._SR  # sample rate (16000)
        mouse = self._mouse  # pynput.mouse module
        vad = self._vad_session  # onnxruntime InferenceSession (Silero VAD)

        VAD_FRAME = 512  # 32 ms at 16 kHz — Silero v4 native frame
        VAD_THRESHOLD = 0.5
        silence_timeout_s = self.io.cfg.vad_silence_timeout_ms / 1000.0

        # Silero VAD ONNX v4 LSTM state tensors; shape [2, 1, 64].
        # ASSUMPTION: ONNX input names are "input", "sr", "h", "c";
        #             outputs are "output" (prob [1,1]), "hn", "cn" ([2,1,64]).
        _state: dict = {
            "h": np.zeros((2, 1, 64), dtype=np.float32),
            "c": np.zeros((2, 1, 64), dtype=np.float32),
            "in_speech": False,
            "t0": 0.0,
            "last_speech_t": 0.0,
        }

        # Map config string to pynput Button; fall back to x1 on AttributeError.
        # ASSUMPTION: pynput.mouse.Button.x1 (M4) and .x2 (M5) exist on Windows
        #             with the user's mouse driver.
        _button_attr = self.io.cfg.gate_mouse_button  # "x1" | "x2" | "left" etc.
        target_button = getattr(mouse.Button, _button_attr, mouse.Button.x1)

        def _on_click(x, y, button, pressed) -> None:
            if button == target_button:
                if pressed:
                    self.io.on_button_press()
                else:
                    self.io.on_button_release()

        mouse_listener = mouse.Listener(on_click=_on_click)
        mouse_listener.start()

        # Audio buffer: list of raw int16 byte chunks assembled per utterance.
        _audio_buf: list[bytes] = []
        event_loop = asyncio.get_event_loop()

        def _audio_callback(indata: np.ndarray, frames: int, time_info, status) -> None:
            """sounddevice InputStream callback (runs in a background thread)."""
            if status:
                log.debug("sounddevice status: %s", status)

            pcm = indata[:, 0].copy()  # mono int16 samples, shape [frames]

            i = 0
            while i + VAD_FRAME <= len(pcm):
                chunk = pcm[i : i + VAD_FRAME]
                chunk_f32 = chunk.astype(np.float32) / 32768.0
                sr_arr = np.array(SR, dtype=np.int64)

                # Run one VAD frame; update stateful LSTM cells in _state dict.
                out = vad.run(
                    None,
                    {
                        "input": chunk_f32.reshape(1, -1),
                        "sr": sr_arr,
                        "h": _state["h"],
                        "c": _state["c"],
                    },
                )
                prob = float(out[0][0][0])
                _state["h"] = out[1]
                _state["c"] = out[2]

                now = time.monotonic()
                is_speech = prob >= VAD_THRESHOLD

                if is_speech:
                    _state["last_speech_t"] = now
                    if not _state["in_speech"]:
                        _state["in_speech"] = True
                        _state["t0"] = now
                        _audio_buf.clear()
                        log.debug("VAD: speech start (prob=%.2f)", prob)
                    _audio_buf.append(chunk.tobytes())
                elif _state["in_speech"]:
                    # Still buffering tail frames during silence window.
                    _audio_buf.append(chunk.tobytes())
                    if (now - _state["last_speech_t"]) >= silence_timeout_s:
                        # Silence timeout reached — segment complete.
                        _state["in_speech"] = False
                        assembled = b"".join(_audio_buf)
                        t0_snap = _state["t0"]
                        t1 = now
                        log.debug(
                            "VAD: speech end (%.1fs, %d bytes)",
                            t1 - t0_snap,
                            len(assembled),
                        )
                        asyncio.run_coroutine_threadsafe(
                            self.listener.on_segment(assembled, t0_snap, t1),
                            event_loop,
                        )
                        _audio_buf.clear()

                i += VAD_FRAME

        try:
            with sd.InputStream(
                samplerate=SR,
                channels=1,
                dtype="int16",
                blocksize=VAD_FRAME,
                device=self.io.cfg.input_device,
                callback=_audio_callback,
            ):
                log.info(
                    "Voice mic loop active (button=%s, mode=%s)",
                    self.io.cfg.gate_mouse_button,
                    self.io.cfg.gate_mode,
                )
                while True:
                    await asyncio.sleep(0.1)
        except Exception as exc:
            log.warning("Mic capture error: %s", exc)
            raise
        finally:
            mouse_listener.stop()
            log.info("Voice mic loop stopped")


def _init_backends(cfg: VoiceConfig):
    """Construct real STT/TTS/audio/VAD backends.  All heavy imports are local.

    Returns (transcribe_fn, synth_fn, play_fn, start_mic_fn).
    Raises ImportError / RuntimeError / OSError on any missing dep or model file;
    build_voice_session catches everything and degrades to voice-off.
    """
    import urllib.request
    from pathlib import Path as _Path

    import numpy as np
    import onnxruntime as ort
    import sounddevice as sd
    from faster_whisper import WhisperModel
    from piper import PiperVoice  # piper-tts package
    from pynput import mouse

    SR = 16000  # whisper + silero sample rate

    # --- Silero VAD ONNX model (download once to ~/.cache/silero-vad/) ---
    # ASSUMPTION: silero_vad.onnx is v4; ONNX inputs "input","sr","h","c";
    #             outputs "output" [1,1], "hn" [2,1,64], "cn" [2,1,64].
    _VAD_CACHE = _Path.home() / ".cache" / "silero-vad" / "silero_vad.onnx"
    _VAD_URL = (
        "https://github.com/snakers4/silero-vad/raw/master"
        "/src/silero_vad/data/silero_vad.onnx"
    )
    if not _VAD_CACHE.exists():
        log.info("Silero VAD model not found; downloading to %s ...", _VAD_CACHE)
        _VAD_CACHE.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(_VAD_URL, str(_VAD_CACHE))
    vad_session = ort.InferenceSession(str(_VAD_CACHE))

    # --- STT: faster-whisper ---
    model = WhisperModel(
        cfg.stt_model, device=cfg.stt_device, compute_type=cfg.stt_compute_type
    )

    # --- TTS: piper-tts ---
    # ASSUMPTION: cfg.tts_voice is a path or a name accepted by PiperVoice.load().
    #             Users must download the ONNX+JSON voice from
    #             https://github.com/rhasspy/piper/releases and set tts.voice
    #             to the .onnx file path (or the bare name if piper-tts resolves it).
    # ASSUMPTION: voice.config.sample_rate is a plain int attribute (e.g. 22050).
    voice = PiperVoice.load(cfg.tts_voice)

    def transcribe_fn(pcm: bytes) -> str:
        """Transcribe raw int16 PCM bytes to text via faster-whisper."""
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        # ASSUMPTION: model.transcribe() returns (segments_generator, TranscriptionInfo).
        segments, _ = model.transcribe(
            audio, language=cfg.stt_language, vad_filter=False
        )
        return " ".join(s.text for s in segments).strip()

    def synth_fn(text: str) -> bytes:
        """Synthesise text to raw int16 PCM via piper-tts."""
        # ASSUMPTION: PiperVoice.synthesize_stream_raw(text) returns Iterable[bytes].
        chunks = bytearray()
        for chunk in voice.synthesize_stream_raw(text):
            chunks.extend(chunk)
        return bytes(chunks)

    def play_fn(pcm: bytes) -> None:
        """Play raw int16 PCM through sounddevice (blocking until done)."""
        audio = np.frombuffer(pcm, dtype=np.int16)
        sd.play(audio, samplerate=voice.config.sample_rate, device=cfg.output_device)
        sd.wait()

    def start_mic_fn(session: VoiceSession) -> None:
        """Inject library references into the session for use in _mic_loop."""
        session._sd = sd
        session._np = np
        session._SR = SR
        session._mouse = mouse
        session._vad_session = vad_session

    return transcribe_fn, synth_fn, play_fn, start_mic_fn


def build_voice_session(cfg: VoiceConfig) -> VoiceSession | None:
    """Build a VoiceSession or return None (voice-off) on any failure."""
    if not cfg.enabled:
        log.info("Voice disabled in config (voice.enabled=false)")
        return None
    try:
        transcribe_fn, synth_fn, play_fn, start_mic = _init_backends(cfg)
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
    sess = VoiceSession(io, listener, speaker)
    sess._start_mic = start_mic  # sentinel: real backends present
    start_mic(sess)  # inject sd / np / SR / mouse / vad_session refs
    return sess


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


def _selftest() -> int:
    """Offline mic->STT->TTS smoke test.  Speak after the prompt.

    Run: .venv/Scripts/python.exe server/voice_io.py --selftest
    Expected: logs "Voice mic loop active", you speak Russian while holding/toggling
    the configured side button, it prints the transcript and speaks it back.
    """
    import time

    logging.basicConfig(level=logging.INFO)
    cfg = VoiceConfig(enabled=True)
    sess = build_voice_session(cfg)
    if sess is None:
        log.error(
            "Voice backends unavailable — install deps and check mic/voice model."
        )
        return 1

    print(
        f"Voice ready.  Press mouse button '{cfg.gate_mouse_button}' "
        f"({cfg.gate_mode} mode) and say something in Russian.  "
        "Listening for 10 seconds..."
    )

    async def _run() -> None:
        run_task = asyncio.create_task(sess.run())
        deadline = time.monotonic() + 10.0
        try:
            while time.monotonic() < deadline:
                try:
                    utt = await asyncio.wait_for(sess.utterances.get(), timeout=0.5)
                    print(f"Transcript: {utt.text!r}")
                    log.info("Speaking back: %r", utt.text)
                    sess.speak(utt.text)
                except asyncio.TimeoutError:
                    pass
        finally:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    print("Selftest complete.")
    return 0


if __name__ == "__main__":
    import sys as _sys

    if "--selftest" in _sys.argv:
        raise SystemExit(_selftest())
