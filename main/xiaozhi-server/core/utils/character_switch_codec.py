"""Character switch tags in LLM replies (same pattern as mv:* motor tags).

Protocol: ``char:<id>`` at the **end** of the assistant reply, e.g.
``Okie, Lili đây nha char:lili`` — stripped before TTS; server switches persona.

| Tag | Character |
| char:kira | Kira |
| char:lili | Lili (aliases: coka) |
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

CHAR_TAG_RE = re.compile(
    r"\bchar\s*:\s*(kira|lili|coka|lily)\b", re.IGNORECASE
)
CHAR_TAG_STRIP_RE = CHAR_TAG_RE

_INCOMPLETE_CHAR_SUFFIX_RE = re.compile(
    r"(?:\s+char(?:\s*:\s*[a-z]{0,4})?)$",
    re.IGNORECASE,
)

_ALIAS_TO_ID = {
    "kira": "kira",
    "lili": "lili",
    "lily": "lili",
    "coka": "lili",
}


def resolve_char_tag(raw: str | None) -> str | None:
    if not raw:
        return None
    return _ALIAS_TO_ID.get(str(raw).lower().strip())


def extract_char_switch(text: str) -> str | None:
    """Return target character id if a char: tag is present."""
    if not text:
        return None
    matches = CHAR_TAG_RE.findall(text)
    if not matches:
        return None
    return resolve_char_tag(matches[-1])


def strip_char_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = CHAR_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def split_char_switch_tag(
    text: str, *, trim_edges: bool = False
) -> tuple[str, str | None]:
    char_id = extract_char_switch(text)
    return strip_char_tags(text, trim_edges=trim_edges), char_id


def hold_incomplete_char_suffix(text: str) -> tuple[str, str]:
    """Split trailing partial ``char:`` for streaming (like mv: hold)."""
    if not text:
        return "", ""
    m = _INCOMPLETE_CHAR_SUFFIX_RE.search(text)
    if m and m.group(0).strip():
        return text[: m.start()], text[m.start() :]
    return text, ""


def apply_char_switch_from_assistant_text(
    conn: "ConnectionHandler", text: str, *, label: str = ""
) -> str | None:
    """If reply contains char:*, switch active character on this connection."""
    char_id = extract_char_switch(text)
    if not char_id:
        return None
    from core.characters.character_switch import apply_active_character

    switched = apply_active_character(conn, char_id)
    logger = getattr(conn, "logger", None)
    if logger:
        logger.bind(tag="char_switch").info(
            f"[char] switch → {char_id} (switched={switched}, from={label or 'assistant'})"
        )
    return char_id


CHARACTER_SWITCH_PROMPT = """## Character switch tags
When the user asks to talk to **another** character, append exactly one tag at the **very end** of your reply:

| Tag | Switch to |
| char:lili | Lili (Lily, Coka) |
| char:kira | Kira |

**Format:** `<natural handoff> char:lili` — tag always **last**; stripped before TTS.

✅ *"Okie, Lili đây nha char:lili"* (user wanted Lili)
✅ *"Sure! Kira's here char:kira"* (user wanted Kira)
❌ Refusing without trying when user clearly wants the other character
❌ Tag in the middle: *"char:lili mình đây"*

When switching **to another** character, handoff in the **same language as ACTIVE LOCALE** in system prompt (do not switch to English unless user asked).
**Character switch does not change locale** — only `char:*` switches persona.

If the user already talks to **you**, do **not** append a switch tag.
**No STT fallback:** the server never switches from raw user speech alone — **you must** append `char:*` to switch (same rule as `mv:*` for motors).
"""
