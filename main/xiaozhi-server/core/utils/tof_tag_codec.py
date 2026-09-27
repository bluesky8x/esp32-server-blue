"""ToF distance-sensor calibration tags (Intent nointent, same pattern as vol: / mv:)."""

from __future__ import annotations

import re

TOF_CAL_TAG_RE = re.compile(
    r"\btof\s*:\s*cal(?:\s*:\s*(\d{1,4}))?\b", re.IGNORECASE
)
TOF_CAL_TAG_STRIP_RE = re.compile(
    r"\btof\s*:\s*cal(?:\s*:\s*\d{1,4})?\b", re.IGNORECASE
)
# `tof:clr` = XOÁ hiệu chuẩn đã lưu trên thiết bị (self.tof.clear_calibration) ⇒ guard quay lại
# ngưỡng fallback. Nhận cả `tof:clear` / `tof:reset` cho dễ nói.
TOF_CLR_TAG_RE = re.compile(r"\btof\s*:\s*(?:clr|clear|reset)\b", re.IGNORECASE)
TOF_CLR_TAG_STRIP_RE = re.compile(r"\btof\s*:\s*(?:clr|clear|reset)\b", re.IGNORECASE)

_CALIBRATE_INTENT_RE = re.compile(
    r"(?:hiệu chuẩn|hieu chuan|hiệu chỉnh|hieu chinh|calibrat|canh chinh|canh chuẩn|"
    r"cân chỉnh|cai dat cam bien|cài đặt cảm biến|"
    r"cảm biến khoảng cách|cam bien khoang cach|"
    r"vl53|tof sensor|distance sensor)",
    re.IGNORECASE,
)

_READY_CONFIRM_RE = re.compile(
    r"(?:\bxong\b|\bok\b|\bready\b|đặt xong|dat xong|"
    r"hiệu chuẩn đi|hieu chuan di|calibrate now|"
    r"bắt đầu hiệu chuẩn|bat dau hieu chuan|làm đi|lam di)",
    re.IGNORECASE,
)

_DISTANCE_IN_TEXT_RE = re.compile(
    r"(\d{1,3})\s*(?:mm|milimet|millimet|cm|centimet)\b", re.IGNORECASE
)
_CM_RE = re.compile(r"(\d{1,2})\s*cm\b", re.IGNORECASE)


def clamp_calibration_distance(value: int) -> int:
    """0 = auto-calibrate to current sensor median reading on device."""
    if value <= 0:
        return 0
    return max(50, min(800, int(value)))


def extract_tof_calibrate_from_assistant_text(text: str) -> int | None:
    """Return calibration distance mm when assistant appended tof:cal[:N].

    Bare ``tof:cal`` → 0 (device auto-calibrates from median reading).
    ``tof:cal:350`` → explicit target only when user measured that distance.
    """
    if not text:
        return None
    match = TOF_CAL_TAG_RE.search(text)
    if not match:
        return None
    if match.group(1):
        return clamp_calibration_distance(int(match.group(1)))
    return 0


def strip_tof_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = TOF_CLR_TAG_STRIP_RE.sub("", text)
    cleaned = TOF_CAL_TAG_STRIP_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def has_tof_clear_in_assistant_text(text: str) -> bool:
    """True khi assistant gắn tag `tof:clr` (xoá hiệu chuẩn đang lưu trên thiết bị)."""
    return bool(text and TOF_CLR_TAG_RE.search(text))


_CLEAR_INTENT_RE = re.compile(
    r"(?:xóa hiệu chuẩn|xoa hieu chuan|xóa cal|xoa cal|xóa cảm biến|xoa cam bien|"
    r"bỏ hiệu chuẩn|bo hieu chuan|reset cảm biến|reset cam bien|"
    r"hiệu chuẩn lại|hieu chuan lai|làm lại hiệu chuẩn|lam lai hieu chuan|"
    r"clear\s+(?:the\s+)?calibration|reset\s+(?:the\s+)?(?:tof|distance sensor|calibration))",
    re.IGNORECASE,
)


def infer_tof_clear_from_user_text(text: str) -> bool:
    """True khi user yêu cầu XOÁ hiệu chuẩn ToF (ưu tiên hơn ý định hiệu chuẩn)."""
    return bool(text and _CLEAR_INTENT_RE.search(str(text)))


def infer_tof_calibrate_from_user_text(text: str) -> int | None:
    """Return calibration distance mm when user asks to calibrate ToF.

    Single-step: the user has ALREADY placed the robot where they want, so any
    calibration request (\"hiệu chuẩn...\", \"ok\", \"xong\", \"ready\"...) →
    0 = auto-calibrate immediately from the device's median reading.
    """
    if not text or not str(text).strip():
        return None
    t = str(text).strip()
    wants_cal = bool(_CALIBRATE_INTENT_RE.search(t))
    ready = bool(_READY_CONFIRM_RE.search(t))
    if not wants_cal and not ready:
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

    # Auto-calibrate from the device's current reading (user already positioned it).
    return 0
