"""Kira memory — backward-compatible wrapper."""

from core.characters.character_memory import (
    CharacterMemoryStore,
    render_full_memory as _render_full_memory,
)


def get_store() -> CharacterMemoryStore:
    return CharacterMemoryStore("kira")


def render_full_memory(scope: str | None) -> str:
    return _render_full_memory("kira", scope)
