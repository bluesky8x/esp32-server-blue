"""Weather lookup tags (Intent nointent — same pattern as vol: / mv:)."""

from __future__ import annotations

import re

# Tag must be at end of assistant reply (same rule as vol:/mv: prompts).
WX_TAG_RE = re.compile(
    r"\bwx\s*:\s*(local|[A-Za-zÀ-ỹ0-9][A-Za-zÀ-ỹ0-9\s,.-]{0,48})?\s*$",
    re.IGNORECASE,
)
WX_TAG_STRIP_RE = re.compile(
    r"\bwx\s*:\s*(?:local|[A-Za-zÀ-ỹ0-9][A-Za-zÀ-ỹ0-9\s,.-]{0,48})?\b",
    re.IGNORECASE,
)


_BAD_WX_LOCATION_RE = re.compile(
    r"\b(is|are|was|wrong|bad|not|the|and|or|nha|nhé|nhe)\b",
    re.IGNORECASE,
)


def _normalize_wx_location(raw: str | None) -> str | None:
    if raw is None:
        return ""
    loc = raw.strip()
    if not loc or loc.lower() == "local":
        return ""
    if len(loc) > 40 or _BAD_WX_LOCATION_RE.search(loc):
        return None
    return loc


def extract_weather_location_from_assistant_text(text: str) -> str | None:
    """Return location string when assistant appended wx: tag at end. Empty str = local/default."""
    if not text:
        return None
    match = WX_TAG_RE.search(text.rstrip())
    if not match:
        return None
    return _normalize_wx_location(match.group(1))


def strip_wx_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = WX_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned
