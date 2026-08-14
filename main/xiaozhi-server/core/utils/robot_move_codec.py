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
| d    | dance         | self.motor.dance          |
| s    | stop          | self.motor.stop           |

Duration (seconds): optional suffix ``:N`` after code — default 5, max 30.
``mv:d`` runs a fixed ~8 s routine; optional ``:N`` is ignored (server cooldown only).
Server prefers ``self.motor.move`` with ``duration_ms`` when available.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ROBOT_MOVE_SEQUENCE = 5
DEFAULT_ROBOT_MOVE_DURATION_SEC = 5
MAX_ROBOT_MOVE_DURATION_SEC = 30
DANCE_MOVE_DURATION_SEC = 24

import re

# mv:t  mv:t:10  Kita mv:f:5
MOVE_TAG_RE = re.compile(
    r"(?:\bKita\s+)?mv\s*:\s*([tprfbsdc])(?:\s*:\s*(\d+))?\b", re.IGNORECASE
)
MOVE_TAG_STRIP_RE = re.compile(
    r"(?:\bKita\s+)?mv\s*:\s*[tprfbsdc](?:\s*:\s*\d+)?\b", re.IGNORECASE
)

_INCOMPLETE_MOVE_SUFFIX_RE = re.compile(
    r"(?:\s+(?:Kita\s+)?(?:m(?:v(?:\s*:\s*[tprfbsd]?(?:\s*:\s*\d{0,2})?)?)?)?)$",
    re.IGNORECASE,
)

MOVE_CODE_TO_MCP: dict[str, tuple[str, ...]] = {
    "t": ("self.motor.turn_left", "self.chassis.turn_left"),
    "p": ("self.motor.turn_right", "self.chassis.turn_right"),
    "f": ("self.motor.forward", "self.chassis.go_forward"),
    "b": ("self.motor.backward", "self.chassis.go_back"),
    "c": ("self.motor.circle",),
    "d": ("self.motor.dance", "self.chassis.dance"),
    "s": ("self.motor.stop",),
}

MOVE_WHEEL_SPEEDS: dict[str, tuple[int, int]] = {
    "t": (-70, 70),
    "p": (70, -70),
    "f": (100, 100),
    "b": (-100, -100),
    "c": (50, 100),
}

MOTOR_MOVE_TOOL_CANDIDATES: tuple[str, ...] = ("self.motor.move",)

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
        duration = clamp_duration(
            match.group(2), default_sec=default_sec, max_sec=max_sec
        )
        if code == "s":
            duration = 0
        elif code == "d":
            duration = DANCE_MOVE_DURATION_SEC
        steps.append(RobotMoveStep(code=code, duration_sec=duration))
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
    from core.utils.util import sanitize_tool_name

    code = (code or "").lower()
    candidates = MOVE_CODE_TO_MCP.get(code, ())
    if not candidates:
        return None

    def pick(name: str) -> str | None:
        if available is None:
            return name
        if name in available:
            return name
        sanitized = sanitize_tool_name(name)
        if sanitized in available:
            return sanitized
        return None

    if available is not None:
        for name in candidates:
            hit = pick(name)
            if hit:
                return hit
        return None
    return candidates[0]


def resolve_motor_move_tool(available: set[str] | None = None) -> str | None:
    from core.utils.util import sanitize_tool_name

    for name in MOTOR_MOVE_TOOL_CANDIDATES:
        if available is None:
            return name
        if name in available:
            return name
        sanitized = sanitize_tool_name(name)
        if sanitized in available:
            return sanitized
    return None


def build_mcp_call(
    step: RobotMoveStep, available: set[str] | None = None
) -> tuple[str | None, dict]:
    """Map mv step → MCP tool name + JSON arguments."""
    code = step.code
    if code == "s":
        return resolve_mcp_tool("s", available), {}

    if code == "d":
        return resolve_mcp_tool("d", available), {}

    duration_ms = (
        step.duration_sec * 1000
        if step.duration_sec > 0
        else DEFAULT_ROBOT_MOVE_DURATION_SEC * 1000
    )

    circle_tool = resolve_mcp_tool("c", available) if code == "c" else None
    if circle_tool == "self.motor.circle" and step.duration_sec > 0:
        return circle_tool, {"duration_ms": duration_ms}

    speeds = MOVE_WHEEL_SPEEDS.get(code)
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
    if step.code in ("s", "d") or step.duration_sec <= 0:
        return step.code
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
