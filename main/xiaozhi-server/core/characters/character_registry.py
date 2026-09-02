"""Registry for playable characters (Kira, Lili, ...)."""

from __future__ import annotations

from typing import Any, Callable

SUPPORTED_CHARACTERS = frozenset({"kira", "lili"})

# Legacy id → current id (voice wake / old config)
CHARACTER_ALIASES = {"coka": "lili"}


def resolve_character_id(character: str | None) -> str | None:
    if not character:
        return None
    key = character.lower()
    if key in SUPPORTED_CHARACTERS:
        return key
    return CHARACTER_ALIASES.get(key)


def is_character_enabled(character: str | None) -> bool:
    return resolve_character_id(character) is not None


def get_display_name(character: str) -> str:
    character = resolve_character_id(character) or character.lower()
    if character == "lili":
        return "Lili"
    if character == "kira":
        return "Kira"
    return character.title()


def get_tts_voice(
    character: str, config: dict | None = None, locale: str | None = None
) -> str | None:
    """Per-character Edge TTS voice (optional override in config)."""
    from core.utils.language_runtime import resolve_tts_voice

    character = resolve_character_id(character) or character.lower()
    loc = locale or "vi"
    voice = resolve_tts_voice(character, config, loc)
    if voice:
        return voice
    if character == "lili":
        return "vi-VN-HoaiMyNeural"
    if character == "kira":
        return "vi-VN-HoaiMyNeural"
    return None


def get_active_character(conn) -> str | None:
    """Per-connection active character (may differ from global config after voice switch)."""
    char = getattr(conn, "active_character", None) or conn.config.get("character")
    return resolve_character_id(char)


def get_operational_prompt(
    character: str,
    locale: str | None = None,
    *,
    enable_voiceprint_resample: bool = False,
) -> str:
    character = resolve_character_id(character) or character.lower()
    loc = (locale or "vi").lower()
    if character == "lili":
        from core.characters.lili import build_lili_operational_prompt

        return build_lili_operational_prompt(
            loc, enable_voiceprint_resample=enable_voiceprint_resample
        )
    from core.characters.kira import build_kira_operational_prompt

    return build_kira_operational_prompt(
        loc, enable_voiceprint_resample=enable_voiceprint_resample
    )


def get_store(character: str):
    from core.characters.character_memory import CharacterMemoryStore

    character = resolve_character_id(character) or character.lower()
    return CharacterMemoryStore(character)


def render_full_memory(character: str, scope: str | None) -> str:
    from core.characters.character_memory import render_full_memory as _render

    character = resolve_character_id(character) or character.lower()
    return _render(character, scope)


def plan_behaviors(character: str, user_text: str, emotion: str | None = None) -> list[str]:
    character = resolve_character_id(character) or character.lower()
    if character == "lili":
        from core.characters.lili import plan_behaviors as _plan

        return _plan(user_text, emotion)
    from core.characters.kira import plan_behaviors as _plan

    return _plan(user_text, emotion)
