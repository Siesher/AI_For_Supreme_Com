# T014: Windows Named Pipe async server — server/pipe_server.py
#
# Creates the server side of \\.\pipe\supcom_llm_bridge.
# Runs inside the asyncio event loop using run_in_executor to prevent blocking.
# Protocol: 4-byte little-endian length prefix + UTF-8 JSON payload.

from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Optional

import win32file
import win32pipe
import pywintypes

log = logging.getLogger(__name__)

PIPE_NAME     = r"\\.\pipe\supcom_llm_bridge"
PIPE_BUF_SIZE = 65536          # in/out buffer size
RECONNECT_DELAY_S = 3.0        # seconds between reconnect attempts


class PipeServer:
    """Manages the Windows Named Pipe connection with the game DLL."""

    def __init__(
        self,
        snapshot_queue: asyncio.Queue,
        command_queue: asyncio.Queue,
    ) -> None:
        self._snapshot_queue = snapshot_queue   # PipeServer → bridge_server
        self._command_queue  = command_queue    # bridge_server → PipeServer
        self._connected      = False
        self._pipe_handle: Optional[object] = None
        # Pending request-response futures keyed by request_id
        self._pending_requests: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Entry point: persistent connect loop + read/write tasks."""
        while True:
            try:
                await self._connect_and_serve()
            except Exception as exc:
                log.warning("Pipe disconnected (%s). Reconnecting in %ss…",
                            exc, RECONNECT_DELAY_S)
                self._connected = False
                # Cancel any pending request-response futures
                for fut in self._pending_requests.values():
                    if not fut.done():
                        fut.cancel()
                self._pending_requests.clear()
            await asyncio.sleep(RECONNECT_DELAY_S)

    async def send_and_wait_response(
        self,
        msg: dict,
        timeout: float = 5.0,
    ) -> Optional[dict]:
        """
        Send a message to Lua and wait for a response with matching request_id.

        Used for observation requests and action executions during ReAct loop.
        Returns the response dict, or None on timeout/error.
        """
        if not self._connected or self._pipe_handle is None:
            log.warning("send_and_wait: pipe not connected")
            return None

        request_id = msg.get("request_id")
        if not request_id:
            log.error("send_and_wait: message has no request_id")
            return None

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_requests[request_id] = fut

        try:
            raw = json.dumps(msg, ensure_ascii=False)
            await loop.run_in_executor(None, self._write_message, self._pipe_handle, raw)
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            log.warning("send_and_wait: timeout for request_id=%s", request_id)
            return None
        except Exception as exc:
            log.error("send_and_wait: error for request_id=%s: %s", request_id, exc)
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_pipe(self):
        """Create a named pipe server instance (blocking, runs in executor)."""
        handle = win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            PIPE_BUF_SIZE,
            PIPE_BUF_SIZE,
            0,           # default timeout
            None,        # default security
        )
        if handle == win32file.INVALID_HANDLE_VALUE:
            raise OSError("CreateNamedPipe failed")
        return handle

    def _connect_client(self, handle) -> None:
        """Wait for a client to connect (blocking)."""
        win32pipe.ConnectNamedPipe(handle, None)

    def _read_message(self, handle) -> Optional[str]:
        """Read one length-prefixed message (blocking). Returns None on EOF."""
        try:
            _, len_bytes = win32file.ReadFile(handle, 4)
        except pywintypes.error:
            return None
        if len(len_bytes) < 4:
            return None
        (msg_len,) = struct.unpack("<I", len_bytes)
        if msg_len == 0 or msg_len > 1024 * 1024:
            return None
        _, payload = win32file.ReadFile(handle, msg_len)
        return payload.decode("utf-8")

    def _write_message(self, handle, msg: str) -> None:
        """Write one length-prefixed message (blocking)."""
        payload = msg.encode("utf-8")
        header  = struct.pack("<I", len(payload))
        win32file.WriteFile(handle, header + payload)

    async def _connect_and_serve(self) -> None:
        loop = asyncio.get_event_loop()

        handle = await loop.run_in_executor(None, self._create_pipe)
        log.info("Pipe server created. Waiting for game client…")
        await loop.run_in_executor(None, self._connect_client, handle)

        self._pipe_handle = handle
        self._connected   = True
        log.info("Game client connected on %s", PIPE_NAME)

        # Run reader and writer concurrently; either failing ends the session.
        try:
            await asyncio.gather(
                self._read_loop(handle, loop),
                self._write_loop(handle, loop),
            )
        finally:
            self._connected = False
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass

    async def _read_loop(self, handle, loop: asyncio.AbstractEventLoop) -> None:
        """Continuously read messages from the DLL.

        Routes observation_result / action_result to pending futures,
        everything else (snapshots, heartbeats) to snapshot_queue.
        """
        while True:
            msg = await loop.run_in_executor(None, self._read_message, handle)
            if msg is None:
                log.info("Game client disconnected (read EOF).")
                return
            try:
                data = json.loads(msg)
            except json.JSONDecodeError as exc:
                log.warning("Malformed JSON from game, discarding: %s", exc)
                continue

            msg_type = data.get("type", "")

            # Route request-response messages to pending futures
            if msg_type in ("observation_result", "action_result"):
                req_id = data.get("request_id")
                fut = self._pending_requests.get(req_id)
                if fut and not fut.done():
                    fut.set_result(data)
                    log.debug("Response routed: type=%s request_id=%s", msg_type, req_id)
                else:
                    log.warning("Unexpected response: type=%s request_id=%s (no pending future)",
                                msg_type, req_id)
                continue

            # Everything else goes to the snapshot queue
            await self._snapshot_queue.put(data)
            log.debug("Snapshot received: type=%s tick=%s",
                      data.get("type"), data.get("data", {}).get("tick"))

    async def _write_loop(self, handle, loop: asyncio.AbstractEventLoop) -> None:
        """Drain the command queue and write commands to the DLL."""
        while True:
            cmd_dict = await self._command_queue.get()
            msg      = json.dumps(cmd_dict, ensure_ascii=False)
            await loop.run_in_executor(None, self._write_message, handle, msg)
            log.debug("Command sent to game: strategy=%s",
                      cmd_dict.get("data", {}).get("strategy"))
