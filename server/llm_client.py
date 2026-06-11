# T018: LLM Client — server/llm_client.py
#
# Sends messages + tools to Ollama's /api/chat endpoint.
# On timeout, auto-retries once with the fast model.
# Returns a list of tool_calls dicts, or None on error.

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from tools import GAME_TOOLS
from xml_tool_parser import _parse_xml_tool_calls  # re-exported for callers/tests

log = logging.getLogger(__name__)

_OLLAMA_CHAT_PATH = "/api/chat"
_CONSECUTIVE_TIMEOUT_LIMIT = 3


class LLMClient:
    """Async Ollama client with tool-calling support."""

    def __init__(self, config: dict) -> None:
        llm_cfg = config.get("llm", {})
        bridge_cfg = config.get("bridge", {})
        self._base_url = llm_cfg.get("base_url", "http://localhost:11434")
        self._model_deep = llm_cfg.get("model_deep", "qwen3.5:9b")
        self._model_fast = llm_cfg.get("model_fast", "qwen3.5:9b")
        self._temperature = llm_cfg.get("temperature", 0.3)
        self._max_tokens = llm_cfg.get("max_tokens", 128)
        self._timeout_s = bridge_cfg.get("timeout_s", 30)

        # Ollama inference tuning
        self._num_ctx = llm_cfg.get("num_ctx", 4096)
        self._num_batch = llm_cfg.get("num_batch", 512)
        self._keep_alive = llm_cfg.get("keep_alive", "30m")
        # MoE-specific: num_gpu controls how many layers on GPU,
        # num_thread limits CPU parallelism so the game still gets cores.
        self._num_gpu = llm_cfg.get("num_gpu")  # None = Ollama auto
        self._num_thread = llm_cfg.get("num_thread")  # None = Ollama auto

        self._consecutive_deep_timeouts = 0
        self._deep_degraded = False

        # Persistent HTTP client — reuses TCP connection across queries
        self._http_client = httpx.AsyncClient(
            timeout=self._timeout_s,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query(
        self,
        messages: list[dict[str, str]],
        model_tag: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """
        POST to Ollama /api/chat with tool definitions.
        Returns {"tool_calls": [...], "_model_used": ..., "_latency_ms": ...}
        or None on unrecoverable error.
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
        """Close the persistent HTTP client."""
        await self._http_client.aclose()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _query_model(
        self,
        messages: list[dict[str, str]],
        model_tag: str,
    ) -> Optional[dict[str, Any]]:
        url = self._base_url.rstrip("/") + _OLLAMA_CHAT_PATH
        options = {
            "temperature": self._temperature,
            "num_predict": self._max_tokens,
            "num_ctx": self._num_ctx,
            "num_batch": self._num_batch,
        }
        if self._num_gpu is not None:
            options["num_gpu"] = self._num_gpu
        if self._num_thread is not None:
            options["num_thread"] = self._num_thread

        payload = {
            "model": model_tag,
            "messages": messages,
            "tools": GAME_TOOLS,
            "stream": False,
            "think": False,
            "keep_alive": self._keep_alive,
            "options": options,
        }

        t_start = time.monotonic()
        try:
            resp = await self._http_client.post(url, json=payload)
            resp.raise_for_status()

            latency_ms = int((time.monotonic() - t_start) * 1000)
            data = resp.json()
            msg = data.get("message", {})

            tool_calls_raw = msg.get("tool_calls", [])
            content = msg.get("content", "")

            # Parse tool calls into clean format
            tool_calls = []
            for tc in tool_calls_raw:
                fn = tc.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments", {})
                if name:
                    tool_calls.append({"name": name, "args": args})

            # If model responded with text instead of tool_calls JSON,
            # try to parse Qwen3.5 XML format: <function=name><parameter=k>v</parameter></function>
            if not tool_calls and content:
                xml_parsed = _parse_xml_tool_calls(content)
                if xml_parsed:
                    tool_calls = xml_parsed
                    log.warning(
                        "LLM returned XML tool calls (Qwen3.5 format), parsed %d call(s): %s",
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
