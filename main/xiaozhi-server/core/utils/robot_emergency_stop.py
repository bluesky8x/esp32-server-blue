"""Emergency robot stop — cancel queued mv:* steps and dispatch motor stop immediately."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

# Urgent stop only — not casual "dừng lại" in a multi-step request.
_EMERGENCY_STOP_RE = re.compile(
    r"(?:"
    r"dừng\s+lại\s+ngay|dung\s+lai\s+ngay|dừng\s+ngay|dung\s+ngay|"
    r"dừng\s+motor\s+ngay|stop\s+now|stop\s+immediately|emergency\s+stop|"
    r"stop\s+the\s+robot|dừng\s+robot\s+ngay"
    r")",
    re.IGNORECASE,
)

_ACK_VI = (
    "Dừng rồi nha!",
    "Okie, dừng ngay đây!",
    "Mình dừng motor rồi nha.",
)
_ACK_EN = (
    "Stopped!",
    "Ok, stopping now!",
    "Motors stopped.",
)


def user_requested_emergency_robot_stop(text: str) -> bool:
    if not text or not str(text).strip():
        return False
    return bool(_EMERGENCY_STOP_RE.search(str(text).strip()))


def emergency_stop_enabled(conn: "ConnectionHandler") -> bool:
    cfg = (conn.config or {}).get("robot_move") or {}
    if isinstance(cfg, dict) and "emergency_stop" in cfg:
        return bool(cfg.get("emergency_stop"))
    return bool((conn.config or {}).get("robot_move_emergency_stop", True))


def pick_emergency_stop_ack(conn: "ConnectionHandler") -> str:
    locale = str(getattr(conn, "active_locale", "vi") or "vi").lower()
    pool = _ACK_EN if locale == "en" else _ACK_VI
    idx = hash(getattr(conn, "session_id", "") or "") % len(pool)
    return pool[idx]
