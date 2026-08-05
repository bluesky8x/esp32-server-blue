"""Varied wake greetings — template-based (fast, no LLM) with anti-repeat memory."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

_MAX_RECENT = 10

# Kira — Vietnamese (character voice, warm, concise)
_KIRA_VI: tuple[str, ...] = (
    "Chào bạn! Hôm nay mình có thể giúp gì nha?",
    "Kira đây! Bạn muốn làm gì tiếp theo?",
    "Mình nghe nè! Bạn cần mình hỗ trợ gì không?",
    "Chào nha! Sẵn sàng trò chuyện rồi đây.",
    "Hi! Kira đây, bạn khỏe không?",
    "Chào bạn! Có điều gì thú vị muốn chia sẻ không?",
    "Mình ở đây nè! Bạn muốn hỏi gì cũng được.",
    "Chào! Hôm nay bạn muốn khám phá điều gì?",
    "Kira sẵn sàng! Bạn nói đi mình nghe.",
    "Chào bạn! Mình vui được nói chuyện với bạn.",
    "Ồ, bạn gọi mình à? Mình đây!",
    "Chào nha! Buổi này bạn có kế hoạch gì không?",
    "Mình nghe rồi! Bạn muốn trò chuyện hay chơi gì nè?",
    "Chào bạn! Có gì hay ho muốn kể mình nghe không?",
    "Kira đây! Mình có thể giúp bài tập, kể chuyện, hay điều khiển robot nha.",
)

_KIRA_EN: tuple[str, ...] = (
    "Hi! Kira here — what would you like to do?",
    "Hello! I'm ready when you are.",
    "Hey! Good to hear from you. What's up?",
    "Hi there! How can I help you today?",
    "Hello! Kira at your service.",
    "Hey! Want to chat, learn something, or move the robot?",
    "Hi! I'm here — go ahead, I'm listening.",
    "Hello! Nice to see you. What should we do first?",
    "Hey there! Ready for a quick chat?",
    "Hi! Kira here. What’s on your mind?",
    "Hello! I'm all ears — what do you need?",
    "Hey! Good to have you back. What's the plan?",
)

_LILI_VI: tuple[str, ...] = (
    "Chào bạn! Lili đây nè.",
    "Mình nghe nè! Bạn cần gì không?",
    "Chào nha! Lili sẵn sàng rồi.",
    "Hi! Lili đây, bạn khỏe không?",
    "Chào bạn! Hôm nay chơi gì nè?",
)

_LILI_EN: tuple[str, ...] = (
    "Hi! Lili here — what's up?",
    "Hello! Ready to chat.",
    "Hey! Lili at your service.",
    "Hi there! How can I help?",
)

_POOLS: dict[str, dict[str, tuple[str, ...]]] = {
    "kira": {"vi": _KIRA_VI, "en": _KIRA_EN},
    "lili": {"vi": _LILI_VI, "en": _LILI_EN},
}


def default_legacy_wakeup_phrases() -> tuple[str, ...]:
    """Firmware / xiaozhi default listen-detect text (not Kira-specific names)."""
    return (
        "嘿，你好呀",
        "嘿你好呀",
        "你好小智",
        "你好小志",
    )


def legacy_wakeup_phrases(config: dict | None) -> list[str]:
    if not config:
        return list(default_legacy_wakeup_phrases())
    custom = config.get("legacy_wakeup_phrases")
    if isinstance(custom, list) and custom:
        return [str(p).strip() for p in custom if str(p).strip()]
    nested = (config.get("wake_greeting") or {}).get("legacy_phrases")
    if isinstance(nested, list) and nested:
        return [str(p).strip() for p in nested if str(p).strip()]
    return list(default_legacy_wakeup_phrases())


def _recent(conn: "ConnectionHandler") -> list[str]:
    items = getattr(conn, "_recent_wake_greetings", None)
    if not isinstance(items, list):
        items = []
        conn._recent_wake_greetings = items
    return items


def pick_wake_greeting(conn: "ConnectionHandler", character_id: str) -> str:
    locale = getattr(conn, "active_locale", None) or "vi"
    if locale not in ("vi", "en"):
        locale = "vi"
    char = (character_id or "kira").lower()
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


def speak_wake_greeting(
    conn: "ConnectionHandler", character_id: str, wake_text: str
) -> None:
    """Play a varied greeting via TTS (no LLM — avoids timeout on wake)."""
    from core.utils.language_runtime import update_locale_from_user_text
    from core.handle.intentHandler import speak_txt

    update_locale_from_user_text(conn, wake_text or "", reason="wake")
    conn.client_abort = False
    import uuid

    conn.sentence_id = str(uuid.uuid4().hex)
    text = pick_wake_greeting(conn, character_id)
    logger = getattr(conn, "logger", None)
    if logger:
        logger.bind(tag="wake_greeting").info(
            f"Wake greeting ({character_id}, locale={getattr(conn, 'active_locale', 'vi')}): {text}"
        )
    speak_txt(conn, text)
