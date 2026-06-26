# Pluggable Inference Engines (KoboldCpp primary) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the inference engine swappable via a one-line config switch, with **KoboldCpp** as the default, by routing all LLM traffic through the OpenAI-compatible `/v1/chat/completions` API while keeping the existing Ollama-native and HF-TurboQuant paths working.

**Architecture:** A new `OpenAIClient` (mirrors `LLMClient`'s `query`/`close` interface) talks `/v1/chat/completions` and works with KoboldCpp, LM Studio, vLLM, Ollama's `/v1`, and TabbyAPI. A small `resolve_engine()` reads `llm.engine` against a preset registry, normalizes `base_url`/`model_deep`/`model_fast`/`api_style` back into `config["llm"]`, and `bridge_server.py` picks the client by `api_style` (`openai` → `OpenAIClient`, `ollama` → existing `LLMClient`, `hf_turbo` → existing `HFLLMClient`). Switching engines = change one string in `config.json`. This implements SP1 Task group C of the design spec (`docs/superpowers/specs/2026-06-11-llm-bot-perfection-design.md` §3.3) and refines the locked §2.3 decision (KoboldCpp default; Ollama/vLLM optional).

**Tech Stack:** Python 3 (asyncio, httpx — already a dependency), pytest (dependency-free async via `asyncio.run`), PowerShell launcher, JSON config.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `server/xml_tool_parser.py` | Create | Qwen3.5 XML→tool_calls fallback parser (extracted, shared by both clients) |
| `server/llm_client.py` | Modify | Re-export `_parse_xml_tool_calls` from new module (no behavior change) |
| `server/engine_config.py` | Create | Engine preset registry + `resolve_engine()` |
| `server/openai_client.py` | Create | `OpenAIClient` — OpenAI `/v1/chat/completions` with tools |
| `server/bridge_server.py` | Modify | Resolve engine, select client by `api_style`, engine-aware warmup, guard Ollama-only env |
| `installer/config.json` | Modify | Add `llm.engine` + `llm.engines` presets; KoboldCpp default |
| `installer/start_bridge.ps1` | Modify | Engine-aware launch (skip Ollama checks for non-Ollama engines) |
| `tests/unit/test_engine_config.py` | Create | Tests for `resolve_engine()` |
| `tests/unit/test_openai_client.py` | Create | Tests for `OpenAIClient` (mocked httpx) |
| `CLAUDE.md` | Modify | Document the engine-switch workflow |

**Interface contract (must not break):** `ReactLoop` (`server/react_loop.py:86`) calls `await self._llm_client.query(conversation, model_tag)` and expects `{"tool_calls": [{"name": str, "args": dict}, ...], "_model_used": str, "_latency_ms": int}` or `None`. `OpenAIClient` must match this exactly, plus `async def close()`.

---

### Task 1: Extract the XML tool-call parser into a shared module

**Files:**
- Create: `server/xml_tool_parser.py`
- Modify: `server/llm_client.py:24-60` (remove inline parser+regexes, import instead)
- Test: existing `tests/unit/test_xml_tool_parser.py` (imports `from llm_client import _parse_xml_tool_calls` — must still pass via re-export)

- [ ] **Step 1: Create the shared module**

Create `server/xml_tool_parser.py` with the parser moved verbatim from `llm_client.py` (keep the `_parse_xml_tool_calls` name so existing imports keep working):

```python
# server/xml_tool_parser.py
#
# Qwen3.5 XML tool-call fallback parser. Shared by llm_client (Ollama-native)
# and openai_client (OpenAI /v1). Some Qwen3.5 builds emit tool calls as XML
# text instead of structured tool_calls; this recovers them.

from __future__ import annotations

import re
from typing import Any

# <function=name><parameter=key>value</parameter></function>  (optionally <tool_call>-wrapped)
_XML_FUNCTION_RE = re.compile(
    r"<function=(?P<name>[a-z_]+)>\s*(?P<params>(?:<parameter=[^>]+>[^<]*</parameter>\s*)*)</function>",
    re.DOTALL,
)
_XML_PARAM_RE = re.compile(
    r"<parameter=(?P<key>[a-z_]+)>(?P<value>[^<]*)</parameter>",
)


def _parse_xml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract tool calls from Qwen3.5 XML format fallback.

    Handles both bare <function=...> and wrapped <tool_call><function=...></tool_call>.
    """
    results: list[dict[str, Any]] = []
    for m in _XML_FUNCTION_RE.finditer(text):
        name = m.group("name")
        params_block = m.group("params")
        args: dict[str, Any] = {}
        for pm in _XML_PARAM_RE.finditer(params_block):
            key = pm.group("key")
            raw_value = pm.group("value").strip()
            if raw_value.isdigit():
                args[key] = int(raw_value)
            elif raw_value in ("true", "false"):
                args[key] = raw_value == "true"
            else:
                args[key] = raw_value
        results.append({"name": name, "args": args})
    return results
```

- [ ] **Step 2: Point `llm_client.py` at the shared module**

In `server/llm_client.py`, delete the inline `_XML_FUNCTION_RE`, `_XML_PARAM_RE`, and `_parse_xml_tool_calls` definition (current lines 24-60), and delete the now-unused `import re` (line 11). Add this import near the other imports (after `from tools import GAME_TOOLS`):

```python
from xml_tool_parser import _parse_xml_tool_calls  # re-exported for callers/tests
```

The rest of `llm_client.py` is unchanged — it still calls `_parse_xml_tool_calls(content)` at the same place.

- [ ] **Step 3: Run the existing parser tests to verify no regression**

Run: `uv run pytest tests/unit/test_xml_tool_parser.py -v`
Expected: PASS (all 12 tests) — the import `from llm_client import _parse_xml_tool_calls` resolves via the re-export.

- [ ] **Step 4: Commit**

```bash
git add server/xml_tool_parser.py server/llm_client.py
git commit -m "refactor: extract XML tool-call parser into shared module"
```

---

### Task 2: Engine preset registry + `resolve_engine()`

**Files:**
- Create: `server/engine_config.py`
- Test: `tests/unit/test_engine_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_engine_config.py`:

```python
"""Tests for inference-engine preset resolution."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

from engine_config import resolve_engine, DEFAULT_ENGINES


def test_default_engine_koboldcpp():
    cfg = {"llm": {"engine": "koboldcpp"}}
    r = resolve_engine(cfg)
    assert r["api_style"] == "openai"
    assert r["base_url"] == "http://localhost:5001/v1"
    assert r["engine_name"] == "koboldcpp"
    # writes resolved values back into config["llm"]
    assert cfg["llm"]["base_url"] == "http://localhost:5001/v1"
    assert cfg["llm"]["api_style"] == "openai"


def test_explicit_engine_vllm():
    cfg = {"llm": {"engine": "vllm"}}
    r = resolve_engine(cfg)
    assert r["api_style"] == "openai"
    assert r["base_url"] == "http://localhost:8000/v1"
    assert r["model_deep"].startswith("Qwen")


def test_custom_engines_override_defaults():
    cfg = {"llm": {"engine": "koboldcpp",
                   "engines": {"koboldcpp": {"api_style": "openai",
                                             "base_url": "http://localhost:9999/v1",
                                             "model_deep": "my-gguf",
                                             "model_fast": "my-gguf"}}}}
    r = resolve_engine(cfg)
    assert r["base_url"] == "http://localhost:9999/v1"
    assert r["model_deep"] == "my-gguf"


def test_legacy_backend_ollama_no_engine_key():
    cfg = {"llm": {"backend": "ollama", "base_url": "http://localhost:11434",
                   "model_deep": "qwen3.5:14b", "model_fast": "qwen3.5:4b"}}
    r = resolve_engine(cfg)
    assert r["api_style"] == "ollama"
    assert r["base_url"] == "http://localhost:11434"
    assert r["engine_name"] == "ollama"


def test_legacy_backend_hf_turbo():
    cfg = {"llm": {"backend": "hf_turbo", "model_deep": "Qwen/Qwen3-8B",
                   "model_fast": "Qwen/Qwen3-8B"}}
    r = resolve_engine(cfg)
    assert r["api_style"] == "hf_turbo"


def test_unknown_engine_falls_back_to_legacy():
    cfg = {"llm": {"engine": "does-not-exist", "backend": "ollama",
                   "base_url": "http://localhost:11434", "model_deep": "qwen3.5:14b"}}
    r = resolve_engine(cfg)
    assert r["api_style"] == "ollama"  # graceful fallback, not a crash


def test_lmstudio_and_tabbyapi_presets_exist():
    assert "lmstudio" in DEFAULT_ENGINES
    assert "tabbyapi" in DEFAULT_ENGINES
    assert DEFAULT_ENGINES["lmstudio"]["base_url"].endswith("/v1")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_engine_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine_config'`

- [ ] **Step 3: Implement `engine_config.py`**

Create `server/engine_config.py`:

```python
# server/engine_config.py
#
# Inference-engine preset registry + resolver. Lets the user swap engines
# (KoboldCpp / Ollama / LM Studio / vLLM / TabbyAPI) with one config key:
#   "llm": { "engine": "koboldcpp" }
# Engines expose an OpenAI-compatible /v1 API (api_style "openai") except
# Ollama's native /api/chat (api_style "ollama") and HF in-process (hf_turbo).

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Built-in presets. base_url for "openai" engines INCLUDES the /v1 suffix.
DEFAULT_ENGINES: dict[str, dict] = {
    "koboldcpp": {
        "api_style": "openai",
        "base_url": "http://localhost:5001/v1",
        "model_deep": "koboldcpp",   # KoboldCpp serves the single loaded GGUF; name is arbitrary
        "model_fast": "koboldcpp",
    },
    "ollama": {  # Ollama via its OpenAI-compatible endpoint
        "api_style": "openai",
        "base_url": "http://localhost:11434/v1",
        "model_deep": "qwen3.5:14b",
        "model_fast": "qwen3.5:4b",
    },
    "ollama_native": {  # Ollama via /api/chat (keeps tuned options + keep_alive)
        "api_style": "ollama",
        "base_url": "http://localhost:11434",
        "model_deep": "qwen3.5:14b",
        "model_fast": "qwen3.5:4b",
    },
    "lmstudio": {
        "api_style": "openai",
        "base_url": "http://localhost:1234/v1",
        "model_deep": "qwen3.5-14b",
        "model_fast": "qwen3.5-4b",
    },
    "vllm": {
        "api_style": "openai",
        "base_url": "http://localhost:8000/v1",
        "model_deep": "Qwen/Qwen3.5-14B-Instruct",
        "model_fast": "Qwen/Qwen3.5-4B-Instruct",
    },
    "tabbyapi": {
        "api_style": "openai",
        "base_url": "http://localhost:5000/v1",
        "model_deep": "Qwen3.5-14B-exl3",
        "model_fast": "Qwen3.5-4B-exl3",
    },
}


def resolve_engine(config: dict) -> dict:
    """Resolve the effective inference engine and normalize config["llm"].

    Precedence:
      1. llm.engine naming a preset in (DEFAULT_ENGINES merged with llm.engines)
      2. legacy llm.backend == "hf_turbo" -> hf_turbo; otherwise ollama-native

    Side effect: writes resolved base_url/model_deep/model_fast/api_style back
    into config["llm"] so LLMClient/HFLLMClient/warmup all read the same values.

    Returns {engine_name, api_style, base_url, model_deep, model_fast}.
    """
    llm = config.setdefault("llm", {})
    engine_name = llm.get("engine")

    presets = dict(DEFAULT_ENGINES)
    presets.update(llm.get("engines", {}) or {})

    if engine_name and engine_name in presets:
        preset = presets[engine_name]
        api_style = preset.get("api_style", "openai")
        base_url = preset.get("base_url", llm.get("base_url", "http://localhost:5001/v1"))
        model_deep = preset.get("model_deep", llm.get("model_deep"))
        model_fast = preset.get("model_fast", model_deep)
    else:
        if engine_name:
            log.warning("Unknown llm.engine '%s' — falling back to legacy backend.", engine_name)
        legacy_backend = llm.get("backend", "ollama")
        api_style = "hf_turbo" if legacy_backend == "hf_turbo" else "ollama"
        base_url = llm.get("base_url", "http://localhost:11434")
        model_deep = llm.get("model_deep", "qwen3.5:14b")
        model_fast = llm.get("model_fast", model_deep)
        engine_name = legacy_backend

    llm["base_url"] = base_url
    llm["model_deep"] = model_deep
    llm["model_fast"] = model_fast
    llm["api_style"] = api_style

    log.info("Engine resolved: %s (api_style=%s base_url=%s deep=%s fast=%s)",
             engine_name, api_style, base_url, model_deep, model_fast)
    return {
        "engine_name": engine_name,
        "api_style": api_style,
        "base_url": base_url,
        "model_deep": model_deep,
        "model_fast": model_fast,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_engine_config.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add server/engine_config.py tests/unit/test_engine_config.py
git commit -m "feat: add inference-engine preset registry and resolver"
```

---

### Task 3: `OpenAIClient` — OpenAI `/v1/chat/completions` with tools

**Files:**
- Create: `server/openai_client.py`
- Test: `tests/unit/test_openai_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_openai_client.py`:

```python
"""Tests for the OpenAI-compatible inference client."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

import httpx
from openai_client import OpenAIClient, _to_openai_messages, _parse_openai_tool_calls


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _client():
    return OpenAIClient({"llm": {"engine": "koboldcpp", "base_url": "http://localhost:5001/v1",
                                 "model_deep": "kobold", "model_fast": "kobold",
                                 "max_tokens": 64},
                         "bridge": {"timeout_s": 5}})


def _run(coro):
    return asyncio.run(coro)


def test_parse_openai_tool_calls_json_string_args():
    raw = [{"id": "call_1", "type": "function",
            "function": {"name": "set_strategy",
                         "arguments": '{"strategy": "rush", "reasoning": "weak enemy"}'}}]
    out = _parse_openai_tool_calls(raw)
    assert out == [{"name": "set_strategy", "args": {"strategy": "rush", "reasoning": "weak enemy"}}]


def test_parse_openai_tool_calls_dict_args():
    # lenient servers may already return a dict
    raw = [{"function": {"name": "defend", "arguments": {"radius": 80}}}]
    out = _parse_openai_tool_calls(raw)
    assert out == [{"name": "defend", "args": {"radius": 80}}]


def test_parse_openai_tool_calls_bad_json_becomes_empty_args():
    raw = [{"function": {"name": "scout", "arguments": "{not json"}}]
    out = _parse_openai_tool_calls(raw)
    assert out == [{"name": "scout", "args": {}}]


def test_to_openai_messages_converts_assistant_and_tool():
    msgs = [
        {"role": "system", "content": "/no_think"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "get_enemy_army", "arguments": {}}}]},
        {"role": "tool", "content": '{"land": 5}'},
    ]
    out = _to_openai_messages(msgs)
    assert out[0]["role"] == "system" and out[1]["role"] == "user"
    asst = out[2]
    assert asst["tool_calls"][0]["type"] == "function"
    assert asst["tool_calls"][0]["function"]["name"] == "get_enemy_army"
    assert isinstance(asst["tool_calls"][0]["function"]["arguments"], str)  # JSON string
    call_id = asst["tool_calls"][0]["id"]
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == call_id  # ids match in order


def test_query_returns_tool_calls(monkeypatch):
    client = _client()
    payload = {"choices": [{"message": {"content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "attack",
                                             "arguments": '{"unit_type": "land"}'}}]}}]}

    async def fake_post(url, json=None):
        assert url == "http://localhost:5001/v1/chat/completions"
        assert json["model"] == "kobold"
        assert "tools" in json
        return _FakeResp(payload)

    monkeypatch.setattr(client._http_client, "post", fake_post)
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result["tool_calls"] == [{"name": "attack", "args": {"unit_type": "land"}}]
    assert result["_model_used"] == "kobold"
    assert "_latency_ms" in result


def test_query_xml_fallback(monkeypatch):
    client = _client()
    payload = {"choices": [{"message": {
        "content": "<function=set_strategy><parameter=strategy>turtle</parameter></function>",
        "tool_calls": []}}]}

    async def fake_post(url, json=None):
        return _FakeResp(payload)

    monkeypatch.setattr(client._http_client, "post", fake_post)
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result["tool_calls"] == [{"name": "set_strategy", "args": {"strategy": "turtle"}}]


def test_query_plain_text_becomes_noop(monkeypatch):
    client = _client()
    payload = {"choices": [{"message": {"content": "I will just wait.", "tool_calls": []}}]}

    async def fake_post(url, json=None):
        return _FakeResp(payload)

    monkeypatch.setattr(client._http_client, "post", fake_post)
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result["tool_calls"][0]["name"] == "noop"


def test_query_timeout_returns_none(monkeypatch):
    client = _client()

    async def fake_post(url, json=None):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(client._http_client, "post", fake_post)
    # deep == fast == "kobold", so the retry also times out -> None
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_openai_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openai_client'`

- [ ] **Step 3: Implement `openai_client.py`**

Create `server/openai_client.py`:

```python
# server/openai_client.py
#
# OpenAI-compatible inference client (/v1/chat/completions with tools).
# Works with KoboldCpp, LM Studio, vLLM, Ollama's /v1, TabbyAPI — the engine
# is selected purely by base_url. Mirrors LLMClient's public interface so it
# is a drop-in inside ReactLoop: async query(messages, model_tag) / async close().

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx

from tools import GAME_TOOLS
from xml_tool_parser import _parse_xml_tool_calls

log = logging.getLogger(__name__)

_CHAT_PATH = "/chat/completions"
_CONSECUTIVE_TIMEOUT_LIMIT = 3


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Normalize internal/Ollama-style assistant+tool messages to OpenAI shape.

    ReactLoop builds assistant messages as
        {"role": "assistant", "tool_calls": [{"function": {"name", "arguments": <dict>}}]}
    and tool results as {"role": "tool", "content": <str>} (no id). OpenAI requires
    tool_calls[].id + type, arguments as a JSON string, and tool messages to carry
    a matching tool_call_id. Plain system/user/assistant(text) messages pass through.
    """
    out: list[dict] = []
    pending_tool_ids: list[str] = []
    counter = 0
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            tcs: list[dict] = []
            pending_tool_ids = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", tc)
                name = fn.get("name")
                args = fn.get("arguments", fn.get("args", {}))
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                counter += 1
                call_id = f"call_{counter}"
                pending_tool_ids.append(call_id)
                tcs.append({"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": args}})
            out.append({"role": "assistant", "content": m.get("content", ""), "tool_calls": tcs})
        elif role == "tool":
            call_id = pending_tool_ids.pop(0) if pending_tool_ids else f"call_{counter}"
            out.append({"role": "tool", "content": m.get("content", ""), "tool_call_id": call_id})
        else:
            out.append(m)
    return out


def _parse_openai_tool_calls(raw: list[dict]) -> list[dict[str, Any]]:
    """Parse OpenAI response tool_calls into the internal {"name","args"} shape.

    OpenAI returns arguments as a JSON string; lenient servers may return a dict.
    """
    out: list[dict[str, Any]] = []
    for tc in raw or []:
        fn = tc.get("function", {})
        name = fn.get("name")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                log.warning("Tool call arguments not valid JSON: %s", args[:120])
                args = {}
        if name:
            out.append({"name": name, "args": args})
    return out


class OpenAIClient:
    """Async OpenAI-compatible client with tool-calling + deep/fast degrade."""

    def __init__(self, config: dict) -> None:
        llm_cfg = config.get("llm", {})
        bridge_cfg = config.get("bridge", {})
        self._base_url = llm_cfg.get("base_url", "http://localhost:5001/v1").rstrip("/")
        self._model_deep = llm_cfg.get("model_deep", "koboldcpp")
        self._model_fast = llm_cfg.get("model_fast", self._model_deep)
        self._temperature = llm_cfg.get("temperature", 0.3)
        self._max_tokens = llm_cfg.get("max_tokens", 128)
        self._api_key = llm_cfg.get("api_key", "not-needed")
        self._timeout_s = bridge_cfg.get("timeout_s", 30)

        self._consecutive_deep_timeouts = 0
        self._deep_degraded = False

        self._http_client = httpx.AsyncClient(
            timeout=self._timeout_s,
            headers={"Authorization": f"Bearer {self._api_key}"},
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )

    async def query(
        self,
        messages: list[dict[str, str]],
        model_tag: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """POST to /v1/chat/completions with tools. Returns the internal decision
        dict {"tool_calls", "_model_used", "_latency_ms"} or None on hard failure."""
        if model_tag is None:
            model_tag = self._model_deep

        if self._deep_degraded and model_tag == self._model_deep:
            log.warning("Deep model degraded — using fast model.")
            model_tag = self._model_fast

        result = await self._query_model(messages, model_tag)

        if result is None and model_tag == self._model_deep:
            self._consecutive_deep_timeouts += 1
            log.warning("Deep model timed out (consecutive: %d). Retrying with fast.",
                        self._consecutive_deep_timeouts)
            if self._consecutive_deep_timeouts >= _CONSECUTIVE_TIMEOUT_LIMIT:
                self._deep_degraded = True
                log.error("Deep model degraded after %d timeouts.", self._consecutive_deep_timeouts)
            result = await self._query_model(messages, self._model_fast)
        elif result is not None and model_tag == self._model_deep:
            self._consecutive_deep_timeouts = 0

        return result

    async def close(self) -> None:
        await self._http_client.aclose()

    async def _query_model(
        self,
        messages: list[dict[str, str]],
        model_tag: str,
    ) -> Optional[dict[str, Any]]:
        url = self._base_url + _CHAT_PATH
        payload = {
            "model": model_tag,
            "messages": _to_openai_messages(messages),
            "tools": GAME_TOOLS,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }

        t_start = time.monotonic()
        try:
            resp = await self._http_client.post(url, json=payload)
            resp.raise_for_status()

            latency_ms = int((time.monotonic() - t_start) * 1000)
            data = resp.json()
            choices = data.get("choices", [])
            msg = choices[0].get("message", {}) if choices else {}

            tool_calls = _parse_openai_tool_calls(msg.get("tool_calls") or [])
            content = msg.get("content") or ""

            if not tool_calls and content:
                xml_parsed = _parse_xml_tool_calls(content)
                if xml_parsed:
                    tool_calls = xml_parsed
                    log.warning("LLM returned XML tool calls, parsed %d call(s): %s",
                                len(xml_parsed), [tc["name"] for tc in xml_parsed])
                else:
                    log.info("LLM returned plain text (no tools): %s", content[:200])
                    tool_calls.append({"name": "noop", "args": {"reasoning": content[:200]}})

            result = {
                "tool_calls": tool_calls,
                "_model_used": model_tag,
                "_latency_ms": latency_ms,
            }
            log.info("LLM ok: model=%s latency=%dms tools=%s", model_tag, latency_ms,
                     [tc["name"] for tc in tool_calls])
            return result

        except httpx.TimeoutException:
            log.warning("LLM timeout: model=%s timeout=%ss", model_tag, self._timeout_s)
            return None
        except httpx.HTTPStatusError as exc:
            log.error("LLM HTTP error: model=%s status=%s", model_tag, exc.response.status_code)
            return None
        except Exception:
            log.exception("LLM unexpected error: model=%s", model_tag)
            return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_openai_client.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add server/openai_client.py tests/unit/test_openai_client.py
git commit -m "feat: add OpenAI-compatible inference client with tool calling"
```

---

### Task 4: Wire engine selection + engine-aware warmup into the bridge server

**Files:**
- Modify: `server/bridge_server.py` (imports; `main()` lines 185-248; warmup 137-211)

- [ ] **Step 1: Add the engine-aware warmup dispatcher**

In `server/bridge_server.py`, **rename** the existing `_warmup_ollama` to keep it as the Ollama branch, and add an OpenAI warmup + dispatcher. Replace the function header at line 137 (`async def _warmup_ollama(config: dict) -> None:`) — keep its body — and insert these two functions immediately after it (before `async def main`):

```python
async def _warmup_openai(config: dict) -> None:
    """Warm up an OpenAI-compatible engine (/v1/chat/completions) so the first
    real query isn't paying cold-load latency."""
    import httpx
    from tools import GAME_TOOLS

    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "http://localhost:5001/v1").rstrip("/")
    api_key = llm_cfg.get("api_key", "not-needed")
    models = {llm_cfg.get("model_deep"), llm_cfg.get("model_fast")}

    for model_tag in models:
        if not model_tag:
            continue
        payload = {
            "model": model_tag,
            "messages": [
                {"role": "system", "content": "/no_think\nReady check."},
                {"role": "user", "content": "Status: OK. Respond with noop."},
            ],
            "tools": GAME_TOOLS,
            "max_tokens": 16,
            "stream": False,
        }
        log.info("Warming up model %s @ %s …", model_tag, base_url)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=120,
                    headers={"Authorization": f"Bearer {api_key}"}) as client:
                resp = await client.post(base_url + "/chat/completions", json=payload)
                resp.raise_for_status()
            log.info("Model %s warm — %.1fs", model_tag, time.monotonic() - t0)
        except Exception as exc:
            log.warning("Warmup failed for %s: %s (will retry on first real query)", model_tag, exc)


async def _warmup(config: dict, api_style: str) -> None:
    """Dispatch warmup by engine api_style."""
    if api_style == "ollama":
        await _warmup_ollama(config)
    elif api_style == "openai":
        await _warmup_openai(config)
    else:  # hf_turbo loads in-process during client construction
        log.info("Skipping warmup for api_style=%s (in-process load)", api_style)
```

- [ ] **Step 2: Resolve the engine and guard Ollama-only env in `main()`**

In `server/bridge_server.py`, add the import near the top (after `from pipe_server import PipeServer`):

```python
from engine_config import resolve_engine
```

Then in `main()`, replace the block from line 200 down through the warmup call (currently the `kv_cache_type` env setup at 201-208 and `await _warmup_ollama(config)` at 211) with:

```python
    # Resolve which inference engine to use (KoboldCpp/Ollama/LM Studio/vLLM/…)
    resolved = resolve_engine(config)
    api_style = resolved["api_style"]
    log.info("  Engine  : %s (api_style=%s, %s)",
             resolved["engine_name"], api_style, resolved["base_url"])

    # Ollama-native KV-cache quantization is only meaningful for the ollama backend
    if api_style == "ollama":
        kv_cache_type = config.get("llm", {}).get("kv_cache_type", "")
        if kv_cache_type and kv_cache_type != "f16":
            os.environ.setdefault("OLLAMA_FLASH_ATTENTION", "1")
            os.environ.setdefault("OLLAMA_KV_CACHE_TYPE", kv_cache_type)
            log.info("  KV cache: %s (Flash Attention: ON)", kv_cache_type)
        else:
            log.info("  KV cache: f16 (default)")

    # Warm up the engine (loads model into VRAM, avoids cold-start on first query)
    await _warmup(config, api_style)
```

Note: the existing `log.info("  Model ...")` at lines 197-198 stays; `resolve_engine` already normalized `config["llm"]["model_deep"]/["model_fast"]`, so it prints the resolved models.

- [ ] **Step 3: Select the client by `api_style`**

In `server/bridge_server.py`, replace the backend-selection block (currently lines 232-243, the `backend = config.get("llm", {}).get("backend", "ollama")` … `llm_client = LLMClient(config) if LLMClient else None` section) with:

```python
    # Select LLM client by resolved api_style
    if api_style == "hf_turbo":
        HFLLMClient = _try_import("hf_llm_client", "HFLLMClient")
        if HFLLMClient:
            log.info("Using HF Transformers + TurboQuant backend")
            llm_client = HFLLMClient(config)
        else:
            log.error("hf_llm_client not available — falling back to OpenAI client")
            OpenAIClient = _try_import("openai_client", "OpenAIClient")
            llm_client = OpenAIClient(config) if OpenAIClient else None
    elif api_style == "ollama":
        log.info("Using Ollama-native (/api/chat) backend")
        llm_client = LLMClient(config) if LLMClient else None
    else:  # "openai" — KoboldCpp / LM Studio / vLLM / Ollama /v1 / TabbyAPI
        OpenAIClient = _try_import("openai_client", "OpenAIClient")
        log.info("Using OpenAI-compatible backend (/v1/chat/completions)")
        llm_client = OpenAIClient(config) if OpenAIClient else None
```

- [ ] **Step 4: Smoke-test that the server module imports and resolves without a live engine**

Run: `uv run python -c "import sys; sys.path.insert(0,'server'); from engine_config import resolve_engine; c={'llm':{'engine':'koboldcpp'}}; print(resolve_engine(c)['api_style'], c['llm']['base_url'])"`
Expected output: `openai http://localhost:5001/v1`

Run: `uv run python -c "import sys; sys.path.insert(0,'server'); import bridge_server; print('import ok')"`
Expected: `import ok` (no ImportError)

- [ ] **Step 5: Run the full unit suite to confirm no regressions**

Run: `uv run pytest tests/unit -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Commit**

```bash
git add server/bridge_server.py
git commit -m "feat: select inference client by engine api_style (KoboldCpp/Ollama/OpenAI)"
```

---

### Task 5: Config — KoboldCpp default + engine presets

**Files:**
- Modify: `installer/config.json` (the `llm` block, lines 7-23)

- [ ] **Step 1: Replace the `llm` block**

In `installer/config.json`, replace the entire `"llm": { ... }` object (lines 7-23) with:

```json
  "llm": {
    "engine": "koboldcpp",
    "_engine_options": "koboldcpp (default) | ollama | ollama_native | lmstudio | vllm | tabbyapi. Change this one key to switch engines.",
    "engines": {
      "koboldcpp":     { "api_style": "openai", "base_url": "http://localhost:5001/v1",  "model_deep": "koboldcpp",                    "model_fast": "koboldcpp" },
      "ollama":        { "api_style": "openai", "base_url": "http://localhost:11434/v1", "model_deep": "qwen3.5:14b",                  "model_fast": "qwen3.5:4b" },
      "ollama_native": { "api_style": "ollama", "base_url": "http://localhost:11434",    "model_deep": "qwen3.5:14b",                  "model_fast": "qwen3.5:4b" },
      "lmstudio":      { "api_style": "openai", "base_url": "http://localhost:1234/v1",  "model_deep": "qwen3.5-14b",                  "model_fast": "qwen3.5-4b" },
      "vllm":          { "api_style": "openai", "base_url": "http://localhost:8000/v1",  "model_deep": "Qwen/Qwen3.5-14B-Instruct",    "model_fast": "Qwen/Qwen3.5-4B-Instruct" },
      "tabbyapi":      { "api_style": "openai", "base_url": "http://localhost:5000/v1",  "model_deep": "Qwen3.5-14B-exl3",             "model_fast": "Qwen3.5-4B-exl3" }
    },
    "backend": "ollama",
    "_backend_comment": "Legacy fallback when 'engine' is unset; 'hf_turbo' selects the HF+TurboQuant in-process backend.",
    "api_key": "not-needed",
    "temperature": 0.3,
    "max_tokens": 128,
    "num_ctx": 4096,
    "num_batch": 512,
    "keep_alive": "30m",
    "kv_cache_type": "q8_0",
    "_kv_cache_comment": "Ollama-native only (api_style=ollama). Ignored by OpenAI engines (configure those at their own launch)."
  },
```

- [ ] **Step 2: Validate the JSON parses and resolves to KoboldCpp**

Run: `uv run python -c "import json,sys; sys.path.insert(0,'server'); from engine_config import resolve_engine; c=json.load(open('installer/config.json',encoding='utf-8')); r=resolve_engine(c); print(r['engine_name'], r['api_style'], r['base_url'])"`
Expected output: `koboldcpp openai http://localhost:5001/v1`

- [ ] **Step 3: Commit**

```bash
git add installer/config.json
git commit -m "feat: default to KoboldCpp engine + ship swappable engine presets"
```

---

### Task 6: Engine-aware launcher

**Files:**
- Modify: `installer/start_bridge.ps1` (replace the Ollama-specific Steps 0-1, lines 28-72)

- [ ] **Step 1: Replace the Ollama-only setup with engine-aware logic**

In `installer/start_bridge.ps1`, replace everything from the `# Step 0` comment block through the end of the model-ping loop (current lines 28-72, ending at the `}` that closes `foreach ($model in $modelsToCheck)`) with:

```powershell
# ---------------------------------------------------------------------------
# Resolve engine (mirror server/engine_config.py defaults)
# ---------------------------------------------------------------------------
$engineName = if ($config.llm.engine) { $config.llm.engine } else { "ollama_native" }

$engineDefaults = @{
    koboldcpp     = @{ api_style = "openai"; base_url = "http://localhost:5001/v1" }
    ollama        = @{ api_style = "openai"; base_url = "http://localhost:11434/v1" }
    ollama_native = @{ api_style = "ollama"; base_url = "http://localhost:11434" }
    lmstudio      = @{ api_style = "openai"; base_url = "http://localhost:1234/v1" }
    vllm          = @{ api_style = "openai"; base_url = "http://localhost:8000/v1" }
    tabbyapi      = @{ api_style = "openai"; base_url = "http://localhost:5000/v1" }
}

if ($engineDefaults.ContainsKey($engineName)) {
    $apiStyle = $engineDefaults[$engineName].api_style
    $baseUrl  = $engineDefaults[$engineName].base_url
    # config.llm.engines.<name> overrides the built-in default if present
    if ($config.llm.engines -and $config.llm.engines.$engineName) {
        if ($config.llm.engines.$engineName.api_style) { $apiStyle = $config.llm.engines.$engineName.api_style }
        if ($config.llm.engines.$engineName.base_url)  { $baseUrl  = $config.llm.engines.$engineName.base_url }
    }
} else {
    $apiStyle = "ollama"; $baseUrl = "http://localhost:11434"
}

Write-Step "Inference engine: $engineName (api_style=$apiStyle, $baseUrl)"

# ---------------------------------------------------------------------------
# Engine setup / health check
# ---------------------------------------------------------------------------
if ($apiStyle -eq "ollama") {
    Write-Step "Configuring Ollama environment"
    $kvCacheType = if ($config.llm.kv_cache_type) { $config.llm.kv_cache_type } else { "q8_0" }
    $env:OLLAMA_FLASH_ATTENTION   = "1"
    $env:OLLAMA_KV_CACHE_TYPE     = $kvCacheType
    $env:OLLAMA_NUM_PARALLEL      = "1"
    $env:OLLAMA_GPU_OVERHEAD      = "3221225472"   # 3 GiB reserved for the game
    $env:OLLAMA_KEEP_ALIVE        = "30m"
    $env:OLLAMA_MAX_LOADED_MODELS = "1"
    Write-OK "Flash Attention ON, KV cache $kvCacheType, GPU overhead 3 GiB reserved"

    Write-Step "Check Ollama is running"
    try {
        Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -UseBasicParsing | Out-Null
        Write-OK "Ollama running"
    } catch {
        Write-Fail "Ollama not running. Start it first (ollama serve)."
        exit 1
    }
} else {
    # OpenAI-compatible engine (KoboldCpp / LM Studio / vLLM / TabbyAPI)
    Write-Step "Check $engineName is serving an OpenAI API"
    try {
        Invoke-WebRequest -Uri "$baseUrl/models" -TimeoutSec 3 -UseBasicParsing | Out-Null
        Write-OK "$engineName reachable at $baseUrl"
    } catch {
        Write-Fail "$engineName not reachable at $baseUrl/models."
        if ($engineName -eq "koboldcpp") {
            Write-Host "  Start it, e.g.:" -ForegroundColor Yellow
            Write-Host "    koboldcpp.exe --model <qwen3.5-14b-q4_k_m.gguf> --usecuda --contextsize 8192 --port 5001" -ForegroundColor Gray
        } elseif ($engineName -eq "lmstudio") {
            Write-Host "  In LM Studio: load a model and start the local server (port 1234), or run: lms server start" -ForegroundColor Yellow
        } elseif ($engineName -eq "vllm") {
            Write-Host "  Start vLLM's OpenAI server on port 8000 (WSL2 build for Blackwell)." -ForegroundColor Yellow
        }
        exit 1
    }
}
```

(The script's later Step 2 — starting `bridge_server.py` — and the summary section are unchanged. The summary line `Models: ... (fast) / ... (deep)` still works because those config keys remain present.)

- [ ] **Step 2: Verify the script parses (syntax check, no execution side effects)**

Run: `powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'installer/start_bridge.ps1'), [ref]$null, [ref]$null); 'parse ok'"`
Expected: `parse ok`

- [ ] **Step 3: Commit**

```bash
git add installer/start_bridge.ps1
git commit -m "feat: make bridge launcher engine-aware (KoboldCpp/OpenAI/Ollama)"
```

---

### Task 7: Document the engine-switch workflow

**Files:**
- Modify: `CLAUDE.md` (the "LLM Backend Selection" subsection under "## LLM Models")

- [ ] **Step 1: Replace the backend-selection subsection**

In `CLAUDE.md`, replace the `### LLM Backend Selection (\`llm.backend\` in config.json)` subsection (the table that lists `ollama` / `hf_turbo`) with:

```markdown
### Inference Engine Selection (`llm.engine` in config.json)

Switch engines by changing **one key** — `llm.engine` — to a preset in `llm.engines`:

| `engine` | api_style | base_url | Quant | When to use |
|----------|-----------|----------|-------|-------------|
| `koboldcpp` (default) | openai | `:5001/v1` | GGUF Q4 | Native Windows, single .exe, best VRAM control + GBNF tool-grammar |
| `ollama` | openai | `:11434/v1` | GGUF Q4 | Incumbent, zero-install; Ollama's OpenAI endpoint |
| `ollama_native` | ollama | `:11434` | GGUF Q4 | Ollama via `/api/chat` (keeps tuned options + `keep_alive`, `kv_cache_type`) |
| `lmstudio` | openai | `:1234/v1` | GGUF Q4 | GUI one-click; `lms server start` |
| `vllm` | openai | `:8000/v1` | NVFP4/FP8 | Max throughput (WSL2 on Blackwell); experimental |
| `tabbyapi` | openai | `:5000/v1` | exl3 | ExLlamaV3; fast single-stream |

All `openai` engines go through `server/openai_client.py` (`/v1/chat/completions` with `tools`).
`ollama_native` uses `server/llm_client.py`. `hf_turbo` (set legacy `llm.backend`) uses `server/hf_llm_client.py`.
Engine speed is NOT the bottleneck for this bot (even 31 tok/s on 14B Q4 meets the 3-4 s/decision budget),
so the default favors native-Windows reliability + game co-residency over raw FP4 throughput.

**KoboldCpp quick start:** `koboldcpp.exe --model qwen3.5-14b-q4_k_m.gguf --usecuda --contextsize 8192 --port 5001`
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document pluggable inference-engine selection"
```

---

## Self-Review

**1. Spec coverage (SP1 §3.3 Task group C):**
- C1 "Refactor `llm_client.py` to OpenAI-compatible `/v1/chat/completions` with tools; keep Ollama working; retain XML fallback" → Task 3 (`OpenAIClient`) + Task 1 (shared XML parser); Ollama-native kept via `ollama_native`/`LLMClient` (Task 4). ✅
- C2 "Config: `model_deep`/`model_fast`; document the backend-swap path" → Task 5 (presets, KoboldCpp default) + Task 7 (docs). ✅
- Refinement of §2.3 "KoboldCpp default; Ollama/vLLM optional via base_url+model" → Tasks 4-6. ✅
- User's explicit ask (option 2: "make KoboldCpp primary, wire into config.json/launcher, easily test different engines") → Tasks 4 (selection), 5 (default + presets), 6 (launcher). ✅
- Note: §3.2 Task group B (None-crash, loud failures, prompt_tokens) is intentionally OUT of scope here — it's a separate SP1 sub-plan.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step has complete code; every command has expected output. ✅

**3. Type/name consistency:**
- `resolve_engine(config) -> {engine_name, api_style, base_url, model_deep, model_fast}` — same keys used in Task 4 (`resolved["api_style"]`, `resolved["engine_name"]`, `resolved["base_url"]`) and asserted in Task 2 tests. ✅
- `OpenAIClient.query(messages, model_tag=None)` returns `{"tool_calls":[{"name","args"}], "_model_used", "_latency_ms"}` — matches `ReactLoop` contract (`server/react_loop.py:86-92`) and Task 3 tests. ✅
- `_parse_xml_tool_calls` name preserved in Task 1 → existing `test_xml_tool_parser.py` import + `llm_client` re-export + `openai_client` import all agree. ✅
- `api_style` values `{"openai","ollama","hf_turbo"}` consistent across `engine_config.py`, `bridge_server.py` selection, and `_warmup` dispatch. ✅
- `_to_openai_messages` / `_parse_openai_tool_calls` defined in Task 3 and imported by name in Task 3 tests. ✅

No issues found.

---

## Execution Handoff

**Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.

**2. Inline Execution** — execute tasks in this session via executing-plans, with checkpoints.

Tasks 1-3 are pure-Python TDD (fully unit-tested offline). Tasks 4-6 are wiring/launcher (verified by import + JSON-resolve + PowerShell-parse smoke checks). The real end-to-end check is the live test: launch KoboldCpp on `:5001`, run the bridge, confirm the decision log shows non-empty `tool_calls` from the `/v1` path.
