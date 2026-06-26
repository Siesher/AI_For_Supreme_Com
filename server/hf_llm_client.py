# HuggingFace Transformers LLM Client with TurboQuant KV cache — server/hf_llm_client.py
#
# Alternative backend to Ollama. Loads model with bitsandbytes NF4 quantization
# (4-bit weights) and uses TurboQuant (arXiv:2504.19874) for KV cache compression.
#
# Same query() interface as LLMClient — drop-in replacement.
# Requires: torch, transformers, bitsandbytes, scipy, accelerate

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import torch

from xml_tool_parser import parse_fallback_tool_calls

log = logging.getLogger(__name__)


class HFLLMClient:
    """HuggingFace Transformers client with TurboQuant KV cache.

    Drop-in replacement for LLMClient. Loads the model once at init
    and keeps it in VRAM between queries (no cold start per query).

    Config keys used:
        llm.hf_model_id: HuggingFace model ID (default: "Qwen/Qwen3-8B")
        llm.temperature: Sampling temperature
        llm.max_tokens: Max new tokens per generation
        turbo_quant.key_bits: Bits for key cache Q_prod quantization (default: 3)
        turbo_quant.value_bits: Bits for value cache Q_mse quantization (default: 3)
        turbo_quant.outlier_channels: Mixed-precision outlier channels (default: 0)
    """

    def __init__(self, config: dict) -> None:
        # Lazy imports — only loaded when HF backend is selected
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from turbo_quant import TurboQuantConfig
        from turbo_quant_cache import TurboQuantCache

        llm_cfg = config.get("llm", {})
        tq_cfg = config.get("turbo_quant", {})

        self._model_id: str = llm_cfg.get("hf_model_id", "Qwen/Qwen3-8B")
        self._temperature: float = llm_cfg.get("temperature", 0.3)
        self._max_tokens: int = llm_cfg.get("max_tokens", 256)
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # TurboQuant config — head_dim auto-detected from model after load
        self._tq_key_bits: int = tq_cfg.get("key_bits", 3)
        self._tq_value_bits: int = tq_cfg.get("value_bits", 3)
        self._tq_outlier_channels: int = tq_cfg.get("outlier_channels", 0)
        self._tq_outlier_bits_extra: int = tq_cfg.get("outlier_bits_extra", 1)

        # Store classes for later use
        self._TurboQuantConfig = TurboQuantConfig
        self._TurboQuantCache = TurboQuantCache

        # Load model with 4-bit NF4 quantization
        log.info("Loading HF model %s with NF4 quantization…", self._model_id)
        t0 = time.monotonic()

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )

        self._tokenizer = AutoTokenizer.from_pretrained(
            self._model_id,
            trust_remote_code=True,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self._model_id,
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=torch.float16,
            trust_remote_code=True,
        )
        self._model.eval()

        # Auto-detect head_dim from model config
        mc = self._model.config
        self._head_dim: int = getattr(mc, "head_dim", None) or (
            mc.hidden_size // mc.num_attention_heads
        )
        self._num_kv_heads: int = getattr(
            mc, "num_key_value_heads", mc.num_attention_heads
        )
        self._num_layers: int = mc.num_hidden_layers

        # Build TurboQuant config with correct head_dim
        self._tq_config = TurboQuantConfig(
            key_bits=self._tq_key_bits,
            value_bits=self._tq_value_bits,
            head_dim=self._head_dim,
            seed=42,
            outlier_channels=self._tq_outlier_channels,
            outlier_bits_extra=self._tq_outlier_bits_extra,
        )

        elapsed = time.monotonic() - t0
        log.info(
            "HF model loaded in %.1fs: %s | head_dim=%d, kv_heads=%d, layers=%d | "
            "TurboQuant: K=%d-bit V=%d-bit (%.1fx compression vs FP16)",
            elapsed,
            self._model_id,
            self._head_dim,
            self._num_kv_heads,
            self._num_layers,
            self._tq_key_bits,
            self._tq_value_bits,
            self._estimate_compression(),
        )

        # Thread pool — model.generate() is synchronous
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hf_llm")

    # ------------------------------------------------------------------
    # Public API (matches LLMClient interface)
    # ------------------------------------------------------------------

    async def query(
        self,
        messages: list[dict[str, str]],
        model_tag: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Send messages to the HF model and return tool calls.

        Args:
            messages: Chat messages (system + user).
            model_tag: Ignored (single model backend).

        Returns:
            Dict with tool_calls, _model_used, _latency_ms, or None on error.
        """
        t_start = time.monotonic()

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._executor,
                self._generate_sync,
                messages,
            )

            latency_ms = int((time.monotonic() - t_start) * 1000)
            result["_model_used"] = f"hf:{self._model_id}"
            result["_latency_ms"] = latency_ms

            log.info(
                "HF LLM ok: model=%s latency=%dms tools=%s",
                self._model_id,
                latency_ms,
                [tc["name"] for tc in result.get("tool_calls", [])],
            )
            return result

        except Exception:
            log.exception("HF LLM generation error")
            return None

    # ------------------------------------------------------------------
    # Internal: synchronous generation
    # ------------------------------------------------------------------

    def _generate_sync(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Run model.generate() with TurboQuant KV cache."""
        from tools import GAME_TOOLS

        # Format messages via tokenizer's chat template
        # Qwen3.5 tokenizer supports tools= parameter
        hf_tools = _convert_tools_for_template(GAME_TOOLS)

        try:
            text = self._tokenizer.apply_chat_template(
                messages,
                tools=hf_tools,
                tokenize=False,
                add_generation_prompt=True,
            )
        except TypeError:
            # Fallback: tokenizer doesn't support tools= kwarg
            # Inject tool descriptions into system message manually
            text = self._tokenizer.apply_chat_template(
                _inject_tools_into_messages(messages, GAME_TOOLS),
                tokenize=False,
                add_generation_prompt=True,
            )

        inputs = self._tokenizer(text, return_tensors="pt").to(self._model.device)
        input_len = inputs["input_ids"].shape[1]

        # Create fresh TurboQuant cache per generation
        tq_cache = self._TurboQuantCache(self._tq_config, device=self._model.device)

        with torch.no_grad():
            gen_kwargs: dict[str, Any] = {
                "max_new_tokens": self._max_tokens,
                "past_key_values": tq_cache,
                "use_cache": True,
            }
            if self._temperature > 0:
                gen_kwargs["do_sample"] = True
                gen_kwargs["temperature"] = self._temperature
            else:
                gen_kwargs["do_sample"] = False

            outputs = self._model.generate(**inputs, **gen_kwargs)

        new_tokens = outputs[0][input_len:]
        response_text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)

        # Log KV cache compression stats
        stats = tq_cache.get_memory_stats()
        if stats.get("compression_ratio"):
            log.info(
                "TurboQuant KV cache: seq=%d, compression=%.1fx, saved=%d KB",
                stats.get("seq_length", 0),
                stats["compression_ratio"],
                (
                    stats.get("total_original_bytes", 0)
                    - stats.get("total_quantized_bytes", 0)
                )
                // 1024,
            )

        tool_calls = _parse_tool_calls(response_text)
        return {"tool_calls": tool_calls}

    def _estimate_compression(self) -> float:
        """Estimate TurboQuant compression ratio for logging."""
        from turbo_quant import TurboQuantEngine

        engine = TurboQuantEngine(self._tq_config)
        stats = engine.estimate_memory_bytes(seq_len=1024, num_heads=self._num_kv_heads)
        return stats.get("compression_ratio", 1.0)


# ---------------------------------------------------------------------------
# Tool call parsing (model output → tool_calls list)
# ---------------------------------------------------------------------------


def _parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse tool calls from HF model output, falling back to noop on plain text.

    Delegates recovery to the shared free-text parser (name(...) calls,
    name\\n{json}, bare/flat JSON, fences, <tool_call>/<function=...> leaks, with
    truncation repair); the noop fallback here is this backend's policy.
    """
    recovered = parse_fallback_tool_calls(text)
    if recovered:
        return recovered
    log.info("HF LLM plain text (no tools parsed): %s", text[:200])
    return [{"name": "noop", "args": {"reasoning": text[:200]}}]


# ---------------------------------------------------------------------------
# Tool format helpers
# ---------------------------------------------------------------------------


def _convert_tools_for_template(game_tools: list[dict]) -> list[dict]:
    """Convert GAME_TOOLS (Ollama format) to HF chat template format.

    GAME_TOOLS already uses OpenAI-compatible format:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    HF apply_chat_template expects the same structure.
    """
    return game_tools  # Already compatible


def _inject_tools_into_messages(
    messages: list[dict[str, str]],
    game_tools: list[dict],
) -> list[dict[str, str]]:
    """Fallback: inject tool descriptions into the system message.

    Used when the tokenizer's chat template doesn't support tools= kwarg.
    """
    tool_desc_parts = []
    for tool in game_tools:
        fn = tool.get("function", tool)
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])

        param_strs = []
        for pname, pdef in props.items():
            req_mark = " (REQUIRED)" if pname in required else ""
            enum = pdef.get("enum", [])
            enum_str = f" one of: {enum}" if enum else ""
            param_strs.append(
                f"  - {pname}: {pdef.get('type', 'string')}{enum_str}{req_mark}"
            )

        params_block = "\n".join(param_strs) if param_strs else "  (no parameters)"
        tool_desc_parts.append(f"### {name}\n{desc}\nParameters:\n{params_block}")

    tools_text = "\n\n".join(tool_desc_parts)
    tool_instruction = (
        "\n\n# Available Tools\n"
        'Call tools using JSON: <tool_call>{"name": "tool_name", "arguments": {"key": "value"}}</tool_call>\n'
        "You may call multiple tools.\n\n"
        f"{tools_text}\n"
    )

    patched = list(messages)
    if patched and patched[0].get("role") == "system":
        patched[0] = {**patched[0], "content": patched[0]["content"] + tool_instruction}
    else:
        patched.insert(0, {"role": "system", "content": tool_instruction})

    return patched
