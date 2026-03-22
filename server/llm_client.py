# T018: LLM Client — server/llm_client.py
#
# Sends prompts to Ollama's /api/generate endpoint.
# On timeout (10s), auto-retries once with the fast model.
# Returns a parsed StrategicDecision dict, or None on unrecoverable error.

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

import httpx

# Strip Qwen3.5 thinking blocks: <think>...</think> or Thinking...\n...done thinking.\n
_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>|Thinking\s*\.{3}.*?done thinking\.\s*",
    re.DOTALL,
)

log = logging.getLogger(__name__)

_OLLAMA_GENERATE_PATH = "/api/generate"
_CONSECUTIVE_TIMEOUT_LIMIT = 3   # after this many 8B timeouts, route all to 4B


class LLMClient:
    """Async Ollama client. Call query(prompt, model_tag) to get a decision."""

    def __init__(self, config: dict) -> None:
        llm_cfg             = config.get("llm", {})
        bridge_cfg          = config.get("bridge", {})
        self._base_url      = llm_cfg.get("base_url", "http://localhost:11434")
        self._model_deep    = llm_cfg.get("model_deep", "qwen3.5:9b")
        self._model_fast    = llm_cfg.get("model_fast", "qwen3.5:4b")
        self._temperature   = llm_cfg.get("temperature", 0.3)
        self._max_tokens    = llm_cfg.get("max_tokens", 256)
        self._timeout_s     = bridge_cfg.get("timeout_s", 10)

        self._consecutive_deep_timeouts = 0
        self._deep_degraded = False   # True if 8B is too slow; route all to 4B

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query(
        self,
        prompt: str,
        model_tag: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """
        POST to Ollama /api/generate. Returns parsed StrategicDecision or None.
        If model_tag is None, defaults to model_deep.
        On timeout, retries once with model_fast.
        """
        if model_tag is None:
            model_tag = self._model_deep

        # Degrade deep model if it keeps timing out (T053)
        if self._deep_degraded and model_tag == self._model_deep:
            log.warning("Deep model degraded — falling back to fast model for this query.")
            model_tag = self._model_fast

        result = await self._query_model(prompt, model_tag)

        if result is None and model_tag == self._model_deep:
            # Primary timeout — retry once with fast model
            self._consecutive_deep_timeouts += 1
            log.warning(
                "Deep model timed out (consecutive: %d). Retrying with fast model.",
                self._consecutive_deep_timeouts,
            )
            if self._consecutive_deep_timeouts >= _CONSECUTIVE_TIMEOUT_LIMIT:
                self._deep_degraded = True
                log.error(
                    "Deep model timed out %d times in a row. "
                    "Routing all traffic to fast model for this session.",
                    self._consecutive_deep_timeouts,
                )
            result = await self._query_model(prompt, self._model_fast)
        elif result is not None and model_tag == self._model_deep:
            self._consecutive_deep_timeouts = 0

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _query_model(
        self,
        prompt: str,
        model_tag: str,
    ) -> Optional[dict[str, Any]]:
        url     = self._base_url.rstrip("/") + _OLLAMA_GENERATE_PATH
        payload = {
            "model":  model_tag,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "think":  False,          # Qwen3.5: disable thinking at API level
            "options": {
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
                "stop":        ["}"],
            },
        }

        t_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()

            latency_ms = int((time.monotonic() - t_start) * 1000)
            data = resp.json()
            raw_text = data.get("response", "")

            # Strip any thinking blocks leaked despite think=false
            raw_text = _THINK_BLOCK_RE.sub("", raw_text).strip()

            # Ensure JSON is properly terminated (Ollama stop token issue)
            if not raw_text.endswith("}"):
                raw_text += "}"

            decision = json.loads(raw_text)
            decision["_model_used"]   = model_tag
            decision["_latency_ms"]   = latency_ms
            log.debug("LLM ok: model=%s latency=%dms strategy=%s",
                      model_tag, latency_ms, decision.get("strategy"))
            return decision

        except httpx.TimeoutException:
            log.warning("LLM timeout: model=%s timeout=%ss", model_tag, self._timeout_s)
            return None
        except httpx.HTTPStatusError as exc:
            log.error("LLM HTTP error: model=%s status=%s", model_tag, exc.response.status_code)
            return None
        except json.JSONDecodeError as exc:
            log.error("LLM JSON decode error: model=%s err=%s", model_tag, exc)
            return None
        except Exception as exc:
            log.exception("LLM unexpected error: model=%s", model_tag)
            return None
