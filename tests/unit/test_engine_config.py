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
    cfg = {
        "llm": {
            "engine": "koboldcpp",
            "engines": {
                "koboldcpp": {
                    "api_style": "openai",
                    "base_url": "http://localhost:9999/v1",
                    "model_deep": "my-gguf",
                    "model_fast": "my-gguf",
                }
            },
        }
    }
    r = resolve_engine(cfg)
    assert r["base_url"] == "http://localhost:9999/v1"
    assert r["model_deep"] == "my-gguf"


def test_legacy_backend_ollama_no_engine_key():
    cfg = {
        "llm": {
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model_deep": "qwen3.5:14b",
            "model_fast": "qwen3.5:4b",
        }
    }
    r = resolve_engine(cfg)
    assert r["api_style"] == "ollama"
    assert r["base_url"] == "http://localhost:11434"
    assert r["engine_name"] == "ollama"


def test_legacy_backend_hf_turbo():
    cfg = {
        "llm": {
            "backend": "hf_turbo",
            "model_deep": "Qwen/Qwen3-8B",
            "model_fast": "Qwen/Qwen3-8B",
        }
    }
    r = resolve_engine(cfg)
    assert r["api_style"] == "hf_turbo"


def test_unknown_engine_falls_back_to_legacy():
    cfg = {
        "llm": {
            "engine": "does-not-exist",
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model_deep": "qwen3.5:14b",
        }
    }
    r = resolve_engine(cfg)
    assert r["api_style"] == "ollama"  # graceful fallback, not a crash


def test_lmstudio_and_tabbyapi_presets_exist():
    assert "lmstudio" in DEFAULT_ENGINES
    assert "tabbyapi" in DEFAULT_ENGINES
    assert DEFAULT_ENGINES["lmstudio"]["base_url"].endswith("/v1")
