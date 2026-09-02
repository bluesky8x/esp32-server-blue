"""Long-term memory tags in LLM replies (same pattern as mv:*, char:*, sleep).

Protocol: append ``mem:<category>:<value>`` at the **very end** of the assistant reply.
Tags are stripped before TTS; server saves to per-device character memory JSON.

Categories: like, name, nick, pref, topic, joke, birthday, lang

Example: ``Okie, mình nhớ bạn thích cà phê nha mem:like:coffee``
Multi: ``... mem:like:coffee mem:topic:AI``

**No STT fallback** — only LLM-appended tags write long-term memory.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

MEM_CATEGORIES = frozenset(
    {"like", "name", "nick", "pref", "topic", "joke", "birthday", "lang"}
)

# Value runs until next mem:/mv:/char:/sleep control tag or end of string.
_MEM_TAG_RE = re.compile(
    r"\bmem\s*:\s*(like|name|nick|pref|topic|joke|birthday|lang)\s*:\s*"
    r"(.+?)(?=\s+mem\s*:|\s+mv\s*:|\s+char\s*:|\s+sleep\s*$|\s*$)",
    re.IGNORECASE,
)
MEM_TAG_STRIP_RE = _MEM_TAG_RE

_INCOMPLETE_MEM_SUFFIX_RE = re.compile(
    r"(?:\s+mem(?:\s*:\s*[a-z]{0,8}(?:\s*:\s*[\w\sÀ-ỹ\-]{0,50})?)?)$",
    re.IGNORECASE,
)


def extract_mem_tags(text: str) -> list[tuple[str, str]]:
    """Return (category, value) pairs found in text."""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    for cat, val in _MEM_TAG_RE.findall(str(text)):
        category = str(cat).lower().strip()
        value = str(val).strip().strip("\"'")
        if category in MEM_CATEGORIES and value:
            out.append((category, value))
    return out


def has_mem_tag(text: str) -> bool:
    return bool(extract_mem_tags(text))


def strip_mem_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = MEM_TAG_STRIP_RE.sub("", str(text))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def hold_incomplete_mem_suffix(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    if has_mem_tag(text):
        return text, ""
    m = _INCOMPLETE_MEM_SUFFIX_RE.search(text)
    if m and m.group(0).strip():
        return text[: m.start()], text[m.start() :]
    return text, ""


def _speaker_can_write_memory(conn: "ConnectionHandler") -> bool:
    """Long-term memory may be written only by the admin (Mr Blue).

    This is part of the multi-user voice feature — gated behind
    ``voiceprint.enroll_enabled``. When voice recognition is not configured
    OR the feature flag is off, memory writes stay enabled (legacy behavior).
    """
    store = getattr(conn, "voice_user_store", None)
    if store is None:
        return True
    if not getattr(store, "enroll_enabled", False):
        return True
    return store.is_admin_speaker(getattr(conn, "current_speaker", None))


def apply_mem_tags_from_assistant_text(
    conn: "ConnectionHandler", text: str, *, label: str = ""
) -> bool:
    tags = extract_mem_tags(text)
    if not tags:
        return False

    # Admin-only: only Mr Blue may ask the robot to save/change memory.
    if not _speaker_can_write_memory(conn):
        logger = getattr(conn, "logger", None)
        if logger:
            logger.bind(tag="mem_tag").info(
                f"[mem] blocked — only admin may save memory "
                f"(speaker={getattr(conn, 'current_speaker', None)!r}, "
                f"from={label or 'assistant'})"
            )
        return False

    from core.characters.character_registry import get_active_character, get_store
    from core.characters.character_memory import resolve_memory_scope

    character = get_active_character(conn)
    if not character:
        return False
    scope = resolve_memory_scope(getattr(conn, "current_speaker", None))
    store = get_store(character)
    changed = store.apply_mem_tags(
        scope,
        tags,
        speaker_name=(getattr(conn, "current_speaker", None) or "").strip() or None,
    )
    if not changed:
        return False
    logger = getattr(conn, "logger", None)
    if logger:
        logger.bind(tag="mem_tag").info(
            f"[mem] saved {len(tags)} tag(s) (from={label or 'assistant'}): "
            + ", ".join(f"{c}={v[:40]}" for c, v in tags)
        )
    refresh = getattr(conn, "_refresh_character_memory_prompt", None)
    if callable(refresh):
        refresh("")
    return True
