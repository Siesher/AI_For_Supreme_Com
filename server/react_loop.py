# ReAct loop engine — server/react_loop.py
#
# Orchestrates the observe → think → act → feedback cycle.
# Each decision cycle runs up to N iterations of LLM queries.
# Observation tools are resolved via pipe round-trips to Lua.
# Action tools are also executed via pipe and results fed back.

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from decision_memory import DecisionMemory, DecisionSummary
from tools import OBSERVATION_TOOL_NAMES, validate_and_filter, deduplicate

log = logging.getLogger(__name__)

_REQUEST_COUNTER = 0


def _next_request_id(prefix: str = "req") -> str:
    global _REQUEST_COUNTER
    _REQUEST_COUNTER += 1
    return f"{prefix}_{_REQUEST_COUNTER:04d}"


class ReactLoop:
    """Runs the ReAct loop for one decision cycle."""

    def __init__(
        self,
        llm_client,
        pipe_server,
        config: dict,
    ) -> None:
        self._llm_client = llm_client
        self._pipe_server = pipe_server

        bot_cfg = config.get("bot", {})
        self._max_iterations = bot_cfg.get("react_max_iterations", 3)
        self._cycle_timeout_s = bot_cfg.get("react_cycle_timeout_s", 30)
        self._obs_timeout_s = 5.0  # per-observation pipe timeout

    async def run_cycle(
        self,
        messages: list[dict[str, str]],
        model_tag: str,
        memory: Optional[DecisionMemory] = None,
        game_time_s: int = 0,
        trigger: str = "periodic",
    ) -> dict[str, Any]:
        """
        Run one complete ReAct cycle.

        Returns:
            {
                "tool_calls": [...final action tool calls...],
                "_model_used": str,
                "_latency_ms": int,
                "_iterations": [...iteration data for logging/memory...],
                "_cycle_summary": DecisionSummary | None,
            }
        """
        cycle_start = time.monotonic()
        cycle_id = memory.next_cycle_id() if memory else 0
        all_iterations: list[dict] = []
        final_tool_calls: list[dict] = []
        model_used = model_tag

        # Accumulate conversation messages across iterations
        conversation = list(messages)

        for iteration_num in range(1, self._max_iterations + 1):
            # Check cycle timeout
            elapsed = time.monotonic() - cycle_start
            if elapsed >= self._cycle_timeout_s:
                log.warning("ReAct cycle timeout at iteration %d (%.1fs)", iteration_num, elapsed)
                break

            iter_start = time.monotonic()

            # Query LLM
            result = await self._llm_client.query(conversation, model_tag)
            if result is None:
                log.warning("ReAct: LLM returned None at iteration %d", iteration_num)
                break

            model_used = result.get("_model_used", model_tag)
            tool_calls = result.get("tool_calls", [])
            iter_latency = int((time.monotonic() - iter_start) * 1000)

            if not tool_calls:
                log.info("ReAct: no tool calls at iteration %d — ending", iteration_num)
                break

            # Validate and deduplicate tool calls
            tool_calls = validate_and_filter(tool_calls)
            tool_calls = deduplicate(tool_calls)

            if not tool_calls:
                log.warning("ReAct: all tool calls invalid at iteration %d — ending", iteration_num)
                break

            # Check for noop — terminates immediately
            has_noop = any(tc["name"] == "noop" for tc in tool_calls)

            # Classify tools
            observation_calls = [tc for tc in tool_calls if tc["name"] in OBSERVATION_TOOL_NAMES]
            action_calls = [tc for tc in tool_calls if tc["name"] not in OBSERVATION_TOOL_NAMES and tc["name"] != "noop"]

            # Execute all tools and collect results
            tool_results: list[dict] = []

            # Execute observations in parallel via asyncio.gather
            if observation_calls:
                obs_coros = [
                    self._execute_observation(tc["name"], tc.get("args", {}))
                    for tc in observation_calls
                ]
                obs_results = await asyncio.gather(*obs_coros, return_exceptions=True)
                for tc, obs_result in zip(observation_calls, obs_results):
                    if isinstance(obs_result, Exception):
                        log.warning("Observation %s raised: %s", tc["name"], obs_result)
                        obs_result = {"success": False, "error": str(obs_result)}
                    tool_results.append({
                        "tool_name": tc["name"],
                        "is_observation": True,
                        "success": obs_result.get("success", False),
                        "data": obs_result.get("data", {}),
                        "error": obs_result.get("error"),
                    })

            # Execute actions sequentially (order matters for game state)
            for tc in action_calls:
                act_result = await self._execute_action(tc["name"], tc.get("args", {}))
                tool_results.append({
                    "tool_name": tc["name"],
                    "is_observation": False,
                    "success": act_result.get("success", False),
                    "data": act_result.get("data", {}),
                    "error": act_result.get("error"),
                })

            # Record iteration
            iteration_data = {
                "iteration_num": iteration_num,
                "tool_calls": [
                    {"name": tc["name"], "args": tc.get("args", {}),
                     "is_observation": tc["name"] in OBSERVATION_TOOL_NAMES}
                    for tc in tool_calls
                ],
                "tool_results": tool_results,
                "latency_ms": iter_latency,
            }
            all_iterations.append(iteration_data)
            final_tool_calls = action_calls

            log.info(
                "ReAct iter %d: obs=%d act=%d noop=%s latency=%dms",
                iteration_num, len(observation_calls), len(action_calls),
                has_noop, iter_latency,
            )

            # Terminate on noop
            if has_noop:
                break

            # Early exit: action calls without new observations means the
            # decision is made — no need for another LLM round (~3-5s saved)
            if action_calls and not observation_calls:
                log.info("ReAct: action-only at iter %d — early exit", iteration_num)
                break

            # If this is the last iteration, don't re-query
            if iteration_num >= self._max_iterations:
                break

            # Append assistant + tool results to conversation for next iteration
            # Build the assistant message with tool_calls (Ollama format)
            assistant_tool_calls = [
                {"function": {"name": tc["name"], "arguments": tc.get("args", {})}}
                for tc in tool_calls
            ]
            conversation.append({
                "role": "assistant",
                "content": "",
                "tool_calls": assistant_tool_calls,
            })

            # Append tool result messages
            for tr in tool_results:
                content = json.dumps(tr["data"], ensure_ascii=False) if tr["success"] else tr.get("error", "error")
                conversation.append({"role": "tool", "content": content})

        # Build cycle summary for memory
        cycle_summary = None
        if memory:
            cycle_summary = DecisionMemory.build_summary(
                cycle_id=cycle_id,
                game_time_s=game_time_s,
                trigger=trigger,
                iterations=all_iterations,
            )

        total_latency = int((time.monotonic() - cycle_start) * 1000)

        return {
            "tool_calls": final_tool_calls,
            "_model_used": model_used,
            "_latency_ms": total_latency,
            "_iterations": all_iterations,
            "_cycle_summary": cycle_summary,
        }

    async def _execute_observation(self, tool_name: str, args: dict) -> dict:
        """Send observation request to Lua via pipe, wait for result."""
        request_id = _next_request_id("obs")
        msg = {
            "type": "observation_request",
            "request_id": request_id,
            "tool": tool_name,
            "args": args,
        }

        response = await self._pipe_server.send_and_wait_response(msg, timeout=self._obs_timeout_s)
        if response is None:
            return {"success": False, "error": f"observation timeout: {tool_name}"}

        return {
            "success": response.get("success", False),
            "data": response.get("data", {}),
            "error": response.get("error"),
        }

    async def _execute_action(self, tool_name: str, args: dict) -> dict:
        """Send action to Lua via pipe, wait for result."""
        request_id = _next_request_id("act")
        msg = {
            "type": "action_execute",
            "request_id": request_id,
            "tool": tool_name,
            "args": args,
        }

        response = await self._pipe_server.send_and_wait_response(msg, timeout=self._obs_timeout_s)
        if response is None:
            return {"success": False, "error": f"action timeout: {tool_name}"}

        return {
            "success": response.get("success", False),
            "data": response.get("data", {}),
            "error": response.get("error"),
        }


class FileIPCReactLoop(ReactLoop):
    """ReactLoop variant that resolves observations from snapshot data.

    Used when DLL pipe is unavailable and file IPC is active.
    Observations are approximated from the snapshot; actions are queued
    and sent back to Lua via command file.
    """

    def __init__(self, llm_client, config: dict) -> None:
        super().__init__(llm_client, pipe_server=None, config=config)
        self._snapshot: dict = {}

    def set_snapshot(self, snapshot: dict) -> None:
        """Set the current snapshot for observation resolution."""
        self._snapshot = snapshot

    async def _execute_observation(self, tool_name: str, args: dict) -> dict:
        from file_ipc import resolve_observation
        return resolve_observation(tool_name, args, self._snapshot)

    async def _execute_action(self, tool_name: str, args: dict) -> dict:
        # Actions will be sent to Lua via command file — acknowledge here
        return {"success": True, "data": {"status": "queued"}}
