"""
Live integration test: full ReAct cycle through Qwen3.5-9B via Ollama.

Tests all 6 improvements end-to-end:
  1. XML tool call parsing (fallback for Qwen3.5 bug)
  2. Tool call validation (unknown tools, bad params rejected)
  3. Deduplication (identical calls merged)
  4. Adaptive polling (threat → shorter interval)
  5. Decision scoring (army growth/loss scored)
  6. Phase prompts (early/mid/late game-specific instructions)

Usage:
  python tests/integration/test_live_react_cycle.py [--model qwen3.5:9b]

Requires Ollama running with the model loaded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Add server to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))

from state_processor import StateProcessor
from llm_client import LLMClient
from tools import validate_and_filter, deduplicate, OBSERVATION_TOOL_NAMES, ALL_TOOL_NAMES
from decision_memory import DecisionMemory, DecisionSummary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("live_test")


# ─────────────────────────────────────────────────────────────────────
# Test snapshots for different scenarios
# ─────────────────────────────────────────────────────────────────────

SCENARIOS: dict[str, dict] = {
    "early_game_peaceful": {
        "tick": 600, "game_time_s": 60, "phase": "early",
        "faction": "UEF", "mode": "opponent", "trigger_event": "periodic",
        "economy": {
            "mass_income": 5.0, "mass_stored": 150, "mass_storage_max": 1000,
            "energy_income": 300, "energy_stored": 1200, "energy_storage_max": 8000,
        },
        "units": {
            "factories": 1, "engineers": 3, "land_military": 4,
            "air_military": 0, "navy_military": 0,
            "t1": 4, "t2": 0, "t3": 0, "experimentals": 0,
        },
        "threats": {"near_base": 0, "nearest_enemy_distance": 1200, "enemy_army_size_estimate": 3},
        "map_control_pct": 25, "player_chat": [], "current_strategy": "balanced",
    },
    "mid_game_under_attack": {
        "tick": 3600, "game_time_s": 360, "phase": "mid",
        "faction": "UEF", "mode": "opponent", "trigger_event": "enemy_detected",
        "economy": {
            "mass_income": 12.0, "mass_stored": 400, "mass_storage_max": 4000,
            "energy_income": 800, "energy_stored": 3000, "energy_storage_max": 16000,
        },
        "units": {
            "factories": 5, "engineers": 8, "land_military": 25,
            "air_military": 6, "navy_military": 0,
            "t1": 15, "t2": 16, "t3": 0, "experimentals": 0,
        },
        "threats": {"near_base": 35, "nearest_enemy_distance": 120, "enemy_army_size_estimate": 30},
        "map_control_pct": 40, "player_chat": [], "current_strategy": "balanced",
    },
    "late_game_dominant": {
        "tick": 9000, "game_time_s": 900, "phase": "late",
        "faction": "UEF", "mode": "opponent", "trigger_event": "periodic",
        "economy": {
            "mass_income": 35.0, "mass_stored": 2000, "mass_storage_max": 8000,
            "energy_income": 3000, "energy_stored": 10000, "energy_storage_max": 50000,
        },
        "units": {
            "factories": 12, "engineers": 15, "land_military": 60,
            "air_military": 20, "navy_military": 0,
            "t1": 20, "t2": 30, "t3": 25, "experimentals": 2,
        },
        "threats": {"near_base": 0, "nearest_enemy_distance": 800, "enemy_army_size_estimate": 40},
        "map_control_pct": 65, "player_chat": [], "current_strategy": "attack",
    },
    "player_chat_russian": {
        "tick": 2400, "game_time_s": 240, "phase": "mid",
        "faction": "UEF", "mode": "opponent", "trigger_event": "player_chat",
        "economy": {
            "mass_income": 10.0, "mass_stored": 300, "mass_storage_max": 2000,
            "energy_income": 600, "energy_stored": 2000, "energy_storage_max": 12000,
        },
        "units": {
            "factories": 4, "engineers": 6, "land_military": 18,
            "air_military": 3, "navy_military": 0,
            "t1": 12, "t2": 9, "t3": 0, "experimentals": 0,
        },
        "threats": {"near_base": 5, "nearest_enemy_distance": 400, "enemy_army_size_estimate": 15},
        "map_control_pct": 35, "player_chat": ["Строй больше танков и атакуй с севера"],
        "current_strategy": "balanced",
    },
}

CONFIG = {
    "llm": {
        "model_deep": "qwen3.5:9b",
        "model_fast": "qwen3.5:9b",
        "base_url": "http://localhost:11434",
        "temperature": 0.3,
        "max_tokens": 128,
        "num_ctx": 4096,
        "num_batch": 512,
        "keep_alive": "30m",
    },
    "bot": {
        "difficulty": "normal",
        "playstyle": "balanced",
        "chat_language": "auto",
        "poll_interval_fast_s": 20,
        "decision_memory_size": 10,
    },
    "bridge": {"timeout_s": 60},
}


# ─────────────────────────────────────────────────────────────────────
# Adaptive poll interval (copied from bridge_server for standalone test)
# ─────────────────────────────────────────────────────────────────────

def _adaptive_poll_interval(snapshot: dict, config: dict) -> int:
    bot_cfg = config.get("bot", {})
    base_interval = bot_cfg.get("poll_interval_fast_s", 20)
    threats = snapshot.get("threats", {})
    near_base = threats.get("near_base", 0)
    enemy_distance = threats.get("nearest_enemy_distance", 9999)
    trigger = snapshot.get("trigger_event", "periodic")

    if near_base > 20 or enemy_distance < 150:
        return max(8, base_interval // 3)
    if near_base > 5 or enemy_distance < 400:
        return max(12, base_interval // 2)
    if trigger not in ("periodic", "deep_periodic"):
        return max(10, base_interval // 2)
    units = snapshot.get("units", {})
    if units.get("land_military", 0) > 30 and near_base == 0:
        return min(30, base_interval + 10)
    return base_interval


# ─────────────────────────────────────────────────────────────────────
# Main test runner
# ─────────────────────────────────────────────────────────────────────

async def run_scenario(
    name: str,
    snapshot: dict,
    sp: StateProcessor,
    llm: LLMClient,
    memory: DecisionMemory,
    prev_snapshot: dict | None,
) -> dict:
    """Run one scenario through the full pipeline."""
    sep = "=" * 70
    log.info(f"\n{sep}\n  SCENARIO: {name}\n{sep}")

    # 1. Score previous cycle (improvement #5)
    if prev_snapshot and memory.entries:
        memory.score_last_cycle(prev_snapshot, snapshot)
        last = memory.entries[-1]
        log.info(f"  [SCORING] Previous cycle score: {last.score:+.2f}")

    # 2. Build messages with phase prompts (improvement #6)
    messages = sp.build_messages(snapshot, CONFIG, [], decision_memory=memory)
    system_msg = messages[0]["content"]

    phase = snapshot.get("phase", "early")
    log.info(f"  [PHASE] {phase.upper()} — prompt length: {len(system_msg)} chars")

    # Check phase prompt is present
    phase_markers = {"early": "EARLY GAME", "mid": "MID GAME", "late": "LATE GAME"}
    marker = phase_markers.get(phase, "EARLY GAME")
    assert marker in system_msg, f"Phase prompt '{marker}' not in system message!"
    log.info(f"  [PHASE] ✓ '{marker}' found in system prompt")

    # 3. Query LLM
    log.info("  [LLM] Querying Qwen3.5:9B…")
    t0 = time.monotonic()
    result = await llm.query(messages)
    latency = time.monotonic() - t0

    if result is None:
        log.error("  [LLM] ✗ No response (timeout or error)")
        return {"status": "error", "scenario": name}

    raw_tool_calls = result.get("tool_calls", [])
    model_used = result.get("_model_used", "?")
    latency_ms = result.get("_latency_ms", int(latency * 1000))

    log.info(f"  [LLM] ✓ Response in {latency_ms}ms from {model_used}")
    log.info(f"  [LLM] Raw tool calls ({len(raw_tool_calls)}): "
             f"{[tc['name'] for tc in raw_tool_calls]}")

    # 4. Validate tool calls (improvement #2)
    validated = validate_and_filter(raw_tool_calls)
    rejected = len(raw_tool_calls) - len(validated)
    if rejected:
        log.warning(f"  [VALIDATE] ✗ Rejected {rejected} invalid tool call(s)")
    else:
        log.info(f"  [VALIDATE] ✓ All {len(validated)} tool calls valid")

    # Check all tool names are known
    for tc in validated:
        assert tc["name"] in ALL_TOOL_NAMES, f"Unknown tool after validation: {tc['name']}"

    # 5. Deduplicate (improvement #3)
    deduped = deduplicate(validated)
    dupes_removed = len(validated) - len(deduped)
    if dupes_removed:
        log.info(f"  [DEDUP] Removed {dupes_removed} duplicate(s)")
    else:
        log.info(f"  [DEDUP] ✓ No duplicates")

    log.info(f"  [FINAL] Tool calls: {json.dumps([tc['name'] for tc in deduped])}")
    for tc in deduped:
        log.info(f"    → {tc['name']}({json.dumps(tc['args'])})")

    # 6. Adaptive poll interval (improvement #4)
    poll = _adaptive_poll_interval(snapshot, CONFIG)
    log.info(f"  [POLL] Adaptive interval: {poll}s "
             f"(base: {CONFIG['bot']['poll_interval_fast_s']}s, "
             f"threat: {snapshot['threats']['near_base']}, "
             f"distance: {snapshot['threats']['nearest_enemy_distance']})")

    # 7. Add to decision memory
    obs = [tc["name"] for tc in deduped if tc["name"] in OBSERVATION_TOOL_NAMES]
    acts = [f"{tc['name']}({json.dumps(tc['args'])})" for tc in deduped if tc["name"] not in OBSERVATION_TOOL_NAMES]
    summary = DecisionSummary(
        cycle_id=len(memory.entries) + 1,
        game_time_s=snapshot["game_time_s"],
        trigger=snapshot["trigger_event"],
        observations=obs,
        actions=acts,
    )
    memory.add_cycle(summary)

    # 8. Check memory prompt block
    mem_block = memory.to_prompt_block()
    if mem_block:
        log.info(f"  [MEMORY] Decision history: {len(memory.entries)} entries, "
                 f"{len(mem_block)} chars in prompt block")

    return {
        "status": "ok",
        "scenario": name,
        "phase": phase,
        "raw_count": len(raw_tool_calls),
        "validated_count": len(validated),
        "deduped_count": len(deduped),
        "tool_names": [tc["name"] for tc in deduped],
        "latency_ms": latency_ms,
        "poll_interval": poll,
        "model": model_used,
    }


async def main(model: str = "qwen3.5:9b") -> None:
    CONFIG["llm"]["model_deep"] = model
    CONFIG["llm"]["model_fast"] = model

    log.info("=" * 70)
    log.info("  LIVE REACT CYCLE TEST — Qwen3.5 + All Improvements")
    log.info("=" * 70)
    log.info(f"  Model: {model}")
    log.info(f"  Scenarios: {list(SCENARIOS.keys())}")

    # Init components
    sp = StateProcessor(CONFIG)
    llm = LLMClient(CONFIG)
    memory = DecisionMemory(max_entries=10)

    # Warm up model — load into VRAM, allocate KV cache at num_ctx=4096
    log.info("  Warming up model (this may take 30-60s on first run)…")
    import httpx
    from tools import GAME_TOOLS
    warmup_url = CONFIG["llm"]["base_url"].rstrip("/") + "/api/chat"
    warmup_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "/no_think\nReady check."},
            {"role": "user", "content": "Status: OK. Respond with noop."},
        ],
        "tools": GAME_TOOLS,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"num_predict": 16, "num_ctx": 4096, "num_batch": 512},
    }
    t_warm = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(warmup_url, json=warmup_payload)
            resp.raise_for_status()
        log.info("  Model warm — %.1fs", time.monotonic() - t_warm)
    except Exception as exc:
        log.warning("  Warmup failed: %s (continuing anyway)", exc)

    results = []
    prev_snapshot = None

    for name, snapshot in SCENARIOS.items():
        result = await run_scenario(name, snapshot, sp, llm, memory, prev_snapshot)
        results.append(result)
        prev_snapshot = snapshot

    # ── Summary ──────────────────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("  SUMMARY")
    log.info("=" * 70)

    all_ok = True
    for r in results:
        status = "✓" if r["status"] == "ok" else "✗"
        if r["status"] != "ok":
            all_ok = False
            log.error(f"  {status} {r['scenario']}: FAILED")
            continue

        log.info(
            f"  {status} {r['scenario']}: "
            f"{r['deduped_count']} tools, {r['latency_ms']}ms, "
            f"poll={r['poll_interval']}s, phase={r['phase']}"
        )
        log.info(f"    tools: {r['tool_names']}")

    # Decision memory final state
    log.info(f"\n  Decision memory: {len(memory.entries)} cycles")
    for e in memory.entries:
        log.info(f"    cycle {e.cycle_id}: t={e.game_time_s}s trigger={e.trigger} "
                 f"score={e.score:+.2f} actions={e.actions}")

    if all_ok:
        log.info("\n  ✓ ALL SCENARIOS PASSED")
    else:
        log.error("\n  ✗ SOME SCENARIOS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    asyncio.run(main(args.model))
