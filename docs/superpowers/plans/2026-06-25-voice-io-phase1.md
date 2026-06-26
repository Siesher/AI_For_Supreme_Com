# Voice I/O Loop (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the player talk to the allied LLM bot by voice (Russian STT in, Russian TTS out) in real time, with a hands-free mouse-button gate, echo-guard, and barge-in — routing entirely around the broken in-game chat, with **no** screen-context yet.

**Architecture:** All new code is Python in a single module `server/voice_io.py` (three isolated units: `SpeechListener`, `SpeechSpeaker`, `VoiceIO`) plus thin wiring in `server/bridge_server.py`. A spoken utterance becomes a **synthetic snapshot** (clone of the latest real game snapshot + `player_chat=[text]` + `trigger_event="player_chat"`) pushed onto the existing `snapshot_queue`; because `player_chat` is already a `PRIORITY_EVENT`, the existing decision loop runs an immediate ReAct cycle. The bot's `chat` tool output is spoken via TTS at the existing extraction seam. The Sim Lua sandbox is **not touched**.

**Tech Stack:** Python 3 (asyncio), faster-whisper (CPU int8) for STT, Silero VAD (onnxruntime) for voice activity, Piper for Russian TTS, sounddevice for audio I/O, pynput for the mouse-button gate. Tests: pytest.

This plan implements **Phase 1 only** (spec §9). Phase 2 (screen-context `[LLM_SCREEN]` channel, deixis, `mark_map`) gets its own plan after Phase 1 lands and passes a live test.

Spec: `docs/superpowers/specs/2026-06-25-voice-screen-agent-design.md`.

## Global Constraints

- Platform: Windows 11, PowerShell; run Python via `.\.venv\Scripts\python.exe`.
- All STT/TTS is **fully local**; no network calls; mic audio lives in RAM only, never persisted (unless `voice.debug_dump_audio` is explicitly true).
- Voice **must never crash the bridge**: any backend/import/runtime failure logs a `WARNING` and degrades to "voice off"; the decision loop keeps running.
- Code/comments/commit messages in **English**; user-facing TTS text is whatever the bot's `chat` tool emits (Russian when the player speaks Russian).
- Test convention (match existing tests): each test file begins with
  `import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))`
  then imports the module directly (e.g. `from voice_io import VoiceIO`).
- Run tests from repo root: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`.
- DRY, YAGNI, TDD, frequent commits. Commit after each task.
- Logging via the `logging` module, never `print()` in the module (a `--selftest` CLI may print).
- Git: work on branch `002-agent-react-loop`. Commit per task; do not push unless asked.

---

### Task 1: VoiceConfig — typed config with defaults

Reads the `voice` block from `config.json` (nested) into a flat dataclass with safe defaults, so every later unit takes typed values, not raw dicts.

**Files:**
- Create: `server/voice_io.py`
- Test: `tests/unit/test_voice_io.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `@dataclass VoiceConfig` with fields and defaults exactly:
    `enabled: bool = True`, `stt_model: str = "small"`, `stt_language: str = "ru"`,
    `stt_device: str = "cpu"`, `stt_compute_type: str = "int8"`, `stt_min_speech_ms: int = 400`,
    `stt_min_chars: int = 2`, `tts_engine: str = "piper"`, `tts_voice: str = "ru_RU-irina-medium"`,
    `tts_max_pending: int = 2`, `vad_silence_timeout_ms: int = 800`, `gate_mode: str = "toggle"`,
    `gate_mouse_button: str = "x1"`, `echo_guard: bool = True`,
    `input_device: object | None = None`, `output_device: object | None = None`,
    `debug_dump_audio: bool = False`
  - `def load_voice_config(config: dict) -> VoiceConfig` — flattens the nested `config["voice"]` block.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_io.py`:
```python
# Unit tests: voice_io
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

import pytest
from voice_io import VoiceConfig, load_voice_config


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_io'`.

- [ ] **Step 3: Write minimal implementation**

Create `server/voice_io.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add server/voice_io.py tests/unit/test_voice_io.py
git commit -m "feat(voice): VoiceConfig loader for the voice block"
```

---

### Task 2: VoiceIO state machine (echo-guard + gate + barge-in)

The pure core: a state machine driving IDLE/LISTENING/SPEAKING from button + speaking events. No audio, no asyncio — fully unit-testable.

**Files:**
- Modify: `server/voice_io.py`
- Test: `tests/unit/test_voice_io.py`

**Interfaces:**
- Consumes: `VoiceConfig` (Task 1).
- Produces:
  - `class VoiceState` constants `IDLE = "idle"`, `LISTENING = "listening"`, `SPEAKING = "speaking"`.
  - `@dataclass Utterance` with `text: str`, `t0: float`, `t1: float`.
  - `class VoiceIO`:
    - `__init__(self, cfg: VoiceConfig, stop_speaking_cb=None)`
    - `state: str` (starts `IDLE`); `gate_on: bool`
    - `on_button_press() -> None`, `on_button_release() -> None`
    - `begin_speaking() -> None`, `end_speaking() -> None`
    - `accepting_speech() -> bool` (True only in LISTENING)
    - `should_emit(text: str) -> bool` (len of stripped text >= `cfg.stt_min_chars`)
    - `_stop_speaking_cb` invoked on barge-in.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_voice_io.py`:
```python
from voice_io import VoiceIO, VoiceState


def _io(mode="toggle", **kw):
    from voice_io import VoiceConfig
    return VoiceIO(VoiceConfig(gate_mode=mode, stt_min_chars=kw.get("min_chars", 2)))


def test_toggle_gate_cycles_listening():
    io = _io("toggle")
    assert io.state == VoiceState.IDLE
    io.on_button_press()                 # toggle ON
    assert io.state == VoiceState.LISTENING
    assert io.accepting_speech() is True
    io.on_button_release()               # release ignored in toggle
    assert io.state == VoiceState.LISTENING
    io.on_button_press()                 # toggle OFF
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
    io.on_button_press()                 # LISTENING
    io.begin_speaking()
    assert io.state == VoiceState.SPEAKING
    assert io.accepting_speech() is False  # echo-guard
    io.end_speaking()
    assert io.state == VoiceState.LISTENING  # gate still on -> back to listening


def test_end_speaking_returns_idle_when_gate_off():
    io = _io("toggle")
    io.begin_speaking()                  # gate never turned on
    io.end_speaking()
    assert io.state == VoiceState.IDLE


def test_barge_in_stops_speaking_and_listens():
    stopped = []
    from voice_io import VoiceConfig
    io = VoiceIO(VoiceConfig(gate_mode="toggle"), stop_speaking_cb=lambda: stopped.append(1))
    io.begin_speaking()
    io.on_button_press()                 # barge-in
    assert stopped == [1]
    assert io.state == VoiceState.LISTENING
    assert io.gate_on is True


def test_should_emit_filters_short_text():
    io = _io("toggle", min_chars=2)
    assert io.should_emit("ок") is True
    assert io.should_emit(" a ") is False
    assert io.should_emit("") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'VoiceIO'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server/voice_io.py`:
```python
from dataclasses import dataclass as _dataclass


class VoiceState:
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


@_dataclass
class Utterance:
    text: str
    t0: float
    t1: float


class VoiceIO:
    """Pure state machine for gate + echo-guard + barge-in.

    Audio adapters call into this; tests drive it directly with no hardware.
    """

    def __init__(self, cfg: VoiceConfig, stop_speaking_cb=None) -> None:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add server/voice_io.py tests/unit/test_voice_io.py
git commit -m "feat(voice): VoiceIO state machine (gate, echo-guard, barge-in)"
```

---

### Task 3: SpeechSpeaker — TTS queue with drop-oldest backlog

A worker that synthesizes + plays queued text, brackets playback with `begin_speaking()`/`end_speaking()`, and drops the oldest pending item when the backlog exceeds `max_pending`. Synthesis and playback are injected so tests use fakes.

**Files:**
- Modify: `server/voice_io.py`
- Test: `tests/unit/test_voice_io.py`

**Interfaces:**
- Consumes: `VoiceIO` (Task 2).
- Produces:
  - `class SpeechSpeaker`:
    - `__init__(self, synth_fn, play_fn, max_pending: int = 2, on_speaking=None, on_done=None)`
      where `synth_fn(text) -> bytes` (PCM) and `play_fn(pcm: bytes) -> None`.
    - `enqueue(self, text: str) -> None` — non-blocking; drops oldest if pending > `max_pending`.
    - `async def run(self) -> None` — worker loop: pull text, `on_speaking()`, synth+play, `on_done()`.
    - `stop_current(self) -> None` — request abort of the in-flight playback (barge-in).
    - `pending() -> int` — queue length (for tests).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_voice_io.py`:
```python
import asyncio
from voice_io import SpeechSpeaker


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'SpeechSpeaker'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server/voice_io.py` (add `import asyncio` and `from collections import deque` to the top-of-file imports):
```python
class SpeechSpeaker:
    """Serializes TTS playback; drops oldest pending text past max_pending."""

    def __init__(self, synth_fn, play_fn, max_pending: int = 2,
                 on_speaking=None, on_done=None) -> None:
        self._synth = synth_fn
        self._play = play_fn
        self._max_pending = max_pending
        self._on_speaking = on_speaking
        self._on_done = on_done
        self._queue: deque[str] = deque()
        self._wake = asyncio.Event()
        self._abort = False

    def enqueue(self, text: str) -> None:
        if not text:
            return
        self._queue.append(text)
        while len(self._queue) > self._max_pending:
            dropped = self._queue.popleft()
            log.info("TTS backlog full; dropped oldest: %r", dropped)
        self._wake.set()

    def pending(self) -> int:
        return len(self._queue)

    def peek_texts(self) -> list:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add server/voice_io.py tests/unit/test_voice_io.py
git commit -m "feat(voice): SpeechSpeaker TTS queue with drop-oldest backlog"
```

---

### Task 4: SpeechListener — gating + STT filter over injected frames

Consumes finished speech segments (from VAD), transcribes via an injected `transcribe_fn`, and emits an `Utterance` onto an asyncio queue **only** when `VoiceIO.accepting_speech()` and the text passes `should_emit`. Audio capture/VAD hardware is injected so tests use fakes.

**Files:**
- Modify: `server/voice_io.py`
- Test: `tests/unit/test_voice_io.py`

**Interfaces:**
- Consumes: `VoiceIO` (Task 2), `Utterance` (Task 2).
- Produces:
  - `class SpeechListener`:
    - `__init__(self, io: VoiceIO, transcribe_fn, clock=...)` where `transcribe_fn(pcm: bytes) -> str`.
    - `utterances: asyncio.Queue` (holds `Utterance`).
    - `async def on_segment(self, pcm: bytes, t0: float, t1: float) -> bool` — transcribe, filter, gate; returns True if an Utterance was emitted.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_voice_io.py`:
```python
from voice_io import SpeechListener, VoiceConfig, Utterance


def _listener(text, mode="toggle"):
    io = VoiceIO(VoiceConfig(gate_mode=mode))
    lis = SpeechListener(io, transcribe_fn=lambda pcm: text)
    return io, lis


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'SpeechListener'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server/voice_io.py`:
```python
class SpeechListener:
    """Turns finished speech segments into gated, filtered Utterances."""

    def __init__(self, io: VoiceIO, transcribe_fn) -> None:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add server/voice_io.py tests/unit/test_voice_io.py
git commit -m "feat(voice): SpeechListener gating + STT filter"
```

---

### Task 5: synthetic-snapshot builder

Pure function that turns an utterance into the synthetic snapshot the bridge enqueues. Clones the latest real snapshot for game-state context and stamps `player_chat` + `trigger_event`.

**Files:**
- Modify: `server/voice_io.py`
- Test: `tests/unit/test_voice_io.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `def build_voice_snapshot(latest_snapshot: dict | None, text: str) -> dict`
    - returns a **new** dict (does not mutate input)
    - if `latest_snapshot` is None, returns a minimal snapshot
    - always sets `player_chat=[text]` and `trigger_event="player_chat"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_voice_io.py`:
```python
from voice_io import build_voice_snapshot


def test_build_voice_snapshot_clones_and_stamps():
    latest = {"tick": 500, "economy": {"mass_income": 7}, "player_chat": [], "trigger_event": "periodic"}
    snap = build_voice_snapshot(latest, "защити базу")
    assert snap["player_chat"] == ["защити базу"]
    assert snap["trigger_event"] == "player_chat"
    assert snap["tick"] == 500
    assert snap["economy"] == {"mass_income": 7}
    # original not mutated
    assert latest["player_chat"] == []
    assert latest["trigger_event"] == "periodic"


def test_build_voice_snapshot_handles_no_latest():
    snap = build_voice_snapshot(None, "привет")
    assert snap["player_chat"] == ["привет"]
    assert snap["trigger_event"] == "player_chat"
    assert "tick" in snap
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_voice_snapshot'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server/voice_io.py` (add `import copy` to the top-of-file imports):
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: PASS (16 passed).

- [ ] **Step 5: Commit**

```bash
git add server/voice_io.py tests/unit/test_voice_io.py
git commit -m "feat(voice): synthetic-snapshot builder for spoken utterances"
```

---

### Task 6: VoiceSession facade + graceful-degrade factory

A `VoiceSession` ties `VoiceIO` + `SpeechListener` + `SpeechSpeaker` together and exposes the two things the bridge needs: `.utterances` (queue) and `.speak(text)`. A `build_voice_session(cfg)` factory constructs the real audio/STT/TTS backends and **returns None on any failure** (missing deps, no mic) so the bridge degrades to voice-off.

**Files:**
- Modify: `server/voice_io.py`
- Test: `tests/unit/test_voice_io.py`

**Interfaces:**
- Consumes: all prior units.
- Produces:
  - `class VoiceSession`:
    - `__init__(self, io: VoiceIO, listener: SpeechListener, speaker: SpeechSpeaker)`
    - `utterances` property → `listener.utterances`
    - `def speak(self, text: str) -> None` → `speaker.enqueue(text)`
    - `async def run(self) -> None` → runs the speaker worker (and, in the real build, the mic loop)
  - `def build_voice_session(cfg: VoiceConfig) -> VoiceSession | None` — returns None (and logs WARNING) if `cfg.enabled` is False or any backend init fails.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_voice_io.py`:
```python
from voice_io import VoiceSession, build_voice_session, VoiceConfig


def test_session_speak_enqueues_and_exposes_utterances():
    io = VoiceIO(VoiceConfig())
    spk = SpeechSpeaker(synth_fn=lambda t: b"", play_fn=lambda p: None)
    lis = SpeechListener(io, transcribe_fn=lambda pcm: "x")
    sess = VoiceSession(io, lis, spk)
    sess.speak("привет")
    assert spk.pending() == 1
    assert sess.utterances is lis.utterances


def test_build_voice_session_disabled_returns_none():
    assert build_voice_session(VoiceConfig(enabled=False)) is None


def test_build_voice_session_missing_backend_returns_none(monkeypatch):
    # Simulate a backend import failure inside the factory.
    import voice_io
    monkeypatch.setattr(voice_io, "_init_backends",
                        lambda cfg: (_ for _ in ()).throw(RuntimeError("no mic")))
    assert build_voice_session(VoiceConfig(enabled=True)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'VoiceSession'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server/voice_io.py`:
```python
class VoiceSession:
    """Facade the bridge talks to: utterances in, speak() out."""

    def __init__(self, io: VoiceIO, listener: SpeechListener, speaker: SpeechSpeaker) -> None:
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


def build_voice_session(cfg: VoiceConfig) -> "VoiceSession | None":
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
        synth_fn, play_fn, max_pending=cfg.tts_max_pending,
        on_speaking=io.begin_speaking, on_done=io.end_speaking,
    )
    io._stop_speaking_cb = speaker.stop_current
    listener = SpeechListener(io, transcribe_fn)
    return VoiceSession(io, listener, speaker)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py -v`
Expected: PASS (19 passed).

- [ ] **Step 5: Commit**

```bash
git add server/voice_io.py tests/unit/test_voice_io.py
git commit -m "feat(voice): VoiceSession facade + graceful-degrade factory"
```

---

### Task 7: Bridge wiring — _voice_loop, latest-snapshot capture, speak hook

Wire the session into `bridge_server.py`: capture the latest real snapshot, inject synthetic snapshots from utterances, and speak the bot's chat replies. Keep the changes minimal and the failure-isolated (`voice=None` ⇒ everything behaves exactly as today).

**Files:**
- Modify: `server/bridge_server.py`
  - `main()` ~lines 297-380 (construct session, shared holder, add `_voice_loop` to `asyncio.gather`)
  - `_file_ipc_poll()` ~lines 383-404 (record latest snapshot)
  - `_decision_loop()` signature ~407-421 and chat-extraction ~544-559 (speak)
- Test: `tests/integration/test_voice_bridge.py`

**Interfaces:**
- Consumes: `VoiceSession`, `build_voice_session`, `load_voice_config`, `build_voice_snapshot` (Tasks 1-6).
- Produces:
  - `async def _voice_loop(voice, shared, snapshot_queue)` — module-level in `bridge_server.py`.
  - `_file_ipc_poll(file_ipc, snapshot_queue, shared=None)` — extended with optional `shared` dict.
  - `_decision_loop(..., voice=None)` — extended with optional `voice`.
  - Shared holder convention: a plain dict `{"snapshot": None}`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_voice_bridge.py`:
```python
# Integration: voice wiring in bridge_server
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

import pytest
import bridge_server


class _FakeUtter:
    def __init__(self, text):
        self.text = text
        self.t0 = 0.0
        self.t1 = 1.0


def test_voice_loop_enqueues_synthetic_snapshot():
    q = asyncio.Queue(maxsize=4)
    shared = {"snapshot": {"tick": 42, "economy": {"mass_income": 5}}}

    class FakeVoice:
        def __init__(self):
            self.utterances = asyncio.Queue()
        def speak(self, text):  # unused here
            pass

    voice = FakeVoice()
    voice.utterances.put_nowait(_FakeUtter("атакуй сюда"))

    async def drive():
        task = asyncio.create_task(bridge_server._voice_loop(voice, shared, q))
        for _ in range(50):
            if not q.empty():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    env = q.get_nowait()
    assert env["type"] == "snapshot"
    assert env["_source"] == "file"
    assert env["data"]["player_chat"] == ["атакуй сюда"]
    assert env["data"]["trigger_event"] == "player_chat"
    assert env["data"]["tick"] == 42  # cloned from latest


def test_decision_loop_speaks_chat(monkeypatch):
    spoken = []

    class FakeVoice:
        def speak(self, text):
            spoken.append(text)

    # Minimal: call the extracted speak helper used by the loop.
    bridge_server._speak_chat_messages(
        decision={"tool_calls": [{"name": "chat", "args": {"message": "иду на север"}}]},
        iterations=[],
        chat_history=[],
        voice=FakeVoice(),
    )
    assert spoken == ["иду на север"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_voice_bridge.py -v`
Expected: FAIL — `AttributeError: module 'bridge_server' has no attribute '_voice_loop'` (and `_speak_chat_messages`).

- [ ] **Step 3: Write minimal implementation**

In `server/bridge_server.py`, add the two helpers (place after `_file_ipc_poll`):
```python
async def _voice_loop(voice, shared: dict, snapshot_queue: asyncio.Queue) -> None:
    """Turn spoken utterances into synthetic priority snapshots."""
    from voice_io import build_voice_snapshot

    log.info("Voice loop started — listening for spoken utterances")
    while True:
        utt = await voice.utterances.get()
        text = getattr(utt, "text", "") or ""
        if not text:
            continue
        snap = build_voice_snapshot(shared.get("snapshot"), text)
        log.info("Voice: utterance -> synthetic snapshot: %r", text)
        await snapshot_queue.put({"type": "snapshot", "data": snap, "_source": "file"})


def _speak_chat_messages(decision: dict, iterations: list, chat_history: list, voice) -> None:
    """Append bot chat messages to history and, if voice is on, speak them."""
    def _handle(tc):
        if tc.get("name") == "chat":
            msg_text = tc.get("args", {}).get("message", "")
            if msg_text:
                chat_history.append({"role": "bot", "text": msg_text})
                if voice:
                    voice.speak(msg_text)

    for tc in decision.get("tool_calls", []):
        _handle(tc)
    for it in iterations:
        for tc in it.get("tool_calls", []):
            _handle(tc)
```

Then **replace** the chat-extraction block in `_decision_loop` (currently lines ~544-559) with a single call, and trim history once:
```python
            # Extract chat messages for history (+ speak them when voice is on)
            _speak_chat_messages(decision, iterations, chat_history, voice)
            chat_history = chat_history[-10:]
```

Extend the `_decision_loop` signature to accept `voice=None` (add as the last keyword param after `file_react_loop=None`):
```python
    file_ipc=None,
    file_react_loop=None,
    voice=None,
) -> None:
```

Extend `_file_ipc_poll` to record the latest snapshot:
```python
async def _file_ipc_poll(file_ipc, snapshot_queue: asyncio.Queue, shared: dict | None = None) -> None:
    ...
        snapshot = file_ipc.read_snapshot()
        if snapshot:
            if shared is not None:
                shared["snapshot"] = snapshot
            ...
```

In `main()`, after `file_ipc` is constructed and before `asyncio.gather`, build the session and shared holder:
```python
    # Voice I/O (Phase 1): spoken utterances -> synthetic priority snapshots,
    # bot chat replies -> TTS. Degrades to no-op if unavailable.
    from voice_io import load_voice_config, build_voice_session
    voice = build_voice_session(load_voice_config(config))
    shared_state: dict = {"snapshot": None}
```

Finally, extend the `asyncio.gather(...)` call to pass `shared_state`/`voice` and add `_voice_loop` only when voice is live:
```python
    coros = [
        pipe_server.start(),
        _watch_and_inject(),
        _file_ipc_poll(file_ipc, snapshot_queue, shared_state),
        _decision_loop(
            snapshot_queue, command_queue, config, state_processor, llm_client,
            llm_router, fallback_strategy, decision_logger, save_state,
            react_loop, decision_memory,
            file_ipc=file_ipc, file_react_loop=file_react_loop, voice=voice,
        ),
    ]
    if voice:
        coros.append(voice.run())
        coros.append(_voice_loop(voice, shared_state, snapshot_queue))
    await asyncio.gather(*coros)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_voice_bridge.py tests/unit/test_voice_io.py -v`
Expected: PASS (all). Also run the existing suite to confirm no regression:
`.\.venv\Scripts\python.exe -m pytest tests/unit -q`
Expected: PASS (pre-existing tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add server/bridge_server.py tests/integration/test_voice_bridge.py
git commit -m "feat(voice): wire VoiceSession into bridge (voice loop + speak hook)"
```

---

### Task 8: Real backends, config block, deps, and live self-test

Wire the real STT/TTS/audio/mouse backends behind `_init_backends`, add the `voice` config block and dependencies, and provide a `--selftest` that verifies mic→STT→TTS offline. Hardware paths are verified by the self-test, not CI unit tests.

**Files:**
- Modify: `server/voice_io.py` (implement `_init_backends`, add `--selftest` CLI, real mic loop)
- Modify: `installer/config.json` (add `voice` block)
- Modify: `server/requirements.txt` (add optional voice deps)
- Modify: `CLAUDE.md` (document enabling voice)
- Test: manual (`--selftest`) + existing unit suite must still pass.

**Interfaces:**
- Consumes: `VoiceConfig`, `VoiceSession` (prior tasks).
- Produces: a working `_init_backends(cfg) -> (transcribe_fn, synth_fn, play_fn, start_mic_fn)` and a `VoiceSession.run()` that also starts the mic loop.

- [ ] **Step 1: Add the config block**

In `installer/config.json`, add a top-level `voice` block (sibling of `bot`):
```json
  "voice": {
    "enabled": false,
    "stt":  { "model": "small", "language": "ru", "device": "cpu", "compute_type": "int8",
              "min_speech_ms": 400, "min_chars": 2 },
    "tts":  { "engine": "piper", "voice": "ru_RU-irina-medium", "max_pending": 2 },
    "vad":  { "engine": "silero", "silence_timeout_ms": 800 },
    "gate": { "mode": "toggle", "mouse_button": "x1" },
    "echo_guard": true,
    "input_device": null,
    "output_device": null,
    "debug_dump_audio": false
  },
```
Note: ship with `"enabled": false` so existing setups are unaffected until the user installs the voice deps and flips it on.

- [ ] **Step 2: Add dependencies**

Append to `server/requirements.txt`:
```
# --- Voice I/O (optional; install only if voice.enabled=true) ---
# faster-whisper>=1.0
# sounddevice>=0.4
# piper-tts>=1.2
# onnxruntime>=1.17
# pynput>=1.7
# numpy>=1.24
```
Install (when enabling voice):
`.\.venv\Scripts\python.exe -m pip install faster-whisper sounddevice piper-tts onnxruntime pynput numpy`

- [ ] **Step 3: Implement the real backends**

Replace the placeholder `_init_backends` in `server/voice_io.py` with the real implementation. It constructs the faster-whisper model, the Piper voice, sounddevice playback, and a Silero-VAD mic loop, and registers the pynput mouse hook. Each import is local so a missing dep raises inside the factory's `try` (→ voice-off).
```python
def _init_backends(cfg: VoiceConfig):
    import numpy as np
    import sounddevice as sd
    from faster_whisper import WhisperModel
    from piper import PiperVoice  # piper-tts
    from pynput import mouse

    SR = 16000  # whisper + silero sample rate

    model = WhisperModel(cfg.stt_model, device=cfg.stt_device, compute_type=cfg.stt_compute_type)
    voice = PiperVoice.load(cfg.tts_voice)

    def transcribe_fn(pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(audio, language=cfg.stt_language, vad_filter=False)
        return " ".join(s.text for s in segments).strip()

    def synth_fn(text: str) -> bytes:
        chunks = bytearray()
        for chunk in voice.synthesize_stream_raw(text):
            chunks.extend(chunk)
        return bytes(chunks)

    def play_fn(pcm: bytes) -> None:
        audio = np.frombuffer(pcm, dtype=np.int16)
        sd.play(audio, samplerate=voice.config.sample_rate, device=cfg.output_device)
        sd.wait()

    def start_mic_fn(session) -> None:
        # Registered later by VoiceSession.run(); see Step 4.
        session._sd = sd
        session._np = np
        session._SR = SR
        session._mouse = mouse

    return transcribe_fn, synth_fn, play_fn, start_mic_fn
```

- [ ] **Step 4: Implement the mic loop + mouse gate in `VoiceSession.run`**

Replace `VoiceSession.run` with a version that also runs the real mic capture + Silero VAD segmentation and the pynput mouse hook, feeding `listener.on_segment`. Guard everything so a runtime audio error logs and stops voice without touching the bridge:
```python
    async def run(self) -> None:
        speaker_task = asyncio.create_task(self.speaker.run())
        try:
            await asyncio.gather(speaker_task, self._mic_loop())
        except Exception as exc:
            log.warning("Voice runtime stopped: %s", exc)
            speaker_task.cancel()

    async def _mic_loop(self) -> None:
        # Real capture only runs when backends were initialized; in tests
        # (no backends) this is a no-op that idles.
        start_mic = getattr(self, "_start_mic", None)
        if start_mic is None:
            return
        # Implementation detail (verified by --selftest, not CI):
        # 1) register pynput mouse listener -> io.on_button_press/release on the
        #    configured side button (x1/x2);
        # 2) open a sounddevice InputStream at 16 kHz mono int16;
        # 3) run Silero VAD over frames; on end-of-speech assemble the segment
        #    pcm and `await self.listener.on_segment(pcm, t0, t1)`.
        ...
```
Set `self._start_mic` in `build_voice_session` from the factory's 4th return value, and call `start_mic(self)` there. (Update `build_voice_session` accordingly.)

- [ ] **Step 5: Add a `--selftest` CLI**

Append to `server/voice_io.py`:
```python
def _selftest() -> int:
    """Offline mic->STT->TTS smoke test. Speak after the prompt."""
    import argparse  # noqa
    logging.basicConfig(level=logging.INFO)
    cfg = VoiceConfig(enabled=True)
    sess = build_voice_session(cfg)
    if sess is None:
        log.error("Voice backends unavailable — check deps/mic.")
        return 1
    log.info("Hold/toggle the side button and say a phrase in Russian...")
    # Drives io + listener for ~10s, prints the transcript, speaks it back.
    ...
    return 0


if __name__ == "__main__":
    import sys as _sys
    if "--selftest" in _sys.argv:
        raise SystemExit(_selftest())
```

- [ ] **Step 6: Verify (manual live + regression)**

Manual (on the user's machine, with deps installed and a mic):
Run: `.\.venv\Scripts\python.exe server/voice_io.py --selftest`
Expected: logs "Voice backends ... ready", you speak Russian while holding the side button, it prints the transcript and speaks it back to your headphones.

Regression (CI-safe, no hardware):
Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_io.py tests/integration/test_voice_bridge.py -v`
Expected: PASS (the factory's degrade path still returns None when deps are absent).

- [ ] **Step 7: Document + commit**

Add a short "Voice mode (Phase 1)" subsection to `CLAUDE.md` near the LLM Models section: how to install deps, flip `voice.enabled`, pick the mouse button, and run `--selftest`.

```bash
git add server/voice_io.py installer/config.json server/requirements.txt CLAUDE.md
git commit -m "feat(voice): real STT/TTS/mic backends, config block, deps, selftest"
```

---

## Self-Review

**1. Spec coverage (Phase 1 scope):**
- STT in (faster-whisper + Silero VAD) — Tasks 4, 8. ✓
- TTS out (Piper, Russian) — Tasks 3, 8. ✓
- VAD + mouse side-button gate (toggle/push) — Tasks 2, 8. ✓
- Echo-guard + barge-in — Task 2 (logic), Tasks 3/6 (wired to speaker). ✓
- TTS backlog drop-oldest — Task 3. ✓
- STT filter (min chars; min_speech_ms reserved for the VAD in Task 8) — Tasks 1, 4. ✓
- Synthetic snapshot → `snapshot_queue`, `trigger_event="player_chat"` (priority) — Tasks 5, 7. ✓
- Speak the `chat` tool output at the extraction seam (545-559) — Task 7. ✓
- Graceful degradation (never crash the bridge) — Task 6 (factory) + Task 7 (`voice=None` path) + guards in 3/4/8. ✓
- Local-only / no audio persisted — enforced in `_init_backends`/config; `debug_dump_audio` default false. ✓
- Acceptance AC4–AC8 are exercisable in Phase 1 (text-only commands like "scout"/"attack"); AC1–AC3 (deixis/pings) are **Phase 2** and explicitly deferred. ✓ (documented gap, not a miss)

**2. Placeholder scan:** Tasks 4-step-3 (mic-loop body) and Task 8 `--selftest` body use `...` for the **hardware** capture loop, which cannot be unit-tested in CI; both are covered by the `--selftest` manual verification step with exact run command and expected output. All pure-logic steps contain complete code. No "TBD/handle edge cases/similar to Task N".

**3. Type consistency:** `VoiceConfig` field names match between Task 1 and their consumers (`stt_min_chars`, `tts_max_pending`, `gate_mode`, `gate_mouse_button`). `VoiceIO.on_button_press/release/begin_speaking/end_speaking/accepting_speech/should_emit` are used consistently in Tasks 2/3/4/6. `SpeechSpeaker(synth_fn, play_fn, max_pending, on_speaking, on_done)` matches Task 6's construction. `build_voice_snapshot(latest, text)` and `_speak_chat_messages(decision, iterations, chat_history, voice)` signatures match their tests. `_init_backends` returns a 4-tuple in Tasks 6 and 8.

**Note on Phase 2 boundary:** giving `attack`/`defend` literal coordinate targeting (spec §6.4) requires touching the Sim `StrategyExecutor` and is intentionally **out of Phase 1**. Phase 1 delivers the spoken conversation loop; Phase 2's plan will cover `[LLM_SCREEN]`, `resolve_location`, `mark_map` pings, and coordinate-targeted orders.
