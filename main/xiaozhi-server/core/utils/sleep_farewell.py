"""Varied sleep farewells — template-based (fast, no LLM) with anti-repeat memory."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

_MAX_RECENT = 8

_KIRA_VI: tuple[str, ...] = (
    "Ngủ ngon nha! Hẹn gặp lại bạn sau 😴",
    "Tạm biệt! Mình nghỉ một chút đây.",
    "Chào bạn! Mình đi ngủ đây, chạm màn hình để gọi mình nhé.",
    "Bye bye! Kira ngủ đây, hẹn gặp lại nha.",
    "Được rồi, mình nghỉ ngơi đây. Chúc bạn một ngày vui!",
    "Tạm biệt bạn! Mình tắt mắt một lúc nhé 😴",
    "Okie, mình sleep mode đây. Gọi mình khi cần nha!",
    "Hẹn gặp lại! Mình nghỉ ngơi một chút thôi.",
)

_KIRA_EN: tuple[str, ...] = (
    "Goodnight! I'll rest now — tap the screen to wake me 😴",
    "Bye! Going to sleep mode. See you soon!",
    "Okay, nap time for Kira. Take care!",
    "Goodbye! I'll dim the screen and rest a bit.",
    "See you later! Touch me when you want to chat again.",
    "Night night! Kira is going to sleep now.",
    "Alright, rest mode on. Catch you later!",
    "Bye for now! I'll be here when you need me.",
)

_LILI_VI: tuple[str, ...] = (
    "Ngủ ngon nha! Lili cũng buồn ngủ rồi, tạm biệt 😴",
    "Ừm, mai chơi tiếp nha! Lili ngủ đây.",
    "Bye bye! Chạm màn hình gọi Lili nha.",
    "Tạm biệt! Lili nghỉ một chút thôi.",
    "Okie, ngủ ngon nha! Hẹn gặp lại.",
)

_LILI_EN: tuple[str, ...] = (
    "Bye! Lili is going to sleep now 😴",
    "Goodnight! Tap to wake me up.",
    "See you! Rest mode for a bit.",
)

_POOLS: dict[str, dict[str, tuple[str, ...]]] = {
    "kira": {"vi": _KIRA_VI, "en": _KIRA_EN},
    "lili": {"vi": _LILI_VI, "en": _LILI_EN},
}

DEVICE_SLEEP_TOOL = "self.power.enter_sleep"


def _recent(conn: "ConnectionHandler") -> list[str]:
    items = getattr(conn, "_recent_sleep_farewells", None)
    if not isinstance(items, list):
        items = []
        conn._recent_sleep_farewells = items
    return items


def pick_sleep_farewell(conn: "ConnectionHandler", character_id: str | None = None) -> str:
    locale = getattr(conn, "active_locale", None) or "vi"
    if locale not in ("vi", "en"):
        locale = "vi"
    char = (character_id or getattr(conn, "active_character_id", None) or "kira").lower()
    pool = _POOLS.get(char, _POOLS["kira"]).get(locale) or _KIRA_VI

    recent = set(_recent(conn))
    candidates = [g for g in pool if g not in recent]
    if not candidates:
        candidates = list(pool)
        _recent(conn).clear()

    choice = random.choice(candidates)
    _recent(conn).append(choice)
    if len(_recent(conn)) > _MAX_RECENT:
        del _recent(conn)[: len(_recent(conn)) - _MAX_RECENT]
    return choice


def speak_sleep_farewell(
    conn: "ConnectionHandler",
    *,
    character_id: str | None = None,
    text: str | None = None,
) -> str:
    """Play farewell TTS and mark session to close after audio."""
    from core.utils.language_runtime import update_locale_from_user_text
    from core.handle.intentHandler import speak_txt

    if text:
        update_locale_from_user_text(conn, text, reason="sleep")
    conn.client_abort = False
    conn.close_after_chat = True

    import uuid

    conn.sentence_id = str(uuid.uuid4().hex)
    farewell = pick_sleep_farewell(conn, character_id)
    logger = getattr(conn, "logger", None)
    if logger:
        logger.bind(tag="sleep_farewell").info(
            f"Sleep farewell (locale={getattr(conn, 'active_locale', 'vi')}): {farewell}"
        )
    speak_txt(conn, farewell)
    return farewell
