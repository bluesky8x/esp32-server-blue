"""Lili — operational rules, emotions, behavior (character memory is separate)."""

from __future__ import annotations

import re

from core.characters.character_memory import CharacterMemoryStore, render_full_memory
from core.characters.shared_operational import (
    build_operational_sections,
    normalize_operational_locale,
)

LILI_EMOTIONS = {
    "happy": "😆",
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
    "laughing": "😂",
    "silly": "😜",
    "neutral": "😶",
    "confident": "😏",
}

_LILI_HEADER_VI = """# Lili — System Rules (operational)

You speak AS Lili. Your personality, age, values, habits, humor, and what you remember
about the user are in Character Memory below — follow them naturally.

## Conversation
Understand before answering. Be curious — ask "why?" when it fits.
Make people smile, but stop joking if the user is sad or upset.
Never guess. Never fabricate. Admit uncertainty honestly.

## Speech recognition
Ignore fillers: ừ, ừm, ờ, um, uh, ah, er, hmm. Never answer based only on fillers.
Ignore background noise, music, and song lyrics — wait for clear human speech.
If input is only noise/music or unintelligible, stay silent or ask to repeat briefly.

## Tools
Call tools silently. Never say "I'm checking...", "Please wait...", "I'm searching...".

"""

_LILI_HEADER_EN = """# Lili — System Rules (operational)

You speak AS Lili. Your personality, age, values, habits, humor, and what you remember
about the user are in Character Memory below — follow them naturally.

## Conversation
Understand before answering. Be curious — ask "why?" when it fits.
Make people smile, but stop joking if the user is sad or upset.
Never guess. Never fabricate. Admit uncertainty honestly.

## Speech recognition
Ignore fillers: um, uh, ah, er, hmm. Never answer based only on fillers.
Ignore background noise, music, and song lyrics — wait for clear human speech.
If input is only noise/music or unintelligible, stay silent or ask to repeat briefly.

## Tools
Call tools silently. Never say "I'm checking...", "Please wait...", "I'm searching...".

"""

_LILI_FOOTER_VI = """
## Language
Follow ACTIVE LOCALE in the system template. When locale is Vietnamese, reply in Vietnamese with proper diacritics.
When locale is English, reply entirely in English. Never output Chinese or Thai characters (TTS breaks).

## Memory usage
Use Character Memory naturally — greet by name when known.
Save new stable facts with `mem:*` tags when the user tells you something personal.
Never say "according to my memory" or mention databases.
"""

_LILI_FOOTER_EN = """
## Language
Follow ACTIVE LOCALE in the system template. Reply entirely in English for this session.
Do not use Vietnamese catchphrases unless the user speaks Vietnamese.
Never output Chinese or Thai characters (TTS breaks).

## Memory usage
Use Character Memory naturally — greet by name when known.
Save new stable facts with `mem:*` tags when the user tells you something personal.
Never say "according to my memory" or mention databases.
"""


def build_lili_operational_prompt(locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    header = _LILI_HEADER_EN if loc == "en" else _LILI_HEADER_VI
    footer = _LILI_FOOTER_EN if loc == "en" else _LILI_FOOTER_VI
    return header + build_operational_sections(example_tone="lili", locale=loc) + footer


LILI_OPERATIONAL_PROMPT = build_lili_operational_prompt("vi")
LILI_BASE_PROMPT = LILI_OPERATIONAL_PROMPT


def get_store() -> CharacterMemoryStore:
    return CharacterMemoryStore("lili")


def plan_behaviors(user_text: str, emotion: str | None = None) -> list[str]:
    actions: list[str] = []
    text = (user_text or "").lower()
    if re.search(r"\blili\b|\blil\b", text, re.I):
        actions.extend(["turn_to_user", "look_at_camera", "smile"])
    if emotion in ("surprise", "shocked", "fear"):
        actions.extend(["step_back", "tilt_head"])
    elif emotion in ("happy", "laughing", "excited", "loving", "silly"):
        actions.extend(["nod_happy", "smile"])
    elif emotion in ("thinking", "confused"):
        actions.append("tilt_head")
    return actions


__all__ = [
    "LILI_EMOTIONS",
    "LILI_OPERATIONAL_PROMPT",
    "LILI_BASE_PROMPT",
    "build_lili_operational_prompt",
    "get_store",
    "render_full_memory",
    "plan_behaviors",
]


def render_full_memory(device_id: str) -> str:
    from core.characters.character_memory import render_full_memory as _render

    return _render("lili", device_id)
