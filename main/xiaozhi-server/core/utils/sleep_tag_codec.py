"""Sleep tag in LLM replies (same pattern as mv:* and char:*).

Protocol: append ``sleep`` at the **very end** of the assistant reply, e.g.
``Ngủ ngon nha, mai chơi tiếp sleep`` — stripped before TTS; robot enters sleep mode.

**No STT fallback** — only the tag triggers sleep (AI must reason and append it).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

# Trailing tag only — avoids matching English "sleep" mid-sentence when not at end.
SLEEP_TAG_RE = re.compile(r"(?:\s+)sleep\s*$", re.IGNORECASE)
SLEEP_TAG_STRIP_RE = SLEEP_TAG_RE

_INCOMPLETE_SLEEP_SUFFIX_RE = re.compile(
    r"(?:\s+s(?:l(?:e(?:e(?:p)?)?)?)?)$",
    re.IGNORECASE,
)


def has_sleep_tag(text: str) -> bool:
    if not text:
        return False
    return bool(SLEEP_TAG_RE.search(str(text).strip()))


def strip_sleep_tag(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = SLEEP_TAG_STRIP_RE.sub("", str(text))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def hold_incomplete_sleep_suffix(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    if has_sleep_tag(text):
        return text, ""
    m = _INCOMPLETE_SLEEP_SUFFIX_RE.search(text)
    if m and m.group(0).strip():
        return text[: m.start()], text[m.start() :]
    return text, ""


def trigger_sleep_mode(conn: "ConnectionHandler", *, label: str = "") -> None:
    """Enter sleep mode: close session after TTS + device MCP sleep."""
    from core.utils.device_sleep import schedule_device_sleep

    if not getattr(conn, "close_after_chat", False):
        conn.close_after_chat = True
    schedule_device_sleep(conn)
    logger = getattr(conn, "logger", None)
    if logger:
        logger.bind(tag="sleep_tag").info(
            f"[sleep] sleep mode triggered (from={label or 'assistant'})"
        )


def apply_sleep_tag_from_assistant_text(
    conn: "ConnectionHandler", text: str, *, label: str = ""
) -> bool:
    if not has_sleep_tag(text):
        return False
    trigger_sleep_mode(conn, label=label)
    return True
