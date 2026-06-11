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
                tcs.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                )
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content", ""),
                    "tool_calls": tcs,
                }
            )
        elif role == "tool":
            call_id = pending_tool_ids.pop(0) if pending_tool_ids else f"call_{counter}"
            out.append(
                {
                    "role": "tool",
                    "content": m.get("content", ""),
                    "tool_call_id": call_id,
                }
            )
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
        """POST to /v1/chat/completions with tools.

        Returns the internal decision dict {"tool_calls", "_model_used", "_latency_ms"}
        or None on hard failure.
        """
        if model_tag is None:
            model_tag = self._model_deep

        if self._deep_degraded and model_tag == self._model_deep:
            log.warning("Deep model degraded — using fast model.")
            model_tag = self._model_fast

        result = await self._query_model(messages, model_tag)

        if result is None and model_tag == self._model_deep:
            self._consecutive_deep_timeouts += 1
            log.warning(
                "Deep model timed out (consecutive: %d). Retrying with fast.",
                self._consecutive_deep_timeouts,
            )
            if self._consecutive_deep_timeouts >= _CONSECUTIVE_TIMEOUT_LIMIT:
                self._deep_degraded = True
                log.error(
                    "Deep model degraded after %d timeouts.",
                    self._consecutive_deep_timeouts,
                )
            result = await self._query_model(messages, self._model_fast)
        elif result is not None and model_tag == self._model_deep:
            self._consecutive_deep_timeouts = 0

        return result

    async def close(self) -> None:
        """Close the underlying HTTP client."""
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
                    log.warning(
                        "LLM returned XML tool calls, parsed %d call(s): %s",
                        len(xml_parsed),
                        [tc["name"] for tc in xml_parsed],
                    )
                else:
                    log.info("LLM returned plain text (no tools): %s", content[:200])
                    tool_calls.append(
                        {"name": "noop", "args": {"reasoning": content[:200]}}
                    )

            result = {
                "tool_calls": tool_calls,
                "_model_used": model_tag,
                "_latency_ms": latency_ms,
            }
            log.info(
                "LLM ok: model=%s latency=%dms tools=%s",
                model_tag,
                latency_ms,
                [tc["name"] for tc in tool_calls],
            )
            return result

        except httpx.TimeoutException:
            log.warning("LLM timeout: model=%s timeout=%ss", model_tag, self._timeout_s)
            return None
        except httpx.HTTPStatusError as exc:
            log.error(
                "LLM HTTP error: model=%s status=%s",
                model_tag,
                exc.response.status_code,
            )
            return None
        except Exception:
            log.exception("LLM unexpected error: model=%s", model_tag)
            return None
