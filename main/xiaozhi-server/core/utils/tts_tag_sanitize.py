"""Strip device control tags before TTS — speech must never include vol:/wx:/mv:/etc."""

from __future__ import annotations

import re

# Trailing partial tags from LLM streaming (incomplete suffix held too late).
_TRAILING_PARTIAL_CONTROL_RE = re.compile(
    r"(?:"
    r"\s+vol\s*:?\s*\d{0,3}|"
    r"\s+wx\s*:.*|"
    r"\s+tof\s*:?\s*cal(?:\s*:?\s*\d{0,4})?|"
    r"\s+mv\s*:.*|"
    r"\s+mem\s*:.*|"
    r"\s+char\s*:?\s*\w*|"
    r"\s+sleep\s*"
    r")+$",
    re.IGNORECASE,
)
# Orphan @time (e.g. after partial wx strip) — covered by weather_tag_codec too.
_TRAILING_AT_PARAM_RE = re.compile(
    r"(?:\s+[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9]{0,24})?@[a-z0-9+-]+\s*$",
    re.IGNORECASE,
)
# Fast reject before running 7 strip passes / dispatch scans on plain LLM tokens.
_CONTROL_TAG_MARKER_RE = re.compile(
    r"(?:\b(?:vol|wx|tof|mv|mem|char)\s*:|\bsleep\b|@)",
    re.IGNORECASE,
)


def may_contain_control_tags(text: str) -> bool:
    """Cheap check — most stream chunks are plain speech with no device tags."""
    return bool(text and _CONTROL_TAG_MARKER_RE.search(text))


def strip_control_tags_for_tts(text: str, *, trim_edges: bool = True) -> str:
    """Remove operational tags from assistant text before speech synthesis."""
    if not text:
        return ""
    if not may_contain_control_tags(text):
        return text.strip() if trim_edges else text
    from core.utils.character_switch_codec import strip_char_tags
    from core.utils.memory_tag_codec import strip_mem_tags
    from core.utils.robot_move_codec import strip_move_tags
    from core.utils.sleep_tag_codec import strip_sleep_tag
    from core.utils.tof_tag_codec import strip_tof_tags
    from core.utils.volume_tag_codec import strip_vol_tags
    from core.utils.weather_tag_codec import strip_wx_tags

    cleaned = text
    for strip_fn in (
        strip_move_tags,
        strip_vol_tags,
        strip_wx_tags,
        strip_tof_tags,
        strip_mem_tags,
        strip_char_tags,
        strip_sleep_tag,
    ):
        cleaned = strip_fn(cleaned, trim_edges=False)

    cleaned = _TRAILING_PARTIAL_CONTROL_RE.sub("", cleaned)
    cleaned = _TRAILING_AT_PARAM_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned
