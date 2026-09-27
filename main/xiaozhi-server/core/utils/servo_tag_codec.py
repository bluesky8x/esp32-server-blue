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
| `srv:oe` | ĐO chân OE# của PCA9685: đảo mức vài lần để đồng hồ thấy được (`oe=5` = 5 lần) — dùng khi **không servo nào quay** nhưng V+/VCC vẫn có | `self.servo.oe_test` |
| `srv:swing=direct` | cách đi 1 cú vung: `arc` (một đường cong mix) / `direct` (kiểu Sesame) | `self.gait.swing` |
| `srv:speed=+30` | tốc độ toàn cục: `+30` = nhanh hơn 30% so với mức ĐANG dùng, `=130` = đặt 130%, `speed` = đọc | `self.gait.speed` |
| `srv:wave` | vẫy tay chào: 1 chân quay knee lên 170° rồi xuống 110°, lặp 5 lần (`wave=N` = chọn chân, `wave=N:M` = chân:số lần) | `self.gait.wave` |
| `srv:dance` | nhảy theo nhạc kiểu 4 chân, 1 động tác mỗi phách (`dance=DgvDf` = pattern riêng) | `self.gait.dance` |
| `srv:crawl=continuous` | cách phối hợp 4 chân: `continuous` (3 chân trụ quét cùng lúc, liền mạch) / `sequential` (từng chân một) | `self.gait.crawl` |
| `srv:relax` / `srv:enable` | thả lỏng / cấp lại lực servo | `self.servo.relax` / `enable` |
| `srv:status` | đọc trạng thái: trim, đảo chiều, góc đang ra lệnh của cả 8 joint | `self.servo.get_positions` |
| `srv:leg=2` | test RIÊNG 1 chân: gập knee từ từ 3 s rồi trả về (cách ly tải để tìm servo yếu/kẹt) | `self.gait.leg_sweep` |
| `srv:rot=90` | hướng MÀN HÌNH: `rot` = đọc, `rot=0/90/180/270` = xoay panel 240x240 (tự áp khoảng bù GRAM: 90/180 cần 80 px); `rot=90:80:0` = ghi đè khoảng bù để tinh chỉnh | `self.screen.rotate` |
| `srv:travel=0` | test HÀNH TRÌNH 1 joint: 0° → 180° → về 0° (hết dải xung 500-2500 µs), 2 vòng (4 lượt × 800 ms), **xong tự về neutral 90°**; nhiều joint: `srv:travel=0,1,2`; test NHỎ để xem chiều bàn chân: `srv:travel=0:small` | `self.servo.sweep` |
| `srv:cal` | ĐỌC mô hình hạ thân: alpha, chiều dài càng, offset đáy thân, chiều cao đứng, độ gập chạm nền | `self.gait.cal_crouch` |
| `srv:cal=20` | giữ tư thế để ĐO: 4 knee gập +20° rồi giữ nguyên (không tự trả về) | `self.gait.cal_crouch` |
| `srv:cal=20:10` | GHI ĐIỂM ĐO: gập 20° thì thân hạ 10 mm (dùng số người dùng vừa đo) | `self.gait.cal_crouch` |
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SERVO_COUNT = 8

# Test hành trình (srv:travel): 180° trong 800 ms ≈ 225°/s trung bình, đỉnh ~337°/s — nằm trong
# khả năng MG90S (~600°/s không tải) nên servo bám được đường cong, không bị trễ rồi bò theo sau.
# 2 vòng = 4 lượt (đi-về-đi-về) + 1 lượt về neutral ≈ 3.6 s. Hành trình VẬT LÝ do dải xung đang
# đặt quyết định (xem `srv:range`).
TRAVEL_TEST_MS = 800
TRAVEL_TEST_LAPS = 2
# Test hành trình NHỎ quanh neutral (90°) để quan sát CHIỀU chuyển động của bàn chân — CHỈ dùng khi
# gọi `srv:travel=N:small`; mặc định `srv:travel=N` vẫn quét TOÀN DẢI 0-180° (500-2500 µs).
#   Với hình học hiện tại (alpha 43.3°, L 55 mm) thì +20° ⇒ bàn chân nhấc ~16 mm và đẩy ra ngoài
#   ~10 mm — lệch nhiều so với số này nghĩa là số alpha/L chưa khớp robot thật.
TRAVEL_TEST_DEG = 20

# --- tool theo loại lệnh ---
SERVO_TOOL_BY_KIND: dict[str, str] = {
    "range": "self.servo.pulse_range",
    "trim": "self.servo.trim",
    "invert": "self.servo.invert",
    "raw": "self.servo.raw_pulse",
    "swing": "self.gait.swing",
    "crawl": "self.gait.crawl",
    "relax": "self.servo.relax",
    "enable": "self.servo.enable",
    "status": "self.servo.get_positions",
    "leg": "self.gait.leg_sweep",
    "travel": "self.servo.sweep",
    "cal": "self.gait.cal_crouch",
    "speed": "self.gait.speed",
    "wave": "self.gait.wave",
    "dance": "self.gait.dance",
    "rot": "self.screen.rotate",
    "oe": "self.servo.oe_test",
}

_RANGE_RE = re.compile(r"range\s*=\s*(\d{3,4})\s*[-–~]\s*(\d{3,4})", re.IGNORECASE)
_TRIM_RE = re.compile(r"trim\s*=\s*(\d{1,2})\s*:\s*(-?\d{1,2})", re.IGNORECASE)
_INVERT_RE = re.compile(r"invert\s*=\s*(\d{1,2})\s*:\s*([01])", re.IGNORECASE)
_RAW_RE = re.compile(r"raw\s*=\s*(\d{1,2})\s*:\s*(\d{3,4})", re.IGNORECASE)
_SWING_RE = re.compile(r"swing\s*=\s*(arc|direct|sesame)", re.IGNORECASE)
_CRAWL_RE = re.compile(r"crawl\s*=\s*(continuous|sequential|seq)", re.IGNORECASE)
_RELAX_RE = re.compile(r"relax\b", re.IGNORECASE)
_STATUS_RE = re.compile(r"status\b", re.IGNORECASE)
_LEG_TEST_RE = re.compile(r"leg\s*=\s*([0-3])\b", re.IGNORECASE)
_ENABLE_RE = re.compile(r"enable\b", re.IGNORECASE)
# 1 hoặc nhiều joint: "travel=0" / "travel=0,1,2" / "travel=4 5"; ":small" = test nhỏ ±20°
_TRAVEL_RE = re.compile(
    r"travel\s*=\s*(\d{1,2}(?:\s*(?:[,+]|\s)\s*\d{1,2})*)(?:\s*:\s*(small|nho|nho))?",
    re.IGNORECASE,
)
# Hiệu chuẩn độ sâu: "cal" (đọc) / "cal=20" (giữ để đo) / "cal=20:10" (ghi điểm) / "cal=reset"
_CAL_RE = re.compile(
    r"cal\s*(?:=\s*(reset|\d{1,2})(?:\s*:\s*(\d{1,3}))?)?", re.IGNORECASE
)
# Tốc độ toàn cục: "speed" (đọc) / "speed=130" (đặt 130% mặc định) / "speed=+30" (nhanh hơn 30%)
_SPEED_RE = re.compile(r"speed\s*(?:=\s*([+-]?\d{1,3}))?", re.IGNORECASE)
# Vẫy tay chào: "wave" = chân mặc định (trước-phải), "wave=N" = chọn chân, "wave=N:M" = chân : số lần
_WAVE_RE = re.compile(r"wave(?:\s*=\s*([0-3])(?:\s*:\s*(\d{1,2}))?)?", re.IGNORECASE)
# Nhảy theo nhạc (4 chân): "dance" = pattern mặc định, "dance=DgvDf" = pattern riêng (1 chữ = 1 phách)
_DANCE_RE = re.compile(r"dance(?:\s*=\s*([a-zA-Z]{1,16}))?", re.IGNORECASE)
# Hướng màn hình: "rot" = đọc (không đổi gì) / "rot=90" = xoay panel 0/90/180/270; kèm khoảng bù
# GRAM tuỳ chọn "rot=90:80:0" (mặc định lấy bảng trong firmware). KHÔNG lưu NVS.
_ROT_RE = re.compile(
    r"rot\s*(?:=\s*(0|90|180|270)(?:\s*:\s*(\d{1,3})(?:\s*:\s*(\d{1,3}))?)?)?",
    re.IGNORECASE,
)
# Test chân OE# (output enable, active LOW) của PCA9685: "oe" / "oe=5" (số lần đảo)
_OE_RE = re.compile(r"oe\s*(?:=\s*(\d{1,2}))?", re.IGNORECASE)

SERVO_TAG_RE = re.compile(
    r"\bsrv\s*:\s*(?:"
    r"range\s*=\s*\d{3,4}\s*[-–~]\s*\d{3,4}"
    r"|trim\s*=\s*\d{1,2}\s*:\s*-?\d{1,2}"
    r"|invert\s*=\s*\d{1,2}\s*:\s*[01]"
    r"|raw\s*=\s*\d{1,2}\s*:\s*\d{3,4}"
    r"|swing\s*=\s*(?:arc|direct|sesame)"
    r"|crawl\s*=\s*(?:continuous|sequential|seq)"
    r"|leg\s*=\s*[0-3]"
    r"|travel\s*=\s*\d{1,2}(?:\s*(?:[,+]|\s)\s*\d{1,2})*(?:\s*:\s*(?:small|nho))?"
    r"|cal(?:\s*=\s*(?:reset|\d{1,2})(?:\s*:\s*\d{1,3})?)?"
    r"|speed(?:\s*=\s*[+-]?\d{1,3})?"
    r"|wave(?:\s*=\s*[0-3](?:\s*:\s*\d{1,2})?)?"
    r"|dance(?:\s*=\s*[a-zA-Z]{1,16})?"
    r"|rot(?:\s*=\s*(?:0|90|180|270)(?:\s*:\s*\d{1,3}(?:\s*:\s*\d{1,3})?)?)?"
    r"|oe(?:\s*=\s*\d{1,2})?"
    r"|status"
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
    r"(?:\s*=\s*[\w,]{0,12})?"
    r"(?:\s*[-–~]\s*\d{0,4})?"
    r"(?:\s*:\s*\d{0,4})?"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ServoCommand:
    kind: str  # range | trim | invert | raw | swing | relax | enable | travel
    joint: int | None = None
    value: int | None = None
    value2: int | None = None
    mode: str | None = None

    def label(self) -> str:
        if self.kind == "range":
            return f"range={self.value}-{self.value2}"
        if self.kind == "trim":
            return f"trim={self.joint}:{self.value}"
        if self.kind == "invert":
            return f"invert={self.joint}:{self.value}"
        if self.kind == "raw":
            return f"raw={self.joint}:{self.value}"
        if self.kind == "swing":
            return f"swing={self.mode}"
        if self.kind == "crawl":
            return f"crawl={self.mode}"
        if self.kind == "leg":
            return f"leg={self.joint}"
        if self.kind == "travel":
            return f"travel={self.joint}" + (":small" if self.mode == "small" else "")
        if self.kind == "cal":
            if self.value is None:
                return "cal"
            if self.value == -1:
                return "cal=reset"
            if self.value2 is None:
                return f"cal={self.value}"
            return f"cal={self.value}:{self.value2}"
        if self.kind == "speed":
            if self.value is None:
                return "speed"
            return f"speed={self.value:+d}" if self.mode == "rel" else f"speed={self.value}"
        if self.kind == "wave":
            if self.joint is None:
                return "wave"
            return f"wave={self.joint}:{self.value}" if self.value else f"wave={self.joint}"
        if self.kind == "dance":
            return "dance" if not self.mode else f"dance={self.mode}"
        if self.kind == "rot":
            label = "rot" if self.value is None else f"rot={self.value}"
            return f"{label}:{self.mode}" if self.mode else label
        if self.kind == "oe":
            return "oe" if self.value is None else f"oe={self.value}"
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
    for m in _SWING_RE.finditer(raw):
        # "sesame" là bí danh của "direct"; "arc" là mặc định của firmware.
        mode = "arc" if m.group(1).lower() == "arc" else "direct"
        found.append((m.start(), ServoCommand(kind="swing", mode=mode)))
    for m in _CRAWL_RE.finditer(raw):
        # "seq" là bí danh của "sequential"; "continuous" là mặc định của firmware.
        mode = "continuous" if m.group(1).lower() == "continuous" else "sequential"
        found.append((m.start(), ServoCommand(kind="crawl", mode=mode)))
    for m in _RELAX_RE.finditer(raw):
        found.append((m.start(), ServoCommand(kind="relax")))
    for m in _STATUS_RE.finditer(raw):
        found.append((m.start(), ServoCommand(kind="status")))
    for m in _LEG_TEST_RE.finditer(raw):
        found.append((m.start(), ServoCommand(kind="leg", joint=int(m.group(1)))))
    for m in _TRAVEL_RE.finditer(raw):
        # "travel=0" (hoặc nhiều joint "travel=0,1,2") = quét TOÀN DẢI 0-180° như trước;
        # thêm ":small" = test NHỎ ±20° quanh neutral để quan sát chiều bàn chân.
        small = bool(m.group(2))
        for token in re.findall(r"\d{1,2}", m.group(1)):
            joint = _joint(token)
            if joint is None:
                continue
            found.append(
                (m.start(), ServoCommand(kind="travel", joint=joint,
                                         mode="small" if small else "full"))
            )
    for m in _ENABLE_RE.finditer(raw):
        found.append((m.start(), ServoCommand(kind="enable")))
    for m in _DANCE_RE.finditer(raw):
        # "dance" = nhảy pattern mặc định; "dance=DgvDf" = pattern riêng (mỗi chữ = 1 phách).
        pattern = m.group(1) if m.group(1) else None
        found.append((m.start(), ServoCommand(kind="dance", mode=pattern)))
    for m in _WAVE_RE.finditer(raw):
        # "wave" = vẫy bằng chân mặc định; "wave=1" = chọn chân 0..3; "wave=1:10" = chân : số lần vẫy.
        joint = int(m.group(1)) if m.group(1) is not None else None
        cycles = _clamp(int(m.group(2)), 1, 10) if m.group(2) else None
        found.append((m.start(), ServoCommand(kind="wave", joint=joint, value=cycles)))
    for m in _ROT_RE.finditer(raw):
        # "rot" = đọc hướng hiện tại (firmware trả rotation_deg, không đổi gì);
        # "rot=90" = xoay panel NGAY sang 0/90/180/270 (firmware tự áp khoảng bù GRAM của hướng
        # đó); "rot=90:80:0" = ghi đè khoảng bù (px) để tinh chỉnh nếu ảnh vẫn lệch.
        if m.group(1) is None:
            found.append((m.start(), ServoCommand(kind="rot")))
        else:
            gap = ":".join(g for g in m.groups()[1:] if g is not None)
            found.append(
                (m.start(), ServoCommand(kind="rot", value=int(m.group(1)), mode=gap or None))
            )
    for m in _OE_RE.finditer(raw):
        # "oe" = đảo chân OE# 3 lần (mặc định firmware); "oe=5" = 5 lần, để đo bằng đồng hồ.
        cycles = _clamp(int(m.group(1)), 1, 10) if m.group(1) else None
        found.append((m.start(), ServoCommand(kind="oe", value=cycles)))
    for m in _SPEED_RE.finditer(raw):        # "speed" = đọc tốc độ; "speed=130" = đặt 130% tốc độ mặc định; "speed=+30" = nhanh hơn
        # 30% so với mức ĐANG dùng (cách người dùng hay nói: "nhanh hơn ba mươi phần trăm").
        if m.group(1) is None:
            found.append((m.start(), ServoCommand(kind="speed", value=0)))
        else:
            token = m.group(1)
            relative = token[0] in "+-"
            pct = _clamp(int(token), -200, 200)
            found.append(
                (m.start(), ServoCommand(kind="speed", value=pct,
                                         mode="rel" if relative else "abs"))
            )
    for m in _CAL_RE.finditer(raw):
        # "cal" = đọc kết quả; "cal=F" = gập F° và giữ để đo; "cal=F:H" = ghi điểm (F°, CAO H mm
        # so với nền); "cal=reset" = xoá hiệu chuẩn, quay về alpha/L/offset mặc định config.h.
        if m.group(1) is None:
            found.append((m.start(), ServoCommand(kind="cal")))
        elif m.group(1).lower() == "reset":
            found.append((m.start(), ServoCommand(kind="cal", value=-1, value2=-1)))
        else:
            # fold 0 = chiều cao lúc ĐỨNG (không gập) — dùng để chốt lại offset đáy thân.
            fold = _clamp(int(m.group(1)), 0, 60)
            points = _clamp(int(m.group(2)), 1, 200) if m.group(2) else None
            found.append((m.start(), ServoCommand(kind="cal", value=fold, value2=points)))

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
    if cmd.kind == "swing":
        return tool, {"mode": cmd.mode}
    if cmd.kind == "crawl":
        return tool, {"mode": cmd.mode}
    if cmd.kind == "leg":
        # 1 chân làm việc một mình, rất chậm (3 s) ⇒ tải cách ly, tìm servo yếu/kẹt.
        # hip_deg=10 (min của tool) chỉ nhấp nhẹ hip để giữ nguyên giao diện; knee_deg=22° = đúng
        # biên độ đã test trên bàn (43° nay là 43° VẬT LÝ, sát trần cơ khí đã đo ~43° nên tránh).
        return tool, {"leg": cmd.joint, "hip_deg": 10, "knee_deg": 22, "duration_ms": 3000}
    if cmd.kind == "travel":
        # MẶC ĐỊNH = quét TOÀN DẢI: 0° → 180° → 0° (2 đầu dải xung 500-2500 µs), 2 vòng, mỗi lượt
        # 800 ms ⇒ nhanh, mượt, và đi hết hành trình cơ khí. end_neutral=1: lượt CUỐI đưa joint về
        # đúng vị trí NEUTRAL 90° (vị trí đứng của gait) chứ không để nằm ở 0°/180°.
        # ":small" = test NHỎ ±TRAVEL_TEST_DEG quanh neutral, để QUAN SÁT CHIỀU bàn chân.
        if cmd.mode == "small":
            d = TRAVEL_TEST_DEG
            return tool, {
                "joint": cmd.joint,
                "from_deg": 90 - d,
                "to_deg": 90 + d,
                "duration_ms": TRAVEL_TEST_MS,
                "laps": TRAVEL_TEST_LAPS,
                "end_neutral": 1,
            }
        return tool, {
            "joint": cmd.joint,
            "from_deg": 0,
            "to_deg": 180,
            "duration_ms": TRAVEL_TEST_MS,
            "laps": TRAVEL_TEST_LAPS,
            "end_neutral": 1,
        }
    if cmd.kind == "speed":
        # Tốc độ toàn cục: "speed=+30" (nhanh hơn 30% so với mức ĐANG dùng) / "speed=130" (đặt
        # tuyệt đối 130% mặc định) / "speed" (đọc: percent=0 ⇒ firmware không đổi gì).
        if cmd.value is None:
            return tool, {"percent": 0}
        if cmd.mode == "rel":
            return tool, {"percent": cmd.value, "relative": 1}
        return tool, {"percent": cmd.value}
    if cmd.kind == "wave":
        # Vẫy tay chào: không có joint ⇒ firmware dùng chân mặc định (trước-phải, leg 1).
        args: dict = {}
        if cmd.joint is not None:
            args["leg"] = cmd.joint
        if cmd.value is not None:
            args["cycles"] = cmd.value
        return tool, args
    if cmd.kind == "dance":
        # Nhảy 4 chân theo phách: pattern rỗng ⇒ firmware dùng GAIT_DANCE_DEFAULT_PATTERN.
        if not cmd.mode:
            return tool, {}
        return tool, {"pattern": cmd.mode}
    if cmd.kind == "rot":
        # Hướng màn hình: "rot=90" xoay panel ngay (0/90/180/270 — firmware làm tròn về bội số
        # của 90 và tự áp khoảng bù GRAM của hướng đó). "rot" không kèm số ⇒ không truyền deg,
        # firmware giữ nguyên hướng đang dùng và trả lại rotation_deg + offset đang áp.
        # "rot=90:80:0" ⇒ gửi kèm offset_x/offset_y để tinh chỉnh (px, 0..200).
        if cmd.value is None:
            return tool, {}
        args: dict = {"deg": cmd.value}
        if cmd.mode:
            parts = [int(p) for p in str(cmd.mode).split(":") if p.isdigit()]
            if parts:
                args["offset_x"] = parts[0]
            if len(parts) > 1:
                args["offset_y"] = parts[1]
        return tool, args
    if cmd.kind == "oe":
        # Test chân OE#: "oe" = firmware tự dùng 3 lần đảo; "oe=5" = 5 lần (mỗi lần 1 s).
        if cmd.value is None:
            return tool, {}
        return tool, {"cycles": cmd.value}
    if cmd.kind == "cal":
        # Hieu chuan do sau: `cal` = doc alpha/L/offset/tran; `cal=F` = gap F do roi GIU de nguoi
        # dung do CHIEU CAO DAY THAN SO VOI NEN bang thuoc; `cal=F:H` = ghi diem do (F do -> cao
        # H mm). Diem thu 2 vao la firmware tu khop alpha & L va luu NVS (xem self.gait.cal_crouch).
        if cmd.value is None:
            return tool, {}
        if cmd.value == -1:
            return tool, {"height_mm": -1}
        if cmd.value2 is None:
            return tool, {"fold_deg": cmd.value}
        return tool, {"fold_deg": cmd.value, "height_mm": cmd.value2}
    return tool, {}
