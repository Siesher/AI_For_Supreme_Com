# Integration: voice wiring in bridge_server
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

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
        decision={
            "tool_calls": [{"name": "chat", "args": {"message": "иду на север"}}]
        },
        iterations=[],
        chat_history=[],
        voice=FakeVoice(),
    )
    assert spoken == ["иду на север"]
