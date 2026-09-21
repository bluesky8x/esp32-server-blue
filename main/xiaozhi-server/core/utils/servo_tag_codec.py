"""Servo calibration tags `srv:*` — cùng pattern với `tof:cal` / `mv:*`.

Vì sao cần: LLM **không** tự gọi tool `self.servo.*` (nó trả lời "mình chưa làm được"),
nên hiệu chuẩn servo phải đi qua tag giống ToF. Server chỉ parse tag rồi gọi đúng tool
thiết bị — không hardcode, không fallback.

| Tag | Việc | Tool thiết bị |
|---|---|---|
| `srv:range=1000-2000` | dải xung µs — servo MG90S kẹt stop thì chỉ tick không quay | `self.servo.pulse_range` |
| `srv:trim=2:-3` | lệch góc chuẩn của 1 joint (độ) | `self.servo.trim` |
| `srv:invert=2:1` | đảo chiều 1 joint (chân đối xứng quay ngược) | `self.servo.invert` |
| `srv:raw=0:1200` | test phần cứng: phát xung cố định cho 1 joint | `self.servo.raw_pulse` |
| `srv:relax` / `srv:enable` | thả lỏng / cấp lại lực servo | `self.servo.relax` / `enable` |
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SERVO_COUNT = 8

# --- tool theo loại lệnh ---
SERVO_TOOL_BY_KIND: dict[str, str] = {
    "range": "self.servo.pulse_range",
    "trim": "self.servo.trim",
    "invert": "self.servo.invert",
    "raw": "self.servo.raw_pulse",
    "relax": "self.servo.relax",
    "enable": "self.servo.enable",
}

_RANGE_RE = re.compile(r"range\s*=\s*(\d{3,4})\s*[-–~]\s*(\d{3,4})", re.IGNORECASE)
_TRIM_RE = re.compile(r"trim\s*=\s*(\d{1,2})\s*:\s*(-?\d{1,2})", re.IGNORECASE)
_INVERT_RE = re.compile(r"invert\s*=\s*(\d{1,2})\s*:\s*([01])", re.IGNORECASE)
_RAW_RE = re.compile(r"raw\s*=\s*(\d{1,2})\s*:\s*(\d{3,4})", re.IGNORECASE)
_RELAX_RE = re.compile(r"relax\b", re.IGNORECASE)
_ENABLE_RE = re.compile(r"enable\b", re.IGNORECASE)

SERVO_TAG_RE = re.compile(
    r"\bsrv\s*:\s*(?:"
    r"range\s*=\s*\d{3,4}\s*[-–~]\s*\d{3,4}"
    r"|trim\s*=\s*\d{1,2}\s*:\s*-?\d{1,2}"
    r"|invert\s*=\s*\d{1,2}\s*:\s*[01]"
    r"|raw\s*=\s*\d{1,2}\s*:\s*\d{3,4}"
    r"|relax|enable"
    r")\b",
    re.IGNORECASE,
)
SERVO_TAG_STRIP_RE = SERVO_TAG_RE

# Tiền tố tag còn dở khi stream ("... srv:ra", "... srv:range=1000-") — giữ lại, không đọc lên loa.
# Giữ cả tag đã hoàn chỉnh (như mv:) — chỉ nhả ra ở flush.
_INCOMPLETE_SERVO_SUFFIX_RE = re.compile(
    r"(?:\s+srv\s*:"
    r"(?:\s*[A-Za-z]{0,8})?"
    r"(?:\s*=\s*\d{0,4})?"
    r"(?:\s*[-–~]\s*\d{0,4})?"
    r"(?:\s*:\s*\d{0,4})?"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ServoCommand:
    kind: str  # range | trim | invert | raw | relax | enable
    joint: int | None = None
    value: int | None = None
    value2: int | None = None

    def label(self) -> str:
        if self.kind == "range":
            return f"range={self.value}-{self.value2}"
        if self.kind == "trim":
            return f"trim={self.joint}:{self.value}"
        if self.kind == "invert":
            return f"invert={self.joint}:{self.value}"
        if self.kind == "raw":
            return f"raw={self.joint}:{self.value}"
        return self.kind


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(int(value), hi))


def _joint(value: str) -> int | None:
    idx = _clamp(int(value), 0, SERVO_COUNT - 1)
    return idx


def extract_servo_commands(text: str | None) -> list[ServoCommand]:
    """Parse mọi tag `srv:*` trong text (theo thứ tự xuất hiện)."""
    if not text:
        return []
    raw = str(text)
    found: list[tuple[int, ServoCommand]] = []

    for m in _RANGE_RE.finditer(raw):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= 0 or hi <= lo:
            continue
        found.append(
            (
                m.start(),
                ServoCommand(
                    kind="range", value=_clamp(lo, 200, 2500), value2=_clamp(hi, 201, 2500)
                ),
            )
        )
    for m in _TRIM_RE.finditer(raw):
        joint = _joint(m.group(1))
        if joint is None:
            continue
        found.append(
            (m.start(), ServoCommand(kind="trim", joint=joint, value=_clamp(m.group(2), -30, 30)))
        )
    for m in _INVERT_RE.finditer(raw):
        joint = _joint(m.group(1))
        if joint is None:
            continue
        found.append(
            (m.start(), ServoCommand(kind="invert", joint=joint, value=int(m.group(2))))
        )
    for m in _RAW_RE.finditer(raw):
        joint = _joint(m.group(1))
        if joint is None:
            continue
        found.append(
            (m.start(), ServoCommand(kind="raw", joint=joint, value=_clamp(m.group(2), 200, 2500)))
        )
    for m in _RELAX_RE.finditer(raw):
        found.append((m.start(), ServoCommand(kind="relax")))
    for m in _ENABLE_RE.finditer(raw):
        found.append((m.start(), ServoCommand(kind="enable")))

    # Chỉ nhận các lệnh nằm trong một tag srv: (tránh bắt chữ "relax" trong câu nói thường).
    spans = [m.span() for m in SERVO_TAG_RE.finditer(raw)]
    if not spans:
        return []
    ordered = [cmd for pos, cmd in sorted(found, key=lambda item: item[0])
               if any(s <= pos < e for s, e in spans)]
    seen: set[str] = set()
    unique: list[ServoCommand] = []
    for cmd in ordered:
        key = cmd.label()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cmd)
    return unique


def strip_servo_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = SERVO_TAG_STRIP_RE.sub("", text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def hold_incomplete_servo_suffix(text: str) -> str:
    """Phần đuôi tag còn dở (giữ lại, không đọc lên loa)."""
    if not text:
        return ""
    match = _INCOMPLETE_SERVO_SUFFIX_RE.search(text)
    return match.group(0) if match else ""


def build_servo_mcp_call(
    cmd: ServoCommand, available: set[str] | None = None
) -> tuple[str | None, dict]:
    """→ (tool, args). Tool không có trên thiết bị → (None, {}) để caller log và bỏ."""
    tool = SERVO_TOOL_BY_KIND.get(cmd.kind)
    if not tool:
        return None, {}
    if available:
        if tool not in available:
            sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", tool)
            if sanitized in available:
                tool = sanitized
            else:
                return None, {}
    if cmd.kind == "range":
        return tool, {"min_us": cmd.value, "max_us": cmd.value2}
    if cmd.kind == "trim":
        return tool, {"joint": cmd.joint, "trim": cmd.value}
    if cmd.kind == "invert":
        return tool, {"joint": cmd.joint, "inverted": cmd.value}
    if cmd.kind == "raw":
        return tool, {"joint": cmd.joint, "pulse_us": cmd.value}
    return tool, {}
