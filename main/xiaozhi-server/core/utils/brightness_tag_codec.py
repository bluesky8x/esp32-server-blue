"""LED brightness control tags for Blue robots (mirrors volume_tag_codec)."""

from __future__ import annotations

import re

LED_TAG_RE = re.compile(r"\bled\s*:\s*(\d{1,3})\b", re.IGNORECASE)
LED_TAG_STRIP_RE = re.compile(r"\bled\s*:\s*\d{1,3}\b", re.IGNORECASE)

_BRIGHTNESS_SET_RE = re.compile(
    r"(?:brightness|độ sáng|do sang|đèn|den|led|sáng|sang)"
    r".{0,24}?"
    r"(\d{1,3})\s*(?:%|phần trăm|phan tram)?",
    re.IGNORECASE,
)
_BRIGHTNESS_SET_SUFFIX_RE = re.compile(
    r"\b(\d{1,3})\s*(?:%|phần trăm|phan tram)\b", re.IGNORECASE
)
_BRIGHTNESS_UP_RE = re.compile(
    r"(?:tăng.*(?:sáng|sang|đèn|den|led|brightness)"
    r"|sáng hơn|sang hon|brighter|turn up.*(?:led|light|brightness)"
    r"|(?:sáng|sang|đèn|den|led|brightness).*tăng)",
    re.IGNORECASE,
)
_BRIGHTNESS_DOWN_RE = re.compile(
    r"(?:giảm.*(?:sáng|sang|đèn|den|led|brightness)"
    r"|tối hơn|toi hon|mờ hơn|mo hon|dimmer|turn down.*(?:led|light|brightness)"
    r"|(?:sáng|sang|đèn|den|led|brightness).*giảm)",
    re.IGNORECASE,
)
_BRIGHTNESS_OFF_RE = re.compile(
    r"(?:tắt.*(?:đèn|den|led|sáng|sang)|turn off.*(?:led|light)"
    r"|(?:đèn|den|led).*tắt)",
    re.IGNORECASE,
)
_BRIGHTNESS_ON_RE = re.compile(
    r"(?:bật.*(?:đèn|den|led|sáng|sang)|turn on.*(?:led|light)"
    r"|(?:đèn|den|led).*bật)",
    re.IGNORECASE,
)

_BRIGHTNESS_KEYWORD_RE = re.compile(
    r"brightness|độ sáng|do sang|đèn|den|led|sáng hơn|tối hơn|mờ hơn"
    r"|brighter|dimmer|tắt đèn|bật đèn|turn off.*light|turn on.*light",
    re.IGNORECASE,
)

_VI_NUMBER_WORDS: dict[str, int] = {
    "một": 1, "mot": 1,
    "hai": 2, "ba": 3,
    "bốn": 4, "bon": 4,
    "năm": 5, "nam": 5,
    "sáu": 6, "sau": 6,
    "bảy": 7, "bay": 7,
    "tám": 8, "tam": 8,
    "chín": 9, "chin": 9,
    "mười": 10, "muoi": 10,
}


def clamp_brightness(value: int) -> int:
    return max(0, min(100, int(value)))


def extract_brightness_from_assistant_text(text: str) -> int | None:
    if not text:
        return None
    match = LED_TAG_RE.search(text)
    if not match:
        return None
    return clamp_brightness(int(match.group(1)))


def strip_led_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = LED_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def infer_brightness_from_user_text(text: str) -> int | None:
    """Return target brightness 0–100 when user asks to change LED brightness."""
    if not text or not str(text).strip():
        return None
    t = str(text).strip()

    if not _BRIGHTNESS_KEYWORD_RE.search(t):
        return None

    # "tắt đèn" → 0
    if _BRIGHTNESS_OFF_RE.search(t):
        return 0
    # "bật đèn" → 100
    if _BRIGHTNESS_ON_RE.search(t):
        return 100

    match = _BRIGHTNESS_SET_RE.search(t)
    if match:
        return clamp_brightness(int(match.group(1)))

    match = _BRIGHTNESS_SET_SUFFIX_RE.search(t)
    if match:
        return clamp_brightness(int(match.group(1)))

    for word, num in _VI_NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s*(?:phần trăm|phan tram|%)\b", t, re.IGNORECASE):
            return clamp_brightness(num * 10)

    if _BRIGHTNESS_UP_RE.search(t):
        return 90
    if _BRIGHTNESS_DOWN_RE.search(t):
        return 30
    return None
