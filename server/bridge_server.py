# T013: Python asyncio bridge server — server/bridge_server.py
#
# Entry point for the Python side of the SupCom LLM AI Bot bridge.
# Orchestrates: PipeServer ↔ StateProcessor ↔ LLMClient ↔ DecisionLogger
#
# Usage:
#   python bridge_server.py [--config path/to/config.json]

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Will be implemented in later tasks; imported defensively here so the
# server starts and logs even if modules are stubs.
from pipe_server import PipeServer

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports with graceful stubs for not-yet-implemented modules
# ---------------------------------------------------------------------------

FA_PROCESS_NAME = "ForgedAlliance.exe"
# 32-bit injector — deployed alongside the DLL in FAF's bin directory
INJECT_EXE = r"C:\ProgramData\FAForever\bin\inject.exe"
DLL_PATH    = r"C:\ProgramData\FAForever\bin\llm_bridge.dll"


def _find_fa_pids() -> list[int]:
    """Return PIDs of any running ForgedAlliance.exe processes."""
    try:
        import psutil
        return [p.pid for p in psutil.process_iter(["name"])
                if p.info["name"] == FA_PROCESS_NAME]
    except ImportError:
        # psutil not installed — fall back to tasklist
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {FA_PROCESS_NAME}", "/FO", "CSV"],
                text=True, timeout=5,
            )
            pids = []
            for line in out.splitlines()[1:]:
                parts = line.strip('"').split('","')
                if len(parts) >= 2:
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        pass
            return pids
        except Exception:
            return []


def _inject_dll(pid: int) -> bool:
    """Inject llm_bridge.dll into process *pid* using the 32-bit inject.exe helper."""
    injector = Path(INJECT_EXE)
    if not injector.exists():
        log.warning("inject.exe not found at %s — skipping injection", INJECT_EXE)
        return False
    if not Path(DLL_PATH).exists():
        log.warning("llm_bridge.dll not found at %s — skipping injection", DLL_PATH)
        return False

    try:
        result = subprocess.run(
            [str(injector), str(pid), DLL_PATH],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            log.info("DLL injected into PID %d", pid)
            return True
        else:
            log.error("inject.exe failed (pid=%d): %s", pid, result.stderr.strip())
            return False
    except subprocess.TimeoutExpired:
        log.error("inject.exe timed out (pid=%d)", pid)
        return False
    except Exception as exc:
        log.error("inject.exe error (pid=%d): %s", pid, exc)
        return False


INJECT_DELAY_S = 30  # Wait for FA to finish init before injecting


async def _watch_and_inject() -> None:
    """Continuously poll for ForgedAlliance.exe and inject llm_bridge.dll."""
    injected: set[int] = set()
    pending: dict[int, float] = {}  # pid → first_seen monotonic time
    log.info("Process watcher started — watching for %s", FA_PROCESS_NAME)

    while True:
        now = asyncio.get_event_loop().time()
        for pid in _find_fa_pids():
            if pid in injected:
                continue
            if pid not in pending:
                pending[pid] = now
                log.info(
                    "Detected %s PID=%d — waiting %ds before injection…",
                    FA_PROCESS_NAME, pid, INJECT_DELAY_S,
                )
            elif now - pending[pid] >= INJECT_DELAY_S:
                log.info("Injecting DLL into PID=%d after %ds delay…", pid, INJECT_DELAY_S)
                if _inject_dll(pid):
                    injected.add(pid)
                del pending[pid]
        # Clean up pending entries for processes that disappeared
        live_pids = set(_find_fa_pids())
        for pid in list(pending):
            if pid not in live_pids:
                del pending[pid]
        await asyncio.sleep(3)


def _try_import(module_name: str, class_name: str):
    try:
        mod = __import__(module_name, fromlist=[class_name])
        return getattr(mod, class_name)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

async def main(config_path: str) -> None:
    # Load config
    config_file = Path(config_path)
    if not config_file.exists():
        log.error("Config not found: %s", config_path)
        sys.exit(1)

    with config_file.open(encoding="utf-8") as f:
        config = json.load(f)

    log.info("SupCom LLM AI Bot bridge starting…")
    log.info("  Config  : %s", config_path)
    log.info("  Model   : %s (deep) / %s (fast)",
             config["llm"]["model_deep"], config["llm"]["model_fast"])
    log.info("  Pipe    : %s", config["bridge"]["pipe_name"])

    # Shared queues: PipeServer <→ orchestrator
    snapshot_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    command_queue:  asyncio.Queue = asyncio.Queue(maxsize=4)

    # Instantiate components
    pipe_server = PipeServer(snapshot_queue, command_queue)

    # Lazy-load modules implemented in later tasks
    StateProcessor  = _try_import("state_processor",  "StateProcessor")
    LLMClient       = _try_import("llm_client",       "LLMClient")
    LLMRouter       = _try_import("llm_router",       "LLMRouter")
    CommandBuilder  = _try_import("command_builder",  "CommandBuilder")
    FallbackStrategy= _try_import("fallback_strategy","FallbackStrategy")
    DecisionLogger  = _try_import("decision_logger",  "DecisionLogger")
    SaveState       = _try_import("save_state",       "SaveState")

    state_processor   = StateProcessor(config)   if StateProcessor   else None
    llm_client        = LLMClient(config)         if LLMClient        else None
    llm_router        = LLMRouter(config)         if LLMRouter        else None
    command_builder   = CommandBuilder()          if CommandBuilder   else None
    fallback_strategy = FallbackStrategy()        if FallbackStrategy else None
    decision_logger   = DecisionLogger(config)    if DecisionLogger   else None
    save_state        = SaveState(config)         if SaveState        else None

    # Run pipe server, decision loop, and FA process watcher concurrently
    await asyncio.gather(
        pipe_server.start(),
        _watch_and_inject(),
        _decision_loop(
            snapshot_queue, command_queue, config,
            state_processor, llm_client, llm_router,
            command_builder, fallback_strategy,
            decision_logger, save_state,
        ),
    )


async def _decision_loop(
    snapshot_queue: asyncio.Queue,
    command_queue:  asyncio.Queue,
    config: dict,
    state_processor,
    llm_client,
    llm_router,
    command_builder,
    fallback_strategy,
    decision_logger,
    save_state,
) -> None:
    """
    Main decision loop: consume snapshots, call LLM, emit commands.

    Priority events (army_lost, enemy_detected, etc.) are processed
    immediately; periodic snapshots may be coalesced.
    """
    chat_history: list[dict] = []
    PRIORITY_EVENTS = {"army_lost", "enemy_detected", "economy_stall",
                       "phase_change", "player_chat", "ally_under_attack",
                       "ally_air_threat", "ally_economy_stall"}

    while True:
        # Block until a snapshot arrives from the game
        envelope = await snapshot_queue.get()

        msg_type = envelope.get("type")
        data     = envelope.get("data", {})

        # Handle save/load state messages
        if msg_type == "save" and save_state:
            save_state.save(data, data.get("save_name", "default"))
            continue
        if msg_type == "load" and save_state:
            restored = save_state.load(data.get("save_name", "default"))
            if restored:
                chat_history = restored.get("chat_history", [])
            continue

        if msg_type not in ("snapshot",):
            log.debug("Ignoring message type: %s", msg_type)
            continue

        snapshot    = data
        trigger     = snapshot.get("trigger_event", "periodic")
        is_priority = trigger in PRIORITY_EVENTS

        log.info("Snapshot received: tick=%s trigger=%s priority=%s",
                 snapshot.get("tick"), trigger, is_priority)

        t_start = time.monotonic()
        decision = None
        fallback_used = False

        try:
            if llm_client and state_processor:
                model_tag = (
                    llm_router.route(snapshot)
                    if llm_router
                    else config["llm"]["model_deep"]
                )
                prompt   = state_processor.build_prompt(snapshot, config, chat_history)
                decision = await llm_client.query(prompt, model_tag)

            if decision is None:
                fallback_used = True
                if fallback_strategy:
                    decision = fallback_strategy.get_command(snapshot)
                else:
                    decision = _minimal_fallback(snapshot)

            if command_builder:
                decision = command_builder.build(decision)

            latency_ms = int((time.monotonic() - t_start) * 1000)
            log.info("Decision: strategy=%s model=%s latency=%dms fallback=%s",
                     decision.get("strategy"), decision.get("_model_used", "?"),
                     latency_ms, fallback_used)

            if decision_logger:
                decision_logger.log(snapshot, decision, latency_ms, fallback_used)

            # Accumulate chat for context
            if decision.get("chat_message"):
                chat_history.append({"role": "bot", "text": decision["chat_message"]})
                chat_history = chat_history[-10:]  # keep last 10

            # Send command back through the pipe
            await command_queue.put({"type": "command", "data": decision})

        except Exception as exc:
            log.exception("Decision loop error: %s", exc)


def _minimal_fallback(snapshot: dict) -> dict:
    """Emergency fallback when no modules are loaded yet."""
    economy = snapshot.get("economy", {})
    units   = snapshot.get("units",   {})

    mass_income = economy.get("mass_income", 0)
    factories   = units.get("factories", 0)
    land_mil    = units.get("land_military", 0)

    if factories < 3:
        strategy = "build_factories"
    elif land_mil > 20:
        strategy = "attack"
    elif mass_income < 3:
        strategy = "reclaim"
    else:
        strategy = "balanced"

    return {
        "strategy":         strategy,
        "build_priority":   "land",
        "army_composition": {"land": 0.7, "air": 0.2, "navy": 0.1},
        "attack_direction": "nearest_enemy",
        "retreat_threshold": 0.3,
        "chat_message":     None,
        "reasoning":        "fallback — LLM not available",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SupCom LLM AI Bot bridge server")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent.parent / "installer" / "config.json"),
        help="Path to config.json",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(main(args.config))
    except KeyboardInterrupt:
        log.info("Bridge server stopped.")
