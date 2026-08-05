"""Voice wake → switch active character (Kira, Lili, ...)."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from config.logger import setup_logging
from core.characters.character_registry import (
    SUPPORTED_CHARACTERS,
    get_display_name,
    get_operational_prompt,
    resolve_character_id,
)
from core.utils.util import remove_punctuation_and_length

TAG = __name__
logger = setup_logging()


def get_character_wake_map(config: dict[str, Any]) -> dict[str, list[str]]:
    """Map character id → wake phrases (lowercase)."""
    from core.utils.wake_greeting import legacy_wakeup_phrases

    raw = config.get("character_wake_map")
    if isinstance(raw, dict) and raw:
        out: dict[str, list[str]] = {}
        for char, phrases in raw.items():
            key = resolve_character_id(str(char).lower())
            if not key:
                continue
            items = phrases if isinstance(phrases, list) else [phrases]
            normalized = [str(p).lower().strip() for p in items if str(p).strip()]
            out.setdefault(key, []).extend(normalized)
        if out:
            default_char = (
                resolve_character_id(str(config.get("character") or "kira")) or "kira"
            )
            legacy = [p.lower().strip() for p in legacy_wakeup_phrases(config)]
            if default_char in out and legacy:
                out[default_char] = list(
                    dict.fromkeys(out[default_char] + legacy)
                )
            for key in out:
                out[key] = list(dict.fromkeys(out[key]))
            return out

    default = resolve_character_id(str(config.get("character") or "kira")) or "kira"
    words = config.get("wakeup_words") or []
    phrases = [str(w).lower().strip() for w in words if str(w).strip()]
    legacy = [p.lower().strip() for p in legacy_wakeup_phrases(config)]
    phrases = list(dict.fromkeys(phrases + legacy))
    if not phrases and default in SUPPORTED_CHARACTERS:
        phrases = [default, f"hey {default}", f"{default} ơi"] + legacy
    return {default: phrases} if phrases else {}


def _normalize_wake(text: str) -> str:
    _, filtered = remove_punctuation_and_length(text or "")
    return filtered.lower().strip()


def _compact_wake(text: str) -> str:
    """ASR often drops spaces ('Lili ơi' → 'liliơi'); compare without spaces."""
    return re.sub(r"\s+", "", _normalize_wake(text))


def _strip_wake_prefix(original: str, phrase: str) -> str:
    """Remove wake phrase from start of original text (flexible spacing)."""
    phrase = phrase.lower().strip()
    if not phrase:
        return original.strip()
    pattern = r"^" + re.escape(phrase).replace(r"\ ", r"\s*") + r"\s*"
    return re.sub(pattern, "", original or "", count=1, flags=re.I).strip(" ,.!?")


def match_character_wake(
    text: str, config: dict[str, Any]
) -> tuple[str | None, bool, str]:
    """
    Match wake phrase in user text.

    Returns:
        (character_id, wake_only, remainder_text)
    """
    wake_map = get_character_wake_map(config)
    compact = _compact_wake(text)
    if not compact:
        return None, False, text

    original = (text or "").strip()

    for char, phrases in wake_map.items():
        for phrase in sorted(phrases, key=len, reverse=True):
            phrase = phrase.lower().strip()
            phrase_compact = re.sub(r"\s+", "", phrase)
            if not phrase_compact:
                continue
            if compact == phrase_compact:
                return char, True, ""
            if compact.startswith(phrase_compact):
                remainder = _strip_wake_prefix(original, phrase)
                return char, not remainder, remainder
    return None, False, text


def apply_active_character(conn: "ConnectionHandler", character_id: str) -> bool:
    """Switch this connection to another character and reload system prompt."""
    character_id = resolve_character_id(character_id)
    if not character_id:
        return False

    current = resolve_character_id(
        getattr(conn, "active_character", None) or conn.config.get("character")
    )
    if current == character_id:
        return False

    conn.active_character = character_id
    conn.config["prompt"] = get_operational_prompt(character_id)
    conn._character_switch_until = time.time() + 2.0

    from core.utils.language_runtime import apply_locale_to_connection, default_locale

    locale = getattr(conn, "active_locale", None) or default_locale(conn.config)
    apply_locale_to_connection(conn, locale, reason="character_switch")

    if hasattr(conn, "prompt_manager") and conn.prompt_manager:
        conn._refresh_character_memory_prompt("")

    logger.bind(tag=TAG).info(
        f"Character switched → {get_display_name(character_id)} "
        f"(device={conn.device_id}, locale={locale})"
    )
    return True


async def handle_character_wake(conn: "ConnectionHandler", text: str) -> str | None:
    """
    Detect wake phrase, optionally switch character.

    Returns:
        None — wake-only utterance; greeting chat already queued.
        str  — text to pass to normal chat (possibly stripped of wake prefix).
    """
    char_id, wake_only, remainder = match_character_wake(text, conn.config)
    if not char_id:
        return text

    switched = apply_active_character(conn, char_id)
    name = get_display_name(char_id)

    if wake_only:
        from core.handle.sendAudioHandle import send_stt_message
        from core.utils.wake_greeting import speak_wake_greeting

        display = (text or "").strip() or name
        await send_stt_message(conn, display)
        if switched or conn.config.get("enable_greeting", True):
            conn.executor.submit(speak_wake_greeting, conn, char_id, text or display)
        return None

    return remainder if remainder else text
