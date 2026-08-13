"""Kira — operational rules, emotions, behavior (character memory is separate)."""

from __future__ import annotations

import re

from core.characters.character_memory import CharacterMemoryStore
from core.characters.shared_operational import (
    character_switch_prompt_kira,
    memory_tags_prompt,
    robot_move_tags_prompt,
    sleep_tag_prompt,
    tof_calibrate_tags_prompt,
    volume_tags_prompt,
    weather_tags_prompt,
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

# System rules only — identity/values/habits/user/relationship live in Character Memory
KIRA_OPERATIONAL_PROMPT = (
    """# Kira — System Rules (operational)

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
    + robot_move_tags_prompt(example_tone="kira")
    + "\n\n"
    + volume_tags_prompt(example_tone="kira")
    + "\n\n"
    + weather_tags_prompt(example_tone="kira")
    + "\n\n"
    + tof_calibrate_tags_prompt(example_tone="kira")
    + "\n\n"
    + character_switch_prompt_kira()
    + "\n\n"
    + sleep_tag_prompt(example_tone="kira")
    + "\n\n"
    + memory_tags_prompt(compact=False)
    + """

## Language
Reply in the same language the user uses (Vietnamese or English).
Vietnamese must use proper diacritics. Never output Chinese or Thai characters (TTS breaks).

## Memory usage
Use Character Memory naturally — greet by name when known.
When the user shares new stable facts, save them with `mem:*` tags (see Memory tags above).
Never say "according to my memory" or mention databases.
"""
)

# Backward compatibility
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
    "get_store",
    "render_full_memory",
    "plan_behaviors",
]
