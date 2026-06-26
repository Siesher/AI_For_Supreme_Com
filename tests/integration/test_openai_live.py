"""Live smoke test for the OpenAIClient path (SP1 group C plumbing).

Unlike test_live_react_cycle.py (which exercises the legacy Ollama-native
/api/chat LLMClient), this drives the NEW OpenAIClient against an
OpenAI-compatible /v1/chat/completions endpoint — the exact code path used by
KoboldCpp, LM Studio, vLLM, TabbyAPI and Ollama's /v1. It proves the group C
wiring works end-to-end against a real, running engine.

Usage:
    python tests/integration/test_openai_live.py \
        --base-url http://localhost:11434/v1 --model qwen3.5:4b

Requires an OpenAI-compatible server running with the model available.
Exit code 0 = at least one scenario produced a parseable response.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Add server to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

from openai_client import OpenAIClient  # noqa: E402
from state_processor import StateProcessor  # noqa: E402
from decision_memory import DecisionMemory  # noqa: E402
from tools import validate_and_filter, deduplicate, ALL_TOOL_NAMES  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("openai_live")


SCENARIOS: dict[str, dict] = {
    "early_game_peaceful": {
        "tick": 600,
        "game_time_s": 60,
        "phase": "early",
        "faction": "UEF",
        "mode": "opponent",
        "trigger_event": "periodic",
        "economy": {
            "mass_income": 5.0,
            "mass_stored": 150,
            "mass_storage_max": 1000,
            "energy_income": 300,
            "energy_stored": 1200,
            "energy_storage_max": 8000,
        },
        "units": {
            "factories": 1,
            "engineers": 3,
            "land_military": 4,
            "air_military": 0,
            "navy_military": 0,
            "t1": 4,
            "t2": 0,
            "t3": 0,
            "experimentals": 0,
        },
        "threats": {
            "near_base": 0,
            "nearest_enemy_distance": 1200,
            "enemy_army_size_estimate": 3,
        },
        "map_control_pct": 25,
        "player_chat": [],
        "current_strategy": "balanced",
    },
    "mid_game_under_attack": {
        "tick": 3600,
        "game_time_s": 360,
        "phase": "mid",
        "faction": "UEF",
        "mode": "opponent",
        "trigger_event": "enemy_detected",
        "economy": {
            "mass_income": 12.0,
            "mass_stored": 400,
            "mass_storage_max": 4000,
            "energy_income": 800,
            "energy_stored": 3000,
            "energy_storage_max": 16000,
        },
        "units": {
            "factories": 5,
            "engineers": 8,
            "land_military": 25,
            "air_military": 6,
            "navy_military": 0,
            "t1": 15,
            "t2": 16,
            "t3": 0,
            "experimentals": 0,
        },
        "threats": {
            "near_base": 35,
            "nearest_enemy_distance": 120,
            "enemy_army_size_estimate": 30,
        },
        "map_control_pct": 40,
        "player_chat": [],
        "current_strategy": "balanced",
    },
}


def _make_config(base_url: str, model: str, timeout_s: int) -> dict[str, Any]:
    """Build a minimal config mirroring the resolved ``ollama``/``openai`` engine shape."""
    return {
        "llm": {
            "base_url": base_url,
            "model_deep": model,
            "model_fast": model,
            "temperature": 0.3,
            "max_tokens": 128,
            "api_key": "not-needed",
        },
        "bot": {
            "difficulty": "normal",
            "playstyle": "balanced",
            "chat_language": "auto",
            "poll_interval_fast_s": 20,
            "decision_memory_size": 10,
        },
        "bridge": {"timeout_s": timeout_s},
    }


async def _warmup(client: OpenAIClient, model: str) -> None:
    """Issue a tiny tool-less request to load the model into VRAM before timing."""
    log.info("Warming up %s (cold load may take 30-90s)…", model)
    t0 = time.monotonic()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "/no_think\nReady check."},
            {"role": "user", "content": "Reply with OK."},
        ],
        "max_tokens": 8,
        "stream": False,
    }
    try:
        resp = await client._http_client.post(
            client._base_url + "/chat/completions", json=payload
        )
        resp.raise_for_status()
        log.info("Warm in %.1fs", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001 — best-effort warmup
        log.warning("Warmup failed: %s (continuing anyway)", exc)


async def _run_scenario(
    name: str,
    snapshot: dict,
    sp: StateProcessor,
    client: OpenAIClient,
    config: dict,
) -> dict[str, Any]:
    """Run one snapshot through StateProcessor → OpenAIClient → validate/dedup."""
    sep = "=" * 70
    log.info("\n%s\n  SCENARIO: %s\n%s", sep, name, sep)

    memory = DecisionMemory(max_entries=10)
    messages = sp.build_messages(snapshot, config, [], decision_memory=memory)
    log.info(
        "  [PROMPT] %d messages, system prompt %d chars",
        len(messages),
        len(messages[0]["content"]),
    )

    log.info("  [LLM] Querying via OpenAIClient (/v1)…")
    t0 = time.monotonic()
    result = await client.query(messages)
    latency = time.monotonic() - t0

    if result is None:
        log.error("  [LLM] ✗ No response (timeout/HTTP error) after %.1fs", latency)
        return {"status": "error", "scenario": name}

    raw = result.get("tool_calls", [])
    log.info(
        "  [LLM] ✓ %dms from %s", result.get("_latency_ms"), result.get("_model_used")
    )
    log.info("  [LLM] Raw tool calls (%d): %s", len(raw), [tc["name"] for tc in raw])

    validated = validate_and_filter(raw)
    deduped = deduplicate(validated)
    for tc in deduped:
        assert tc["name"] in ALL_TOOL_NAMES, f"Unknown tool: {tc['name']}"
        log.info("    → %s(%s)", tc["name"], json.dumps(tc["args"], ensure_ascii=False))

    return {
        "status": "ok",
        "scenario": name,
        "raw_count": len(raw),
        "deduped_count": len(deduped),
        "tool_names": [tc["name"] for tc in deduped],
        "latency_ms": result.get("_latency_ms"),
        "model": result.get("_model_used"),
    }


async def main(base_url: str, model: str, timeout_s: int) -> None:
    log.info("=" * 70)
    log.info("  OPENAI-CLIENT LIVE SMOKE — SP1 group C plumbing")
    log.info("=" * 70)
    log.info("  base_url=%s  model=%s  timeout=%ss", base_url, model, timeout_s)

    config = _make_config(base_url, model, timeout_s)
    sp = StateProcessor(config)
    client = OpenAIClient(config)

    try:
        await _warmup(client, model)
        results: list[dict[str, Any]] = []
        for name, snapshot in SCENARIOS.items():
            results.append(await _run_scenario(name, snapshot, sp, client, config))
    finally:
        await client.close()

    log.info("\n%s\n  SUMMARY\n%s", "=" * 70, "=" * 70)
    any_ok = False
    for r in results:
        if r["status"] == "ok":
            any_ok = True
            log.info(
                "  ✓ %s: %d tools, %sms — %s",
                r["scenario"],
                r["deduped_count"],
                r["latency_ms"],
                r["tool_names"],
            )
        else:
            log.error("  ✗ %s: FAILED", r["scenario"])

    if any_ok:
        log.info("\n  ✓ OpenAIClient round-trip works against the live engine.")
    else:
        log.error(
            "\n  ✗ No scenario produced a response — group C plumbing or engine is broken."
        )
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:5001/v1")
    parser.add_argument("--model", default="koboldcpp")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.model, args.timeout))
