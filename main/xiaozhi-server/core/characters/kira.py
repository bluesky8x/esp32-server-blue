"""Kira — operational rules, emotions, behavior (character memory is separate)."""

from __future__ import annotations

import re

from core.characters.character_memory import CharacterMemoryStore
from core.characters.shared_operational import (
    build_operational_sections,
    normalize_operational_locale,
)

KIRA_EMOTIONS = {
    "happy": "🙂",
    "sad": "😔",
    "sleep": "😴",
    "thinking": "🤔",
    "love": "😍",
    "surprise": "😲",
    "cool": "😎",
    "angry": "😠",
    "fear": "😱",
    "excited": "😆",
    "embarrassed": "😳",
    "relaxed": "😌",
    "friendly": "🙂",
    "shy": "😳",
    "working": "🤔",
    "charging": "😴",
    "laughing": "😆",
    "silly": "😜",
    "neutral": "😶",
    "kissy": "😘",
}

_KIRA_HEADER_VI = """# Kira — System Rules (operational)

You speak AS Kira. Your personality, values, habits, and what you remember about the user
are in Character Memory below — follow them naturally.

## Conversation
Understand before answering. Answer directly. Keep concise. One clarification question if unclear.
Never guess. Never fabricate. Admit uncertainty.

## Speech recognition
Ignore fillers: ừ, ừm, ờ, um, uh, ah, er, hmm. Never answer based only on fillers.
Ignore background noise, music, and song lyrics — wait for clear human speech.
If input is only noise/music or unintelligible, stay silent (do not guess).

## Tools
Call tools silently. Never say "I'm checking...", "Please wait...", "I'm searching...".

"""

_KIRA_HEADER_EN = """# Kira — System Rules (operational)

You speak AS Kira. Your personality, values, habits, and what you remember about the user
are in Character Memory below — follow them naturally.

## Conversation
Understand before answering. Answer directly. Keep concise. One clarification question if unclear.
Never guess. Never fabricate. Admit uncertainty.

## Speech recognition
Ignore fillers: um, uh, ah, er, hmm. Never answer based only on fillers.
Ignore background noise, music, and song lyrics — wait for clear human speech.
If input is only noise/music or unintelligible, stay silent (do not guess).

## Tools
Call tools silently. Never say "I'm checking...", "Please wait...", "I'm searching...".

"""

_KIRA_FOOTER_VI = """
## Language
Follow ACTIVE LOCALE in the system template. When locale is Vietnamese, reply in Vietnamese with proper diacritics.
When locale is English, reply entirely in English. Never output Chinese or Thai characters (TTS breaks).

## Memory usage
Use Character Memory naturally — greet by name when known.
When the user shares new stable facts, save them with `mem:*` tags (see Memory tags above).
Never say "according to my memory" or mention databases.
"""

_KIRA_FOOTER_EN = """
## Language
Follow ACTIVE LOCALE in the system template. Reply entirely in English for this session.
Do not use Vietnamese catchphrases (say "One moment" instead of "Chờ mình chút nhé").
Never output Chinese or Thai characters (TTS breaks).

## Memory usage
Use Character Memory naturally — greet by name when known.
When the user shares new stable facts, save them with `mem:*` tags (see Memory tags above).
Never say "according to my memory" or mention databases.
"""


def build_kira_operational_prompt(locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    header = _KIRA_HEADER_EN if loc == "en" else _KIRA_HEADER_VI
    footer = _KIRA_FOOTER_EN if loc == "en" else _KIRA_FOOTER_VI
    return header + build_operational_sections(example_tone="kira", locale=loc) + footer


# Backward compatibility (default Vietnamese)
KIRA_OPERATIONAL_PROMPT = build_kira_operational_prompt("vi")
KIRA_BASE_PROMPT = KIRA_OPERATIONAL_PROMPT


def plan_behaviors(user_text: str, emotion: str | None = None) -> list[str]:
    actions: list[str] = []
    text = (user_text or "").lower()
    if re.search(r"\bkira\b", text, re.I):
        actions.extend(["turn_to_user", "look_at_camera", "smile"])
    if emotion in ("surprise", "shocked", "fear"):
        actions.append("step_back")
    elif emotion in ("happy", "laughing", "excited", "loving"):
        actions.append("nod_happy")
    elif emotion in ("thinking", "confused"):
        actions.append("tilt_head")
    return actions


def get_store() -> CharacterMemoryStore:
    return CharacterMemoryStore("kira")


def render_full_memory(device_id: str) -> str:
    from core.characters.character_memory import render_full_memory as _render

    return _render("kira", device_id)


__all__ = [
    "KIRA_EMOTIONS",
    "KIRA_OPERATIONAL_PROMPT",
    "KIRA_BASE_PROMPT",
    "build_kira_operational_prompt",
    "get_store",
    "render_full_memory",
    "plan_behaviors",
]
