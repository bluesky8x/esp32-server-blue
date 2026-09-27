"""Compact robot move tags embedded in LLM text (stripped before TTS).

Protocol: ``mv:<code>[:<seconds>]`` appended **at end of reply**, e.g.
``Mình quay trái 10 giây nha mv:t:10`` — never ``Mình mv:t rồi nha``.

| Code | Action        | Device MCP (primary)      |
|------|---------------|---------------------------|
| t    | turn left     | self.motor.turn_left      |
| p    | turn right    | self.motor.turn_right     |
| f    | forward       | self.motor.forward        |
| b    | backward      | self.motor.backward       |
| c    | circle (arc)  | self.motor.circle         |
| d    | dance 1       | stream ./music/ + self.motor.dance |
| d2   | dance 2       | stream ./music/ + self.motor.dance |
| d3   | dance 3       | stream ./music/ + self.motor.dance |
| ld   | dance 1 (alias) | same as mv:d |
| ld2  | dance 2 (alias) | same as mv:d2 |
| ld3  | dance 3 (alias) | same as mv:d3 |
| s    | stop          | self.motor.stop           |

Duration (seconds): optional suffix ``:N`` after code — default 5, max 30.
``mv:d`` / ``mv:ld`` / ``mv:d2`` / ``mv:ld2`` / ``mv:d3`` / ``mv:ld3`` all stream music from server ``./music/`` (no embedded dance on robot).
Optional ``:N`` on dance tags is ignored (server cooldown only).
Server prefers ``self.motor.move`` with ``duration_ms`` when available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ROBOT_MOVE_SEQUENCE = 5
DEFAULT_ROBOT_MOVE_DURATION_SEC = 5
MAX_ROBOT_MOVE_DURATION_SEC = 30
DANCE_MOVE_DURATION_SEC = 24
DANCE2_MOVE_DURATION_SEC = 100
DANCE3_MOVE_DURATION_SEC = 26

# --- "bước" (steps) ---
# 1 bước = 1 chu kỳ crawl = cả 4 chân nhấc-hạ một lần (theo quy ước của robot nhện v4).
# Trên robot có self.gait.walk/self.gait.turn, tag `mv:f:steps=3` được gửi NGUYÊN số bước
# (chính xác tuyệt đối). Trên robot chỉ có self.motor.* (blue-v2) thì quy đổi ra duration_ms
# bằng thời gian thật của N bước.
DEFAULT_CRAWL_STEP_MS = 350  # khớp default step_ms của tool self.gait.walk (chỉ để ƯỚC LƯỢNG)
MAX_MOVE_STEPS_PER_CALL = 8  # firmware self.gait.walk chặn 1..8 (vượt → MCP báo lỗi)
# Hằng số nhịp của gait (main/boards/blue-v4/config.h, GAIT_JOINT_SEQUENTIAL_CRAWL) — CHỈ dùng để
# ước lượng thời gian chờ của tag `steps=`; việc đi thật do firmware quyết.
# Mặc định firmware chạy crawl LIÊN TỤC (duty factor 3/4): cả 4 chân trên một đồng hồ, lệch pha
# 25% ⇒ 1 bước = 1 chu kỳ = 4 × thời gian vung, KHÔNG còn pha push/chờ riêng.
# (Chế độ cũ SEQUENTIAL = (swing + chờ chạm nền + push) × 4 ≈ 4x lâu hơn — đổi bằng tag
# `srv:crawl=sequential`; khi đó con số ở đây sẽ ước lượng thấp, chỉ ảnh hưởng thời gian chờ.)
_default_crawl_step_ms = DEFAULT_CRAWL_STEP_MS


def crawl_cycle_ms(step_ms: int | None = None) -> int:
    """Thời gian thật (ms) của MỘT bước = 1 chu kỳ 4 chân của crawl liên tục."""
    if step_ms is None:
        step_ms = _default_crawl_step_ms
    # Firmware: clamp(step_ms, 200..6000) rồi clamp tiếp 150..2000 cho riêng thời gian vung.
    swing = max(200, min(int(step_ms), 2000))
    return swing * 4


def set_default_crawl_step_ms(step_ms: int) -> None:
    """Connection gọi khi khởi tạo để mọi quy đổi "giây → bước" khớp config."""
    global _default_crawl_step_ms
    try:
        _default_crawl_step_ms = max(200, min(int(step_ms), 6000))
    except (TypeError, ValueError):
        pass


def steps_for_duration(duration_sec: int, step_ms: int | None = None) -> int:
    """Đổi "giây" của người dùng ra số bước (làm tròn), clamp 1..MAX_MOVE_STEPS_PER_CALL."""
    cycle = max(crawl_cycle_ms(step_ms), 400)
    steps = int(round(max(1, int(duration_sec)) * 1000 / cycle))
    return max(1, min(steps, MAX_MOVE_STEPS_PER_CALL))


def clamp_move_steps(steps: int | str | None) -> int | None:
    if steps is None or steps == "":
        return None
    try:
        value = int(steps)
    except (TypeError, ValueError):
        return None
    return max(1, min(value, MAX_MOVE_STEPS_PER_CALL))

# mv:t  mv:t:10  mv:d3  mv:ld2  Kita mv:f:5  mv:d:song=Shape of You  mv:d2:song=Cắt đôi nỗi sầu
MOVE_TAG_RE = re.compile(
    r"(?:\bKita\s+)?mv\s*:\s*(ld3|ld2|ld|d3|d2|[tprfbsdc])"
    r"(?:(?:\s*:\s*(\d+))?(?:\s*:\s*(?:song=)?[\"']?([^\"'\n\r]+?)[\"']?)?|(?:\s*:\s*(?:song=)?[\"']?([^\"'\n\r\d]+.*?)[\"']?))?"
    r"(?=(?:\s+mv:|\s*$|[.,!?]))",
    re.IGNORECASE,
)
MOVE_TAG_STRIP_RE = re.compile(
    r"(?:\bKita\s+)?mv\s*:\s*(?:ld3|ld2|ld|d3|d2|[tprfbsdc])"
    r"(?:(?:\s*:\s*\d+)?(?:\s*:\s*(?:song=)?[\"']?[^\"'\n\r]+?[\"']?)?|(?:\s*:\s*(?:song=)?[\"']?[^\"'\n\r\d]+.*?[\"']?))?"
    r"(?=(?:\s+mv:|\s*$|[.,!?]))",
    re.IGNORECASE,
)

_INCOMPLETE_MOVE_SUFFIX_RE = re.compile(
    r"(?:\s+(?:Kita\s+)?(?:m(?:v(?:\s*:\s*(?:ld3|ld2|ld|d3|d2|[tprfbsd]?(?:\s*:\s*[^\"'\n\r]{0,30})?)?)?)?)?)$",
    re.IGNORECASE,
)

MOVE_CODE_TO_MCP: dict[str, tuple[str, ...]] = {
    "t": ("self.motor.turn_left", "self.chassis.turn_left"),
    "p": ("self.motor.turn_right", "self.chassis.turn_right"),
    "f": ("self.motor.forward", "self.chassis.go_forward"),
    "b": ("self.motor.backward", "self.chassis.go_back"),
    "c": ("self.motor.circle",),
    "d": ("self.motor.dance", "self.chassis.dance"),
    "d2": ("self.motor.dance", "self.chassis.dance"),
    "d3": ("self.motor.dance", "self.chassis.dance"),
    "ld": ("self.motor.dance", "self.chassis.dance"),
    "ld2": ("self.motor.dance", "self.chassis.dance"),
    "ld3": ("self.motor.dance", "self.chassis.dance"),
    "s": ("self.motor.stop",),
}

MOVE_WHEEL_SPEEDS: dict[str, tuple[int, int]] = {
    "t": (70, -70),
    "p": (-70, 70),
    "f": (100, 100),
    "b": (-100, -100),
    "c": (50, 100),
}

MOTOR_MOVE_TOOL_CANDIDATES: tuple[str, ...] = ("self.motor.move",)

# Robot có gait chân (blue-v4): tag `steps=` đi thẳng vào 2 tool này.
GAIT_WALK_TOOL: str = "self.gait.walk"
GAIT_TURN_TOOL: str = "self.gait.turn"
# code → (tool, direction). Turn: -1 = trái, +1 = phải (khớp blue_v4_motor_compat.cc).
MOVE_STEPS_TO_GAIT: dict[str, tuple[str, int]] = {
    "f": (GAIT_WALK_TOOL, 1),
    "b": (GAIT_WALK_TOOL, -1),
    "t": (GAIT_TURN_TOOL, -1),
    "p": (GAIT_TURN_TOOL, 1),
}

# --- Board profiles -----------------------------------------------------------
# Thiết bị tự khai board khi bắt tay MCP: {"serverInfo": {"name": "blue-v4", ...}}
# (device_mcp/mcp_handler.py lưu vào conn.device_board). Server chọn tool theo profile
# thay vì dò mù:
#   * blue-v4 (robot nhện, gait joint-space): `steps=` đi thẳng vào self.gait.walk /
#     self.gait.turn (chính xác số bước), còn "giây" dùng self.motor.move(duration_ms).
#   * blue-v2 (bánh xe / 2-DoF): chỉ self.motor.* — mọi yêu cầu quy về duration_ms.
# Thêm board mới: thêm 1 entry. Role "walk"/"turn" chỉ có nghĩa với robot chân.
BOARD_TOOL_PROFILES: dict[str, dict[str, str]] = {
    "blue-v4": {
        "walk": "self.gait.walk",
        "turn": "self.gait.turn",
        "move": "self.motor.move",
        "circle": "self.motor.circle",
        "dance": "self.motor.dance",
        "stop": "self.motor.stop",
    },
    "blue-v2": {
        "move": "self.motor.move",
        "circle": "self.motor.circle",
        "dance": "self.motor.dance",
        "stop": "self.motor.stop",
    },
}

_BOARD_ALIASES: dict[str, str] = {
    "bluev4": "blue-v4",
    "blue_v4": "blue-v4",
    "blue4": "blue-v4",
    "bluev2": "blue-v2",
    "blue_v2": "blue-v2",
    "blue2": "blue-v2",
}


def normalize_board_name(board: str | None) -> str | None:
    """"blue_v4" / "BlueV4" / "blue-v4-2.4.1" -> "blue-v4"."""
    if not board:
        return None
    key = str(board).strip().lower().replace(" ", "")
    key = _BOARD_ALIASES.get(key, key)
    for known in BOARD_TOOL_PROFILES:
        if key == known or key.startswith(known + "-") or key.startswith(known + "."):
            return known
    return key


def board_tool_profile(board: str | None) -> dict[str, str]:
    """Tool map của board (rỗng = board lạ → dùng đường dò tool như cũ)."""
    key = normalize_board_name(board)
    if key and key in BOARD_TOOL_PROFILES:
        return BOARD_TOOL_PROFILES[key]
    return {}


def board_has_gait(board: str | None) -> bool:
    """True nếu board là robot chân (có walk/turn) — dùng để dạy LLM dạng `steps=`."""
    profile = board_tool_profile(board)
    return bool(profile.get("walk") and profile.get("turn"))


# `mv:f:steps=3` / `mv:f:3:steps=4` / tiếng Việt `mv:f:buoc=3`
_STEPS_IN_TAG_RE = re.compile(
    r":\s*(?:steps|buoc|bước)\s*=\s*(\d{1,2})", re.IGNORECASE
)

_REFUSAL_RE = re.compile(
    r"không thể|cannot|không quay|không đi|không làm được", re.IGNORECASE
)
_CAPABILITY_RE = re.compile(
    r"có thể đi|có thể quay|muốn mình làm gì|muốn em làm gì", re.IGNORECASE
)
_DURATION_IN_TEXT_RE = re.compile(
    r"(?:trong|for)\s+(\d{1,2})\s*(?:gi(?:â|a)y|seconds?|secs?)\b",
    re.IGNORECASE,
)
# "10 giây", "mười giây" at end of phrase (user ASR / assistant reply)
_DURATION_VI_SUFFIX_RE = re.compile(
    r"\b(\d{1,2}|mười|muoi|năm|nam|một|mot|hai|ba|bốn|bon|sáu|sau|bảy|bay|tám|tam|chín|chin)\s*"
    r"(?:gi(?:â|a)y|seconds?|secs?)\b",
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
DANCE_CODES = frozenset({"d", "d2", "d3", "ld", "ld2", "ld3"})
LIVE_DANCE_CODES = DANCE_CODES


def is_dance_code(code: str) -> bool:
    return (code or "").lower() in DANCE_CODES


def is_live_dance_code(code: str) -> bool:
    return (code or "").lower() in LIVE_DANCE_CODES


def dance_track_for_code(code: str) -> int:
    code = (code or "").lower()
    if code in ("d3", "ld3"):
        return 3
    if code in ("d2", "ld2"):
        return 2
    return 1


_INFER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"quay\s+trái|quẹo\s+trái|sang\s+trái|rẽ\s+trái|đi\s+(?:qua\s+)?trái|(?:^|\s)qua\s+trái",
            re.IGNORECASE,
        ),
        "t",
    ),
    (
        re.compile(
            r"quay\s+phải|quẹo\s+phải|sang\s+phải|rẽ\s+phải|đi\s+(?:qua\s+)?phải|(?:^|\s)qua\s+phải",
            re.IGNORECASE,
        ),
        "p",
    ),
    (re.compile(r"lùi(?:\s+lại)?|đi\s+lùi|quay\s+lại", re.IGNORECASE), "b"),
    (
        re.compile(
            r"đi\s+tới|tiến(?!g)(?:\s+lên)?|đi\s+thẳng|đi\s+lên",
            re.IGNORECASE,
        ),
        "f",
    ),
    (re.compile(r"dừng(?:\s+lại)?", re.IGNORECASE), "s"),
    (
        re.compile(
            r"stream\s*nh[aạ]c|nh[aạ]c\s*stream|live\s*dance|nh[aạ]c\s*từ\s*server|"
            r"phát\s*nh[aạ]c\s*nh[aả]y|bật\s*nh[aạ]c\s*nh[aả]y",
            re.IGNORECASE,
        ),
        "ld",
    ),
    (
        re.compile(
            r"dance\s*3|nhảy\s*3|múa\s*3|mu\s*a\s*3|drill|pirate|cướp\s*biển",
            re.IGNORECASE,
        ),
        "d3",
    ),
    (
        re.compile(
            r"dance\s*2|nhảy\s*2|múa\s*2|mu\s*a\s*2|hip[\s-]?hop",
            re.IGNORECASE,
        ),
        "d2",
    ),
    (
        re.compile(
            r"dance\s*1|nhảy\s*1|múa\s*1|mu\s*a\s*1",
            re.IGNORECASE,
        ),
        "d",
    ),
    (
        re.compile(
            r"nhảy|múa|mu\s*a|dance|lắc\s+lắc|wiggle",
            re.IGNORECASE,
        ),
        "d",
    ),
    (
        re.compile(
            r"đi\s+vòng\s+vòng|di\s+vong\s+vong|quay\s+vòng|quay\s+vong|"
            r"đi\s+vòng|di\s+vong",
            re.IGNORECASE,
        ),
        "c",
    ),
)


@dataclass(frozen=True)
class RobotMoveStep:
    code: str
    duration_sec: int
    song: str | None = None
    # Số "bước" người dùng yêu cầu (1 bước = 1 chu kỳ crawl 4 chân). None = dùng giây.
    steps: int | None = None


def clamp_duration(
    seconds: int | str | None,
    *,
    default_sec: int = DEFAULT_ROBOT_MOVE_DURATION_SEC,
    max_sec: int = MAX_ROBOT_MOVE_DURATION_SEC,
) -> int:
    if seconds is None or seconds == "":
        return default_sec
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return default_sec
    return max(1, min(value, max_sec))


def limit_robot_move_steps(
    steps: list[RobotMoveStep], max_steps: int | None = None
) -> list[RobotMoveStep]:
    cap = max_steps if max_steps is not None else MAX_ROBOT_MOVE_SEQUENCE
    if cap <= 0:
        return []
    return steps[:cap]


def limit_robot_move_codes(
    codes: list[str], max_steps: int | None = None
) -> list[str]:
    return [step.code for step in limit_robot_move_steps(steps_from_codes(codes), max_steps)]


def steps_from_codes(
    codes: list[str], *, default_sec: int = DEFAULT_ROBOT_MOVE_DURATION_SEC
) -> list[RobotMoveStep]:
    return [RobotMoveStep(code=c, duration_sec=default_sec) for c in codes]


def extract_move_steps(
    text: str,
    *,
    default_sec: int = DEFAULT_ROBOT_MOVE_DURATION_SEC,
    max_sec: int = MAX_ROBOT_MOVE_DURATION_SEC,
) -> list[RobotMoveStep]:
    if not text:
        return []
    steps: list[RobotMoveStep] = []
    for match in MOVE_TAG_RE.finditer(text):
        code = match.group(1).lower()
        duration_raw = match.group(2)
        song_raw = match.group(3) or match.group(4)
        song: str | None = None
        if song_raw:
            s = str(song_raw).strip().strip("\"'")
            if s.lower().startswith("song="):
                s = s[5:].strip().strip("\"'")
            if s:
                song = s

        # `mv:f:steps=3` bị regex bắt vào nhóm "song" (vì \"steps=3\" không phải số thuần).
        steps_match = _STEPS_IN_TAG_RE.search(match.group(0))
        move_steps = clamp_move_steps(steps_match.group(1)) if steps_match else None
        if move_steps is None and song and song.lower().startswith("steps="):
            move_steps = clamp_move_steps(song[6:])
        if move_steps is not None:
            song = None

        duration = clamp_duration(
            duration_raw, default_sec=default_sec, max_sec=max_sec
        )
        if code == "s":
            duration = 0
            move_steps = None
        elif code in DANCE_CODES:
            track = dance_track_for_code(code)
            if track == 3:
                duration = DANCE3_MOVE_DURATION_SEC
            elif track == 2:
                duration = DANCE2_MOVE_DURATION_SEC
            else:
                duration = DANCE_MOVE_DURATION_SEC
            move_steps = None
        elif move_steps is not None:
            # Thời gian thật của N bước — dùng cho cooldown/chờ thiết bị, không gửi xuống.
            duration = max(1, round(move_steps * crawl_cycle_ms() / 1000.0))
        steps.append(
            RobotMoveStep(
                code=code, duration_sec=duration, song=song, steps=move_steps
            )
        )
    return steps


def extract_move_codes(text: str) -> list[str]:
    return [step.code for step in extract_move_steps(text)]


def _parse_duration_token(token: str, *, max_sec: int) -> int:
    token = (token or "").strip().lower()
    if token.isdigit():
        return clamp_duration(int(token), max_sec=max_sec)
    if token in _VI_NUMBER_WORDS:
        return clamp_duration(_VI_NUMBER_WORDS[token], max_sec=max_sec)
    return clamp_duration(token, max_sec=max_sec)


def infer_duration_from_text(text: str, *, max_sec: int = MAX_ROBOT_MOVE_DURATION_SEC) -> int | None:
    if not text:
        return None
    match = _DURATION_IN_TEXT_RE.search(text)
    if match:
        return _parse_duration_token(match.group(1), max_sec=max_sec)
    match = _DURATION_VI_SUFFIX_RE.search(text)
    if match:
        return _parse_duration_token(match.group(1), max_sec=max_sec)
    return None


def infer_mv_codes_multi(text: str) -> list[str]:
    if not text or not str(text).strip():
        return []
    t = str(text).strip()
    if _REFUSAL_RE.search(t) or _CAPABILITY_RE.search(t):
        return []
    if re.search(r"\bhoặc\b", t, re.IGNORECASE) and (
        re.search(r"trái", t, re.IGNORECASE) and re.search(r"phải", t, re.IGNORECASE)
    ):
        return []

    hits: list[tuple[int, int, str]] = []
    for pattern, code in _INFER_RULES:
        for match in pattern.finditer(t):
            hits.append((match.start(), match.end(), code))
    hits.sort(key=lambda item: (item[0], -item[1]))

    codes: list[str] = []
    last_end = -1
    for start, end, code in hits:
        if start < last_end:
            continue
        if not codes or codes[-1] != code:
            codes.append(code)
        last_end = end
    return codes


def infer_mv_codes_from_reply(text: str) -> list[str]:
    if not text or not str(text).strip():
        return []
    if extract_move_codes(text):
        return []
    return infer_mv_codes_multi(text)[:1]


def user_requested_robot_move(text: str) -> bool:
    """True when the user turn is asking the robot to move (not general chat)."""
    if not text or not str(text).strip():
        return False
    t = str(text).strip()
    if extract_move_codes(t):
        return True
    if _REFUSAL_RE.search(t) or _CAPABILITY_RE.search(t):
        return False
    return bool(infer_mv_codes_multi(t))


def extract_move_steps_from_assistant_reply(
    text: str,
    *,
    default_sec: int = DEFAULT_ROBOT_MOVE_DURATION_SEC,
    max_sec: int = MAX_ROBOT_MOVE_DURATION_SEC,
    allow_inference: bool = False,
) -> list[RobotMoveStep]:
    """Parse mv:* tags from the assistant reply. Inference is opt-in (legacy)."""
    explicit = extract_move_steps(text, default_sec=default_sec, max_sec=max_sec)
    if explicit or not allow_inference:
        return limit_robot_move_steps(explicit)

    spoken = strip_move_tags(text or "", trim_edges=True)
    inferred_codes = infer_mv_codes_multi(spoken)
    inferred_duration = infer_duration_from_text(spoken, max_sec=max_sec)

    merged: list[RobotMoveStep] = []
    for code in inferred_codes:
        duration = (
            inferred_duration
            if inferred_duration is not None and len(inferred_codes) == 1
            else default_sec
        )
        if code == "s":
            duration = 0
        merged.append(RobotMoveStep(code=code, duration_sec=duration))

    if merged:
        return limit_robot_move_steps(merged)
    fallback_codes = infer_mv_codes_from_reply(text)
    if not fallback_codes:
        return []
    duration = infer_duration_from_text(spoken, max_sec=max_sec)
    if duration is None:
        duration = default_sec
    return [
        RobotMoveStep(
            code=fallback_codes[0],
            duration_sec=0 if fallback_codes[0] == "s" else duration,
        )
    ]


def extract_move_codes_from_assistant_reply(text: str) -> list[str]:
    return [step.code for step in extract_move_steps_from_assistant_reply(text)]


def strip_move_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = MOVE_TAG_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def split_robot_move_tags(
    text: str, *, trim_edges: bool = False
) -> tuple[str, list[str]]:
    steps = extract_move_steps(text)
    return strip_move_tags(text, trim_edges=trim_edges), [step.code for step in steps]


def split_robot_move_steps(
    text: str, *, trim_edges: bool = False
) -> tuple[str, list[RobotMoveStep]]:
    steps = extract_move_steps(text)
    return strip_move_tags(text, trim_edges=trim_edges), steps


def resolve_mcp_tool(code: str, available: set[str] | None = None) -> str | None:
    code = (code or "").lower()
    candidates = MOVE_CODE_TO_MCP.get(code, ())
    if not candidates:
        return None

    def pick(name: str) -> str | None:
        return resolve_named_tool(name, available)

    if available is not None:
        for name in candidates:
            hit = pick(name)
            if hit:
                return hit
        return None
    return candidates[0]


def resolve_motor_move_tool(available: set[str] | None = None) -> str | None:
    for name in MOTOR_MOVE_TOOL_CANDIDATES:
        hit = resolve_named_tool(name, available)
        if hit:
            return hit
    return None


# Giống core.utils.util.sanitize_tool_name — inline để codec không phải import numpy.
_UNSAFE_TOOL_CHARS_RE = re.compile(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]")


def resolve_named_tool(name: str | None, available: set[str] | None = None) -> str | None:
    """Kiểm tra một tool cụ thể (tên trong board profile) có trên thiết bị không."""
    if not name:
        return None
    if available is None:
        return name
    if name in available:
        return name
    sanitized = _UNSAFE_TOOL_CHARS_RE.sub("_", name)
    return sanitized if sanitized in available else None


def build_mcp_call(
    step: RobotMoveStep,
    available: set[str] | None = None,
    *,
    board: str | None = None,
) -> tuple[str | None, dict]:
    """Map mv step → MCP tool name + JSON arguments.

    `board` là tên board thiết bị tự khai (conn.device_board, vd "blue-v4").

    Server CHỈ ra lệnh ở mức "hướng + số bước" (hoặc "hướng + thời gian"); mọi thứ cơ học
    (1 bước = 4 chân/8 servo nhấc-hạ thế nào, biên độ, tốc độ, chiều) do firmware quyết:
      * robot chân + tag `steps=`: `self.gait.walk/turn {direction, steps}`
      * robot chân + tag "giây":  `self.motor.move {left, right, duration_ms}`
        (firmware tự quy duration → số bước bằng hằng số pha của nó)
      * board không có gait (blue-v2): như cũ, chỉ self.motor.*
    """
    code = step.code
    profile = board_tool_profile(board)
    if code == "s":
        return resolve_named_tool(profile.get("stop") or "self.motor.stop", available), {}

    if code in DANCE_CODES:
        track = dance_track_for_code(code)
        args: dict = {"track": track}
        want = profile.get("dance") or "self.motor.dance"
        return (
            resolve_named_tool(want, available) or resolve_mcp_tool("d", available),
            args,
        )

    # --- Robot chân + người dùng đếm BƯỚC: chuyển nguyên số bước cho firmware ---
    if step.steps and code in MOVE_STEPS_TO_GAIT:
        gait_tool, direction = MOVE_STEPS_TO_GAIT[code]
        role = "walk" if gait_tool == GAIT_WALK_TOOL else "turn"
        want = profile.get(role) or gait_tool
        resolved = resolve_named_tool(want, available)
        if resolved:
            return resolved, {"direction": int(direction), "steps": int(step.steps)}

    duration_ms = (
        step.duration_sec * 1000
        if step.duration_sec > 0
        else DEFAULT_ROBOT_MOVE_DURATION_SEC * 1000
    )

    circle_tool = (
        resolve_named_tool(profile.get("circle") or "self.motor.circle", available)
        if code == "c"
        else None
    )
    if circle_tool and step.duration_sec > 0:
        return circle_tool, {"duration_ms": duration_ms}

    speeds = MOVE_WHEEL_SPEEDS.get(code)
    move_tool = resolve_named_tool(profile.get("move") or "self.motor.move", available)
    if move_tool is None:
        move_tool = resolve_motor_move_tool(available)

    if move_tool and speeds is not None and step.duration_sec > 0:
        left, right = speeds
        return move_tool, {
            "left": left,
            "right": right,
            "duration_ms": duration_ms,
        }

    tool = resolve_mcp_tool(code, available)
    if tool and step.duration_sec > 0:
        return tool, {"duration_ms": duration_ms}
    return tool, {}


def format_move_step(step: RobotMoveStep) -> str:
    if step.code in DANCE_CODES:
        if step.song:
            return f"{step.code}:song={step.song}"
        return step.code
    if step.code == "s" or step.duration_sec <= 0:
        return step.code
    if step.steps:
        return f"{step.code}:steps={step.steps}"
    return f"{step.code}:{step.duration_sec}"


def prepare_stream_chunk_for_tts(
    text: str,
    *,
    default_sec: int = DEFAULT_ROBOT_MOVE_DURATION_SEC,
    max_sec: int = MAX_ROBOT_MOVE_DURATION_SEC,
) -> tuple[str, str, list[RobotMoveStep]]:
    if not text:
        return "", "", []

    hold = ""
    work = text
    m = _INCOMPLETE_MOVE_SUFFIX_RE.search(work)
    if m and m.group(0).strip():
        hold = work[m.start() :]
        work = work[: m.start()]

    cleaned = strip_move_tags(work, trim_edges=False)
    steps = extract_move_steps(work, default_sec=default_sec, max_sec=max_sec)
    return cleaned, hold, steps


def finalize_stream_text_for_tts(
    text: str,
    *,
    default_sec: int = DEFAULT_ROBOT_MOVE_DURATION_SEC,
    max_sec: int = MAX_ROBOT_MOVE_DURATION_SEC,
    allow_inference: bool = False,
) -> tuple[str, list[RobotMoveStep]]:
    cleaned = strip_move_tags(text or "", trim_edges=False)
    steps = extract_move_steps(text or "", default_sec=default_sec, max_sec=max_sec)
    if not steps:
        steps = extract_move_steps_from_assistant_reply(
            cleaned,
            default_sec=default_sec,
            max_sec=max_sec,
            allow_inference=allow_inference,
        )
    return cleaned, steps
