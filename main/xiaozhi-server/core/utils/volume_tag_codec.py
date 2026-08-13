"""Volume control tags for Blue robots (works with Intent nointent + mv-style prompts)."""

from __future__ import annotations

import re

VOL_TAG_RE = re.compile(r"\bvol\s*:\s*(\d{1,3})\b", re.IGNORECASE)
VOL_TAG_STRIP_RE = re.compile(r"\bvol\s*:\s*\d{1,3}\b", re.IGNORECASE)

_VOLUME_SET_RE = re.compile(
    r"(?:volume|âm lượng|am luong|loa|speaker)"
    r".{0,24}?"
    r"(\d{1,3})\s*(?:%|phần trăm|phan tram)?",
    re.IGNORECASE,
)
_VOLUME_SET_SUFFIX_RE = re.compile(
    r"\b(\d{1,3})\s*(?:%|phần trăm|phan tram)\b", re.IGNORECASE
)
_VOLUME_UP_RE = re.compile(
    r"(?:tăng|tang|to hơn|cho to|lớn hơn|lon hon|volume up|turn up|louder)",
    re.IGNORECASE,
)
_VOLUME_DOWN_RE = re.compile(
    r"(?:giảm|giam|nhỏ hơn|nho hon|volume down|turn down|quieter|im hơn|im hon)",
    re.IGNORECASE,
)
_VI_NUMBER_WORDS: dict[str, int] = {
    "một": 1,
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "bon": 4,
    "năm": 5,
    "nam": 5,
    "sáu": 6,
    "sau": 6,
    "bảy": 7,
    "bay": 7,
    "tám": 8,
    "tam": 8,
    "chín": 9,
    "chin": 9,
    "mười": 10,
    "muoi": 10,
}


def clamp_volume(value: int) -> int:
    return max(0, min(100, int(value)))


def extract_volume_from_assistant_text(text: str) -> int | None:
    if not text:
        return None
    match = VOL_TAG_RE.search(text)
    if not match:
        return None
    return clamp_volume(int(match.group(1)))


def strip_vol_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = VOL_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


_EN_PERCENT_WORDS: dict[str, int] = {
    "ten": 10,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}


def _parse_number_token(token: str) -> int | None:
    token = (token or "").strip().lower()
    if token.isdigit():
        return clamp_volume(int(token))
    if token in _VI_NUMBER_WORDS:
        return clamp_volume(_VI_NUMBER_WORDS[token] * 10)
    return None


def infer_volume_from_user_text(text: str) -> int | None:
    """Return target volume 0–100 when user asks to change speaker volume."""
    if not text or not str(text).strip():
        return None
    t = str(text).strip()

    if not re.search(
        r"volume|âm lượng|am luong|loa|speaker|to hơn|nhỏ hơn|im hơn|louder|quieter",
        t,
        re.IGNORECASE,
    ):
        return None

    match = _VOLUME_SET_RE.search(t)
    if match:
        return clamp_volume(int(match.group(1)))

    match = _VOLUME_SET_SUFFIX_RE.search(t)
    if match:
        return clamp_volume(int(match.group(1)))

    for word, num in _VI_NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s*(?:phần trăm|phan tram|%)\b", t, re.IGNORECASE):
            return clamp_volume(num * 10)

    for word, num in _EN_PERCENT_WORDS.items():
        if re.search(rf"\b{word}\s*(?:percent|%)\b", t, re.IGNORECASE):
            return clamp_volume(num)

    if _VOLUME_UP_RE.search(t):
        return 90
    if _VOLUME_DOWN_RE.search(t):
        return 35
    return None
