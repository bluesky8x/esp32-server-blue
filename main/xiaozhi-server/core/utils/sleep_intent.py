"""Detect when the user wants to end the conversation and put the robot to sleep."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

# Vietnamese + English farewell / sleep phrases (natural speech, not exact commands).
_SLEEP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(tạm biệt|tam biet|hẹn gặp lại|hen gap lai|chào nhé|chao nhe|chào bạn|chao ban)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(goodbye|good bye|bye bye|see you later|see ya|good night|g'night)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(ngủ đi|ngu di|đi ngủ|di ngu|ngủ thôi|ngu thoi|go to sleep|sleep now|time to sleep|go sleep)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(buồn ngủ|buon ngu|muốn ngủ|muon ngu|mệt (?:quá )?(?:ngủ|muốn ngủ)|"
        r"ngủ (?:quá|rồi|nha|nhé)|ngu qua|"
        r"sleepy|i'?m (?:so )?sleepy|feeling sleepy|tired and sleepy)",
        re.IGNORECASE,
    ),
    re.compile(
        r"thôi.*(?:ngủ|ngu|buồn ngủ|buon ngu|đi ngủ|di ngu)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(kết thúc cuộc trò chuyện|ket thuc cuoc tro chuyen|kết thúc hội thoại|"
        r"end (the )?conversation|stop talking|stop chatting|that's all for now)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(thôi (?:nhé|nha|đi|nói chuyện|noi chuyện|trò chuyện|tro chuyen)|"
        r"không nói nữa|khong noi nua|im đi|im di|"
        r"i'?m done|we'?re done|that'?s all)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(nghỉ đi|nghi di|nghỉ ngơi đi|rest now|take a nap|nap time)",
        re.IGNORECASE,
    ),
)

# Short exact commands (also merged from config exit_commands at runtime).
_SLEEP_EXACT_VI_EN: frozenset[str] = frozenset(
    {
        "退出",
        "关闭",
        "tạm biệt",
        "tam biet",
        "ngủ đi",
        "ngu di",
        "goodbye",
        "bye",
        "good night",
        "buồn ngủ",
        "buon ngu",
    }
)

_MOTOR_STOP_ONLY_RE = re.compile(
    r"^(?:dừng lại|dung lai|stop|stop now)$", re.IGNORECASE
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def user_requested_sleep(text: str, *, extra_exact: tuple[str, ...] | None = None) -> bool:
    """True when the user turn is asking to end chat / put the robot to sleep."""
    t = _normalize(text)
    if not t:
        return False
    if _MOTOR_STOP_ONLY_RE.match(t):
        return False

    lowered = t.lower()
    exact = set(_SLEEP_EXACT_VI_EN)
    if extra_exact:
        exact.update(e.lower() for e in extra_exact if e)
    if lowered in exact:
        return True

    for pattern in _SLEEP_PATTERNS:
        if pattern.search(t):
            return True
    return False


def sleep_intent_enabled(conn: "ConnectionHandler") -> bool:
    cfg = (conn.config or {}).get("sleep_intent") or {}
    return cfg.get("enable", True) is not False


def extra_sleep_exact_phrases(conn: "ConnectionHandler") -> tuple[str, ...]:
    cfg = (conn.config or {}).get("sleep_intent") or {}
    custom = cfg.get("exact_phrases")
    if isinstance(custom, list) and custom:
        return tuple(str(p).strip() for p in custom if str(p).strip())
    return ()


def detect_sleep_request(conn: "ConnectionHandler", text: str) -> bool:
    if not sleep_intent_enabled(conn):
        return False
    return user_requested_sleep(text, extra_exact=extra_sleep_exact_phrases(conn))
