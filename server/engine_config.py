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
        "model_deep": "koboldcpp",  # KoboldCpp serves the single loaded GGUF; name is arbitrary
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

    Args:
        config: Top-level configuration dict (mutated in place under "llm").

    Returns:
        Dict with keys: engine_name, api_style, base_url, model_deep, model_fast.
    """
    llm = config.setdefault("llm", {})
    engine_name = llm.get("engine")

    presets = dict(DEFAULT_ENGINES)
    presets.update(llm.get("engines", {}) or {})

    if engine_name and engine_name in presets:
        preset = presets[engine_name]
        api_style = preset.get("api_style", "openai")
        base_url = preset.get(
            "base_url", llm.get("base_url", "http://localhost:5001/v1")
        )
        model_deep = preset.get("model_deep", llm.get("model_deep"))
        model_fast = preset.get("model_fast", model_deep)
    else:
        if engine_name:
            log.warning(
                "Unknown llm.engine '%s' — falling back to legacy backend.", engine_name
            )
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

    log.info(
        "Engine resolved: %s (api_style=%s base_url=%s deep=%s fast=%s)",
        engine_name,
        api_style,
        base_url,
        model_deep,
        model_fast,
    )
    return {
        "engine_name": engine_name,
        "api_style": api_style,
        "base_url": base_url,
        "model_deep": model_deep,
        "model_fast": model_fast,
    }
