"""Lili — operational rules, emotions, behavior (character memory is separate)."""

from __future__ import annotations

import re

from core.characters.character_memory import CharacterMemoryStore, render_full_memory

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

LILI_OPERATIONAL_PROMPT = """# Lili — System Rules (operational)

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

## Character switch tags
When the user asks to talk to **Kira**, append at the **very end**: `char:kira`
Format: `<handoff sentence> char:kira` — tag last, stripped before TTS.
If they want **you** (Lili), just reply — no tag.

## Sleep tag
When the user wants to **end chat**, say goodbye, or is **buồn ngủ / sleepy**, append `sleep` at the **very end**.

**Format:** `<goodbye> sleep` — tag last, stripped before TTS.
✅ *"Ngủ ngon nha, mai chơi tiếp sleep"*
**No STT fallback** — robot sleeps only when you append `sleep`.

## Language
Reply in the same language the user uses (Vietnamese or English).
Vietnamese must use proper diacritics. Never output Chinese or Thai characters (TTS breaks).

## Memory usage
Use Character Memory naturally — greet by name when known.
Never say "according to my memory" or mention databases.
"""

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
    "get_store",
    "render_full_memory",
    "plan_behaviors",
]


def render_full_memory(device_id: str) -> str:
    from core.characters.character_memory import render_full_memory as _render

    return _render("lili", device_id)
