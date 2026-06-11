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
from engine_config import resolve_engine
from pipe_server import PipeServer

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports with graceful stubs for not-yet-implemented modules
# ---------------------------------------------------------------------------

FA_PROCESS_NAME = "ForgedAlliance.exe"
# 32-bit injector — deployed alongside the DLL in FAF's bin directory
INJECT_EXE = r"C:\ProgramData\FAForever\bin\inject.exe"
DLL_PATH = r"C:\ProgramData\FAForever\bin\llm_bridge.dll"


def _find_fa_pids() -> list[int]:
    """Return PIDs of any running ForgedAlliance.exe processes."""
    try:
        import psutil

        return [
            p.pid
            for p in psutil.process_iter(["name"])
            if p.info["name"] == FA_PROCESS_NAME
        ]
    except ImportError:
        # psutil not installed — fall back to tasklist
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {FA_PROCESS_NAME}", "/FO", "CSV"],
                text=True,
                timeout=5,
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
            capture_output=True,
            text=True,
            timeout=20,
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
                    FA_PROCESS_NAME,
                    pid,
                    INJECT_DELAY_S,
                )
            elif now - pending[pid] >= INJECT_DELAY_S:
                log.info(
                    "Injecting DLL into PID=%d after %ds delay…", pid, INJECT_DELAY_S
                )
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


async def _warmup_ollama(config: dict) -> None:
    """Warm up Ollama: load model, allocate KV cache, compile tool template.

    Uses /api/chat (not /api/generate) with the actual tool definitions
    so the full inference pipeline is primed before the first real query.
    """
    import httpx
    from tools import GAME_TOOLS

    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "http://localhost:11434")
    num_ctx = llm_cfg.get("num_ctx", 4096)
    num_batch = llm_cfg.get("num_batch", 512)
    keep_alive = llm_cfg.get("keep_alive", "30m")
    models = {llm_cfg.get("model_deep"), llm_cfg.get("model_fast")}

    for model_tag in models:
        if not model_tag:
            continue
        url = base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": model_tag,
            "messages": [
                {"role": "system", "content": "/no_think\nReady check."},
                {"role": "user", "content": "Status: OK. Respond with noop."},
            ],
            "tools": GAME_TOOLS,
            "stream": False,
            "think": False,
            "keep_alive": keep_alive,
            "options": {
                "num_predict": 16,
                "num_ctx": num_ctx,
                "num_batch": num_batch,
            },
        }
        log.info("Warming up model %s (num_ctx=%d) …", model_tag, num_ctx)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            elapsed = time.monotonic() - t0
            log.info("Model %s warm — %.1fs", model_tag, elapsed)
        except Exception as exc:
            log.warning(
                "Warmup failed for %s: %s (will retry on first real query)",
                model_tag,
                exc,
            )


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
            async with httpx.AsyncClient(
                timeout=120, headers={"Authorization": f"Bearer {api_key}"}
            ) as client:
                resp = await client.post(base_url + "/chat/completions", json=payload)
                resp.raise_for_status()
            log.info("Model %s warm — %.1fs", model_tag, time.monotonic() - t0)
        except Exception as exc:
            log.warning(
                "Warmup failed for %s: %s (will retry on first real query)",
                model_tag,
                exc,
            )


async def _warmup(config: dict, api_style: str) -> None:
    """Dispatch warmup by engine api_style."""
    if api_style == "ollama":
        await _warmup_ollama(config)
    elif api_style == "openai":
        await _warmup_openai(config)
    else:  # hf_turbo loads in-process during client construction
        log.info("Skipping warmup for api_style=%s (in-process load)", api_style)


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
    log.info(
        "  Model   : %s (deep) / %s (fast)",
        config["llm"]["model_deep"],
        config["llm"]["model_fast"],
    )
    log.info("  Pipe    : %s", config["bridge"]["pipe_name"])

    # Resolve which inference engine to use (KoboldCpp/Ollama/LM Studio/vLLM/…)
    resolved = resolve_engine(config)
    api_style = resolved["api_style"]
    log.info(
        "  Engine  : %s (api_style=%s, %s)",
        resolved["engine_name"],
        api_style,
        resolved["base_url"],
    )

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

    # Shared queues: PipeServer <→ orchestrator
    snapshot_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    command_queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    # Instantiate components
    pipe_server = PipeServer(snapshot_queue, command_queue)

    # Lazy-load modules implemented in later tasks
    StateProcessor = _try_import("state_processor", "StateProcessor")
    LLMClient = _try_import("llm_client", "LLMClient")
    LLMRouter = _try_import("llm_router", "LLMRouter")
    FallbackStrategy = _try_import("fallback_strategy", "FallbackStrategy")
    DecisionLogger = _try_import("decision_logger", "DecisionLogger")
    SaveState = _try_import("save_state", "SaveState")
    ReactLoopCls = _try_import("react_loop", "ReactLoop")
    DecisionMemoryCls = _try_import("decision_memory", "DecisionMemory")

    state_processor = StateProcessor(config) if StateProcessor else None

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
    llm_router = LLMRouter(config) if LLMRouter else None
    fallback_strategy = FallbackStrategy() if FallbackStrategy else None
    decision_logger = DecisionLogger(config) if DecisionLogger else None
    save_state = SaveState(config) if SaveState else None

    react_loop = (
        ReactLoopCls(llm_client, pipe_server, config)
        if ReactLoopCls and llm_client
        else None
    )

    # File IPC — fallback when DLL pipe is unavailable (loadlib blocked in sim)
    from file_ipc import FileIPCServer

    FileIPCReactLoopCls = _try_import("react_loop", "FileIPCReactLoop")
    file_ipc = FileIPCServer()
    file_ipc.cleanup()  # remove stale files from previous session
    file_react_loop = (
        FileIPCReactLoopCls(llm_client, config)
        if FileIPCReactLoopCls and llm_client
        else None
    )

    memory_size = config.get("bot", {}).get("decision_memory_size", 10)
    decision_memory = DecisionMemoryCls(memory_size) if DecisionMemoryCls else None

    # Run pipe server, file IPC poller, decision loop, and FA process watcher
    await asyncio.gather(
        pipe_server.start(),
        _watch_and_inject(),
        _file_ipc_poll(file_ipc, snapshot_queue),
        _decision_loop(
            snapshot_queue,
            command_queue,
            config,
            state_processor,
            llm_client,
            llm_router,
            fallback_strategy,
            decision_logger,
            save_state,
            react_loop,
            decision_memory,
            file_ipc=file_ipc,
            file_react_loop=file_react_loop,
        ),
    )


async def _file_ipc_poll(file_ipc, snapshot_queue: asyncio.Queue) -> None:
    """Poll game log for [LLM_SNAP] lines and feed them to the decision loop.

    Outbound: Sim → Sync → UI → LOG("[LLM_SNAP]json") → game log → here.
    """
    log.info("File IPC poller started — tailing game logs in %s", file_ipc.log_dir)
    while True:
        snapshot = file_ipc.read_snapshot()
        if snapshot:
            log.info(
                "File IPC: snapshot from game log (tick=%s trigger=%s)",
                snapshot.get("tick"),
                snapshot.get("trigger_event"),
            )
            await snapshot_queue.put(
                {
                    "type": "snapshot",
                    "data": snapshot,
                    "_source": "file",
                }
            )
        await asyncio.sleep(1)


async def _decision_loop(
    snapshot_queue: asyncio.Queue,
    command_queue: asyncio.Queue,
    config: dict,
    state_processor,
    llm_client,
    llm_router,
    fallback_strategy,
    decision_logger,
    save_state,
    react_loop,
    decision_memory,
    file_ipc=None,
    file_react_loop=None,
) -> None:
    """
    Main decision loop: consume snapshots, run ReAct cycle, emit commands.

    Priority events (army_lost, enemy_detected, etc.) are processed
    immediately; periodic snapshots may be coalesced.
    """
    chat_history: list[dict] = []
    prev_snapshot: dict | None = None
    PRIORITY_EVENTS = {
        "army_lost",
        "enemy_detected",
        "economy_stall",
        "phase_change",
        "player_chat",
        "ally_under_attack",
        "ally_air_threat",
        "ally_economy_stall",
    }

    while True:
        envelope = await snapshot_queue.get()

        msg_type = envelope.get("type")
        data = envelope.get("data", {})

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

        snapshot = data
        source = envelope.get("_source", "pipe")
        trigger = snapshot.get("trigger_event", "periodic")
        game_time_s = snapshot.get("game_time_s", 0)
        is_priority = trigger in PRIORITY_EVENTS

        log.info(
            "Snapshot received: tick=%s trigger=%s priority=%s source=%s",
            snapshot.get("tick"),
            trigger,
            is_priority,
            source,
        )

        t_start = time.monotonic()
        decision = None
        fallback_used = False

        try:
            # Score previous cycle now that we have a new snapshot
            if decision_memory and prev_snapshot:
                decision_memory.score_last_cycle(prev_snapshot, snapshot)

            # Choose react loop: file IPC resolves observations from snapshot,
            # pipe react loop sends observation queries to Lua in real-time
            active_react = react_loop
            if source == "file" and file_react_loop:
                file_react_loop.set_snapshot(snapshot)
                active_react = file_react_loop

            if state_processor and active_react:
                model_tag = (
                    llm_router.route(snapshot)
                    if llm_router
                    else config["llm"]["model_deep"]
                )
                messages = state_processor.build_messages(
                    snapshot, config, chat_history, decision_memory=decision_memory
                )
                decision = await active_react.run_cycle(
                    messages=messages,
                    model_tag=model_tag,
                    memory=decision_memory,
                    game_time_s=game_time_s,
                    trigger=trigger,
                )

                # Store cycle summary in memory
                cycle_summary = decision.pop("_cycle_summary", None)
                if cycle_summary and decision_memory:
                    decision_memory.add_cycle(cycle_summary)

            elif llm_client and state_processor:
                # Fallback: single-shot query (no ReAct loop)
                model_tag = config["llm"]["model_deep"]
                messages = state_processor.build_messages(
                    snapshot, config, chat_history
                )
                decision = await llm_client.query(messages, model_tag)

            if decision is None:
                fallback_used = True
                if fallback_strategy:
                    decision = _fallback_to_tools(
                        fallback_strategy.get_command(snapshot)
                    )
                else:
                    decision = _fallback_to_tools(_minimal_fallback(snapshot))

            latency_ms = int((time.monotonic() - t_start) * 1000)
            tool_names = [tc["name"] for tc in decision.get("tool_calls", [])]
            iterations = decision.get("_iterations", [])
            log.info(
                "Decision: tools=%s model=%s latency=%dms iters=%d fallback=%s",
                tool_names,
                decision.get("_model_used", "?"),
                latency_ms,
                len(iterations),
                fallback_used,
            )

            if decision_logger:
                decision_logger.log(snapshot, decision, latency_ms, fallback_used)

            # Extract chat messages for history
            for tc in decision.get("tool_calls", []):
                if tc["name"] == "chat":
                    msg_text = tc.get("args", {}).get("message", "")
                    if msg_text:
                        chat_history.append({"role": "bot", "text": msg_text})
                        chat_history = chat_history[-10:]

            # Also check iteration data for chat calls
            for it in iterations:
                for tc in it.get("tool_calls", []):
                    if tc.get("name") == "chat":
                        msg_text = tc.get("args", {}).get("message", "")
                        if msg_text:
                            chat_history.append({"role": "bot", "text": msg_text})
                            chat_history = chat_history[-10:]

            # Compute adaptive poll interval based on threat level
            decision["poll_interval_override"] = _adaptive_poll_interval(
                snapshot, config
            )

            # Route command back to game via the same channel the snapshot came from
            if source == "file" and file_ipc:
                # Strip internal fields that Lua doesn't need / can't parse
                cmd_for_lua = {
                    k: v for k, v in decision.items() if not k.startswith("_")
                }
                file_ipc.write_command(cmd_for_lua)
                log.info("File IPC: command written (%d tool calls)", len(tool_names))
            else:
                await command_queue.put({"type": "command", "data": decision})

            prev_snapshot = snapshot

        except Exception as exc:
            log.exception("Decision loop error: %s", exc)


def _adaptive_poll_interval(snapshot: dict, config: dict) -> int:
    """Compute next poll interval based on game situation.

    High threat → poll every 8s (react faster)
    Medium activity → default 20s
    Calm/idle → stretch to 30s (save GPU)
    """
    bot_cfg = config.get("bot", {})
    base_interval = bot_cfg.get("poll_interval_fast_s", 20)

    threats = snapshot.get("threats", {})
    near_base = threats.get("near_base", 0)
    enemy_distance = threats.get("nearest_enemy_distance", 9999)
    trigger = snapshot.get("trigger_event", "periodic")

    # Crisis: enemy at the gates
    if near_base > 20 or enemy_distance < 150:
        return max(8, base_interval // 3)

    # Elevated: some threat detected
    if near_base > 5 or enemy_distance < 400:
        return max(12, base_interval // 2)

    # Priority event just happened — check again soon
    if trigger not in ("periodic", "deep_periodic"):
        return max(10, base_interval // 2)

    # Calm: nothing happening, stretch interval
    units = snapshot.get("units", {})
    land_mil = units.get("land_military", 0)
    if land_mil > 30 and near_base == 0:
        return min(30, base_interval + 10)

    return base_interval


def _fallback_to_tools(old_decision: dict) -> dict:
    """Convert old-style flat decision dict to tool_calls format."""
    tool_calls = []
    strategy = old_decision.get("strategy", "balanced")

    if strategy in ("attack", "land_rush"):
        tool_calls.append(
            {"name": "attack", "args": {"unit_type": "land", "force_size": "medium"}}
        )
    elif strategy == "defend":
        tool_calls.append({"name": "defend", "args": {"radius": 80}})
    else:
        tool_calls.append(
            {
                "name": "set_strategy",
                "args": {"strategy": strategy, "reasoning": "fallback"},
            }
        )

    chat_msg = old_decision.get("chat_message")
    if chat_msg:
        tool_calls.append({"name": "chat", "args": {"message": chat_msg}})

    return {"tool_calls": tool_calls, "_model_used": "fallback", "_latency_ms": 0}


def _minimal_fallback(snapshot: dict) -> dict:
    """Emergency fallback when no modules are loaded yet."""
    economy = snapshot.get("economy", {})
    units = snapshot.get("units", {})

    mass_income = economy.get("mass_income", 0)
    factories = units.get("factories", 0)
    land_mil = units.get("land_military", 0)

    if factories < 3:
        strategy = "build_factories"
    elif land_mil > 20:
        strategy = "attack"
    elif mass_income < 3:
        strategy = "reclaim"
    else:
        strategy = "balanced"

    return {
        "strategy": strategy,
        "chat_message": None,
        "reasoning": "fallback — LLM not available",
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
