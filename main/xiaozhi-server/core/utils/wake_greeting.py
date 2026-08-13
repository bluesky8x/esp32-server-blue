"""Varied wake greetings — template-based (fast, no LLM) with anti-repeat memory."""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

_MAX_RECENT = 10
# Device sends listen/start first (touch → mic on); greet only after this settle time.
STARTUP_GREETING_DELAY_SEC = 0.6

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
    "Hi! Kira here. What's on your mind?",
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


def _greeting_tts_text(text: str) -> str:
    """One spoken phrase — strip punctuation so TTS does not split mid-greeting."""
    for ch in "!?;,":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _begin_greeting_tts(conn: "ConnectionHandler") -> None:
    """Standard tts start — only after device is already in listening (touch flow)."""
    conn.client_abort = False
    conn.client_is_speaking = True
    loop = getattr(conn, "loop", None)
    if loop is None:
        return
    from core.handle.sendAudioHandle import send_tts_message

    future = asyncio.run_coroutine_threadsafe(send_tts_message(conn, "start"), loop)
    try:
        future.result(timeout=3)
    except Exception as exc:
        logger = getattr(conn, "logger", None)
        if logger:
            logger.bind(tag="wake_greeting").warning(f"Greeting tts start failed: {exc}")


def speak_greeting_txt(conn: "ConnectionHandler", text: str) -> None:
    """Queue greeting as TTS chunks (split long text for local TTS timeout)."""
    from core.utils.dialogue import Message
    from core.utils.tts_chunk import split_text_for_tts
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

    spoken = _greeting_tts_text(text)
    cfg = getattr(conn, "config", {}) or {}
    max_chars = int(cfg.get("tts_chunk_max_chars", 160))
    max_words = int(cfg.get("tts_chunk_max_words", 35))
    chunks = split_text_for_tts(spoken, max_chars=max_chars, max_words=max_words)
    if not chunks:
        chunks = [spoken] if spoken else []

    conn.tts.store_tts_text(conn.sentence_id, text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
        )
    )
    for chunk in chunks:
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=chunk,
            )
        )
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )
    conn.dialogue.put(Message(role="assistant", content=text))


def speak_wake_greeting(
    conn: "ConnectionHandler", character_id: str, wake_text: str
) -> None:
    """Play a varied greeting via normal TTS (device listening first, then speak)."""
    conn._startup_greeting_sent = True
    from core.utils.language_runtime import update_locale_from_user_text

    update_locale_from_user_text(conn, wake_text or "", reason="wake")
    import uuid

    conn.sentence_id = str(uuid.uuid4().hex)
    text = pick_wake_greeting(conn, character_id)
    logger = getattr(conn, "logger", None)
    if logger:
        logger.bind(tag="wake_greeting").info(
            f"Wake greeting ({character_id}, locale={getattr(conn, 'active_locale', 'vi')}): {text}"
        )
    _begin_greeting_tts(conn)
    speak_greeting_txt(conn, text)


async def maybe_speak_startup_greeting(conn: "ConnectionHandler") -> None:
    """After first listen/start: wait for mic, then play startup hello."""
    if getattr(conn, "_startup_greeting_sent", False):
        return
    if not conn.config.get("enable_greeting", True):
        return
    nested = (conn.config.get("wake_greeting") or {}).get("startup")
    if nested is False:
        return

    logger = getattr(conn, "logger", None)
    start = time.time()
    while time.time() - start < 10:
        if conn.tts and conn.asr is not None and conn.vad is not None:
            break
        await asyncio.sleep(0.1)
    else:
        if logger:
            logger.bind(tag="wake_greeting").warning(
                "Startup greeting skipped — TTS/ASR/VAD not ready within 10s"
            )
        return

    if getattr(conn, "_startup_greeting_sent", False):
        return

    delay = STARTUP_GREETING_DELAY_SEC
    wg = conn.config.get("wake_greeting") or {}
    if isinstance(wg, dict) and wg.get("startup_delay_sec") is not None:
        try:
            delay = max(0.0, float(wg["startup_delay_sec"]))
        except (TypeError, ValueError):
            pass

    if delay > 0:
        if logger:
            logger.bind(tag="wake_greeting").debug(
                f"Startup greeting delay {delay:.1f}s (device listen settle)"
            )
        await asyncio.sleep(delay)

    if getattr(conn, "_startup_greeting_sent", False):
        return

    char_id = (
        getattr(conn, "active_character", None)
        or conn.config.get("character")
        or "kira"
    )
    if logger:
        logger.bind(tag="wake_greeting").info(
            f"Startup greeting ({char_id}, device={getattr(conn, 'device_id', '?')})"
        )
    if getattr(conn, "executor", None):
        conn.executor.submit(speak_wake_greeting, conn, char_id, "")
    else:
        speak_wake_greeting(conn, char_id, "")
