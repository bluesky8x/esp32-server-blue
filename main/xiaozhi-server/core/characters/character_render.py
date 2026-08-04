"""Render rich character identity YAML into Character Memory text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_identity_cache: dict[str, dict[str, Any]] = {}


def identity_path(character_id: str) -> Path:
    return Path(__file__).with_name(f"{character_id}_identity.yaml")


def load_identity(character_id: str) -> dict[str, Any]:
    if character_id in _identity_cache:
        return _identity_cache[character_id]
    path = identity_path(character_id)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _identity_cache[character_id] = data
    return data


def _lines_section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    out = [f"### {title}"]
    for item in items:
        out.append(f"- {item}")
    out.append("")
    return out


def _lines_dict_section(title: str, mapping: dict[str, Any]) -> list[str]:
    if not mapping:
        return []
    out = [f"### {title}"]
    for key, val in mapping.items():
        if isinstance(val, list):
            out.append(f"- {key}: {', '.join(str(v) for v in val)}")
        else:
            out.append(f"- {key}: {val}")
    out.append("")
    return out


def render_static_memory(character_id: str) -> str:
    data = load_identity(character_id)
    ident = data.get("identity") or {}
    display = ident.get("name") or character_id.title()

    lines = [
        f"## Character Memory — {display} (always true)",
        "",
        "### Identity",
        f"Name: {ident.get('name', display)}",
    ]
    if ident.get("nickname"):
        lines.append(f"Nickname: {ident.get('nickname')}")
    lines.extend(
        [
            f"Age: {ident.get('age', '?')}",
            f"Gender: {ident.get('gender', '')}",
            f"Languages: {', '.join(ident.get('languages') or ['Vietnamese', 'English'])}",
            f"Occupation: {ident.get('occupation', '')}",
            f"Personality: {', '.join(ident.get('personality') or [])}",
            f"Speaking style: {', '.join(ident.get('speaking_style') or [])}",
            "",
        ]
    )

    lines.extend(_lines_section("Core Values", data.get("core_values") or []))

    conv = data.get("conversation") or {}
    if conv:
        lines.append("### Conversation Style")
        tone = conv.get("tone") or {}
        if tone:
            lines.append("Tone map: " + ", ".join(f"{k}={v}" for k, v in tone.items()))
        lines.extend(f"- {b}" for b in conv.get("behavior") or [])
        lines.append("")

    speech = data.get("speech") or {}
    if speech:
        lines.append("### Speech")
        for key in ("sentence_length", "punctuation", "contractions"):
            if speech.get(key):
                lines.append(f"- {key}: {speech[key]}")
        if speech.get("fillers"):
            lines.append(f"- Fillers (sparingly): {', '.join(speech['fillers'])}")
        reactions = speech.get("reactions") or {}
        for mood, phrases in reactions.items():
            if phrases:
                lines.append(f"- Reactions ({mood}): {', '.join(phrases)}")
        lines.append("")

    for block_key, title in (
        ("humor", "Humor"),
        ("child_traits", "Child Traits"),
        ("playful_teasing", "Playful Teasing"),
        ("empathy", "Emotional Intelligence"),
        ("habits", "Habits"),
        ("likes", "Likes"),
        ("dislikes", "Dislikes"),
        ("quirks", "Quirks"),
        ("curiosity", "Curiosity"),
        ("interaction", "Interaction Rules"),
        ("safety", "Safety"),
        ("response_goals", "Response Goals"),
    ):
        block = data.get(block_key)
        if block is None:
            continue
        if isinstance(block, list):
            lines.extend(_lines_section(title, block))
        elif isinstance(block, dict):
            lines.append(f"### {title}")
            for sub_key, sub_val in block.items():
                if isinstance(sub_val, list):
                    lines.append(f"**{sub_key}:**")
                    lines.extend(f"- {x}" for x in sub_val)
                elif isinstance(sub_val, dict):
                    lines.append(f"**{sub_key}:** " + ", ".join(f"{k}={v}" for k, v in sub_val.items()))
                elif sub_val is not None:
                    lines.append(f"- {sub_key}: {sub_val}")
            lines.append("")

    if data.get("backstory"):
        lines.extend(["### Backstory", str(data["backstory"]).strip(), ""])

    lines.extend(
        _lines_section(
            "Catchphrases (use sparingly, naturally)",
            data.get("catchphrases") or [],
        )
    )

    return "\n".join(lines).rstrip()
