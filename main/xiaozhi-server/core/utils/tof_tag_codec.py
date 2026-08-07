"""ToF distance-sensor calibration tags (Intent nointent, same pattern as vol: / mv:)."""

from __future__ import annotations

import re

TOF_CAL_TAG_RE = re.compile(
    r"\btof\s*:\s*cal(?:\s*:\s*(\d{1,4}))?\b", re.IGNORECASE
)
TOF_CAL_TAG_STRIP_RE = re.compile(
    r"\btof\s*:\s*cal(?:\s*:\s*\d{1,4})?\b", re.IGNORECASE
)

_CALIBRATE_INTENT_RE = re.compile(
    r"(?:hiệu chuẩn|hieu chuan|calibrat|canh chinh|canh chuẩn|"
    r"cân chỉnh|cai dat cam bien|cài đặt cảm biến|"
    r"cảm biến khoảng cách|cam bien khoang cach|"
    r"vl53|tof sensor|distance sensor)",
    re.IGNORECASE,
)

_DISTANCE_IN_TEXT_RE = re.compile(
    r"(\d{1,3})\s*(?:mm|milimet|millimet|cm|centimet)\b", re.IGNORECASE
)
_CM_RE = re.compile(r"(\d{1,2})\s*cm\b", re.IGNORECASE)


def default_calibration_distance_mm() -> int:
    return 100


def clamp_calibration_distance(value: int) -> int:
    return max(50, min(800, int(value)))


def extract_tof_calibrate_from_assistant_text(text: str) -> int | None:
    """Return target distance mm when assistant appended tof:cal[:N]."""
    if not text:
        return None
    match = TOF_CAL_TAG_RE.search(text)
    if not match:
        return None
    if match.group(1):
        return clamp_calibration_distance(int(match.group(1)))
    return default_calibration_distance_mm()


def strip_tof_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = TOF_CAL_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def infer_tof_calibrate_from_user_text(text: str) -> int | None:
    """Return calibration distance mm when user asks to calibrate ToF."""
    if not text or not str(text).strip():
        return None
    t = str(text).strip()
    if not _CALIBRATE_INTENT_RE.search(t):
        return None

    cm = _CM_RE.search(t)
    if cm:
        return clamp_calibration_distance(int(cm.group(1)) * 10)

    mm = _DISTANCE_IN_TEXT_RE.search(t)
    if mm:
        val = int(mm.group(1))
        span = mm.group(0).lower()
        if "cm" in span or "centimet" in span:
            val *= 10
        return clamp_calibration_distance(val)

    return default_calibration_distance_mm()
