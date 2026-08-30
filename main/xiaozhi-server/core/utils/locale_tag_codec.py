"""Locale control tag for per-reply TTS routing (vi → VieNeu, en → Kokoro).

The LLM decides the language of its reply and marks it with a tag at the START
of the reply:

    [locale=en] Hello! How are you?
    [locale=vi] Dạ, mình khỏe lắm.

Server parses the tag → switches the per-connection locale (and thus the TTS
provider/voice) → strips the tag before speech synthesis.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

# Tag shape: [locale=en] / [locale:en] / [locale = en] (whitespace tolerant).
_LOCALE_TAG_RE = re.compile(
    r"\[\s*locale\s*[:=]\s*(vi|en|vietnamese|english)\s*\]", re.IGNORECASE
)
# Strip variant also consumes ONE trailing whitespace so "[locale=en] 🙂 Hello"
# becomes "🙂 Hello" (no leading blank before the emoji).
_LOCALE_TAG_STRIP_RE = re.compile(
    r"\[\s*locale\s*[:=]\s*(?:vi|en|vietnamese|english)\s*\]\s*", re.IGNORECASE
)

# Partial tag left over from streaming (held until the closing bracket arrives).
# Locale tag is a PREFIX tag ([locale=en] at the start of the reply), so unlike
# suffix tags (mv:/mem:...) we must hold an INCOMPLETE opening bracket at the
# start of a chunk, and also a trailing incomplete one on flush.
_LOCALE_TAG_OPEN_RE = re.compile(r"\[\s*locale\s*[:=]\s*[a-z]*\s*$", re.IGNORECASE)
_LOCALE_TAG_LEAD_RE = re.compile(
    r"^\[\s*locale(?:\s*[:=]\s*[a-z]*)?\s*\]?", re.IGNORECASE
)

_SUPPORTED_LOCALES = frozenset({"vi", "en"})


def _normalize(locale: str) -> str:
    key = (locale or "").strip().lower()
    if key in ("vietnamese",):
        return "vi"
    if key in ("english",):
        return "en"
    return key if key in _SUPPORTED_LOCALES else ""


def extract_locale_from_assistant_text(text: str) -> str | None:
    """Return the locale a reply declares via its leading [locale=xx] tag."""
    if not text or not str(text).strip():
        return None
    match = _LOCALE_TAG_RE.search(text)
    if not match:
        return None
    return _normalize(match.group(1))


def strip_locale_tags(text: str, *, trim_edges: bool = True) -> str:
    """Remove any [locale=xx] tag(s) before TTS."""
    if not text:
        return ""
    cleaned = _LOCALE_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def hold_incomplete_locale_suffix(text: str) -> tuple[str, str]:
    """Hold a partial [locale... tag from a stream chunk (prefix or suffix).

    Returns (work_text, hold). The hold is replayed when the tag completes so
    an incomplete opening bracket never reaches the TTS.
    """
    if not text:
        return text, ""
    # Trailing incomplete tag: [locale=en (no closing bracket yet).
    match = _LOCALE_TAG_OPEN_RE.search(text)
    if match:
        return text[: match.start()], text[match.start():]
    # Leading incomplete tag: "[locale" at the very start with no closing "]".
    lead = _LOCALE_TAG_LEAD_RE.match(text)
    if lead:
        held = lead.group(0)
        if not held.rstrip().endswith("]"):
            return text[len(held):], held
    return text, ""


def apply_locale_tag_from_assistant_text(
    conn: "ConnectionHandler", text: str, *, label: str = "locale_tag"
) -> bool:
    """If the reply declares [locale=xx], apply it to the connection.

    Returns True when a tag was found and applied.
    """
    if not conn or not text:
        return False
    locale = extract_locale_from_assistant_text(text)
    if not locale:
        return False
    from core.utils.language_runtime import apply_locale_to_connection

    apply_locale_to_connection(conn, locale, reason=label)
    return True
