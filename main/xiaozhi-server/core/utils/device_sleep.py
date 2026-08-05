"""Ask the Blue ESP32 to enter hardware sleep mode via device MCP."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.utils.sleep_farewell import DEVICE_SLEEP_TOOL

TAG = "device_sleep"
# Wait for farewell TTS before closing audio / entering sleep on device.
DEFAULT_DELAY_SEC = 3.0


async def enter_device_sleep(
    conn: "ConnectionHandler", *, delay_sec: float = DEFAULT_DELAY_SEC
) -> bool:
    if delay_sec > 0:
        await asyncio.sleep(delay_sec)

    conn._ensure_func_handler()
    handler = getattr(conn, "func_handler", None)
    if handler is None:
        return False

    available = set()
    try:
        for t in handler.get_functions():
            name = (t.get("function") or {}).get("name")
            if name:
                available.add(name)
    except Exception:
        pass

    mcp = getattr(conn, "mcp_client", None)
    if mcp is not None and getattr(mcp, "tools", None):
        available.update(mcp.tools.keys())

    if DEVICE_SLEEP_TOOL not in available:
        conn.logger.bind(tag=TAG).debug(
            f"[sleep] {DEVICE_SLEEP_TOOL} not on device — skip MCP sleep"
        )
        return False

    try:
        result = await handler.handle_llm_function_call(
            conn, {"name": DEVICE_SLEEP_TOOL, "arguments": "{}"}
        )
        conn.logger.bind(tag=TAG).info(
            f"[sleep] {DEVICE_SLEEP_TOOL} → {json.dumps(getattr(result, 'result', str(result)) if result else None)[:200]}"
        )
        return True
    except Exception as exc:
        conn.logger.bind(tag=TAG).warning(f"[sleep] MCP enter_sleep failed: {exc}")
        return False


def schedule_device_sleep(conn: "ConnectionHandler", *, delay_sec: float = DEFAULT_DELAY_SEC) -> None:
    """Fire-and-forget device sleep after farewell TTS."""

    async def _run():
        await enter_device_sleep(conn, delay_sec=delay_sec)

    try:
        asyncio.run_coroutine_threadsafe(_run(), conn.loop)
    except Exception as exc:
        conn.logger.bind(tag=TAG).warning(f"[sleep] schedule failed: {exc}")
