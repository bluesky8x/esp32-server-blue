"""Compact robot posture tags embedded in LLM text (stripped before TTS).

Protocol: ``pst:<code>[:<deg>]`` appended **at end of reply**, e.g.
``Mình nghiêng sang trái chút nha pst:lt:15`` — never ``Mình pst:lt rồi nha``.

| Code | Ý nghĩa          | Device MCP (primary)                    |
|------|------------------|-----------------------------------------|
| std  | đứng / stand     | self.gait.stand                         |
| sit  | ngồi / sit       | self.gait.sit                           |
| lt   | nghiêng trái     | self.gait.body {roll_deg: +deg}         |
| rt   | nghiêng phải     | self.gait.body {roll_deg: -deg}         |
| fw   | nghiêng trước    | self.gait.body {pitch_deg: +deg}        |
| bw   | nghiêng sau      | self.gait.body {pitch_deg: -deg}        |

``deg`` is optional (5..20, default 12) and only applies to the four tilt codes.

Design rules (same as ``robot_move_codec``):
* the LLM decides — the server only parses tags;
* **no server fallback**: if the device exposes no ``self.gait.*`` tool the tag is
  reported and dropped, never emulated by other tools;
* everything is gated by ``robot_posture.enable`` in ``config.yaml`` (default OFF).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_TILT_DEG = 12
MIN_TILT_DEG = 5
MAX_TILT_DEG = 20

POSTURE_CODES: tuple[str, ...] = ("std", "sit", "lt", "rt", "fw", "bw")
TILT_CODES: tuple[str, ...] = ("lt", "rt", "fw", "bw")

import re

# pst:std  pst:sit  pst:lt  pst:lt:15  Kita pst:rt:10
POSTURE_TAG_RE = re.compile(
    r"(?:\bKita\s+)?pst\s*:\s*(std|sit|lt|rt|fw|bw)(?:\s*:\s*(\d{1,2}))?"
    r"(?=(?:\s+pst:|\s*$|[.,!?]))",
    re.IGNORECASE,
)
POSTURE_TAG_STRIP_RE = re.compile(
    r"(?:\bKita\s+)?pst\s*:\s*(?:std|sit|lt|rt|fw|bw)(?:\s*:\s*\d{1,2})?"
    r"(?=(?:\s+pst:|\s*$|[.,!?]))",
    re.IGNORECASE,
)
# Trailing partial tag while streaming ("... pst:l") — held back, never spoken.
_INCOMPLETE_POSTURE_SUFFIX_RE = re.compile(
    r"(?:\s+(?:Kita\s+)?(?:p|ps|pst|pst\s*:"
    r"|pst\s*:\s*(?:s|st|si|sit|l|lt|r|rt|f|fw|b|bw)?(?:\s*:\s*\d{0,2})?))$",
    re.IGNORECASE,
)

POSTURE_CODE_TO_MCP: dict[str, str] = {
    "std": "self.gait.stand",
    "sit": "self.gait.sit",
    "lt": "self.gait.body",
    "rt": "self.gait.body",
    "fw": "self.gait.body",
    "bw": "self.gait.body",
}


@dataclass
class PostureStep:
    code: str
    deg: int = 0  # 0 = use the firmware default / not applicable

    def __post_init__(self) -> None:
        self.code = str(self.code).strip().lower()
        if self.code in TILT_CODES:
            self.deg = clamp_tilt_deg(self.deg)
        else:
            self.deg = 0


def clamp_tilt_deg(deg: object, *, default: int = DEFAULT_TILT_DEG) -> int:
    """Clamp a tilt angle into the safe 5..20 deg window (invalid -> default)."""
    try:
        value = int(str(deg).strip())
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return max(MIN_TILT_DEG, min(MAX_TILT_DEG, value))


def extract_posture_steps(text: str) -> list[PostureStep]:
    """Return every ``pst:`` tag found in the assistant text, in order."""
    if not text:
        return []
    steps: list[PostureStep] = []
    for match in POSTURE_TAG_RE.finditer(text):
        code = match.group(1).lower()
        deg = clamp_tilt_deg(match.group(2)) if code in TILT_CODES else 0
        steps.append(PostureStep(code=code, deg=deg))
    return steps


def strip_posture_tags(text: str, *, trim_edges: bool = False) -> str:
    """Remove posture tags from text (used before TTS)."""
    if not text:
        return ""
    cleaned = POSTURE_TAG_STRIP_RE.sub("", str(text))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned


def hold_incomplete_posture_suffix(text: str) -> str:
    """Suffix of a chunk that may become a posture tag — hold it back while streaming."""
    if not text:
        return ""
    match = _INCOMPLETE_POSTURE_SUFFIX_RE.search(text)
    if match and match.group(0).strip():
        return match.group(0)
    return ""


def prepare_stream_chunk_for_tts(text: str) -> tuple[str, str, list[PostureStep]]:
    """Split a stream chunk into (spoken text, held suffix, posture steps)."""
    if not text:
        return "", "", []
    hold = hold_incomplete_posture_suffix(text)
    work = text[: len(text) - len(hold)] if hold else text
    cleaned = strip_posture_tags(work, trim_edges=False)
    steps = extract_posture_steps(work)
    return cleaned, hold, steps


def format_posture_step(step: PostureStep) -> str:
    if step.code in TILT_CODES and step.deg:
        return f"{step.code}:{step.deg}"
    return step.code


# Device tool lists may hold sanitised spellings (same rule as core.utils.util) — inlined here so
# the codec stays import-light (core.utils.util pulls in numpy).
_UNSAFE_TOOL_CHARS_RE = re.compile(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]")


def resolve_posture_tool(tool_name: str, available: set[str] | None = None) -> str | None:
    """Resolve the device tool name, tolerating the sanitiser's spelling."""
    if not tool_name:
        return None
    if not available:
        return tool_name
    if tool_name in available:
        return tool_name
    sanitized = _UNSAFE_TOOL_CHARS_RE.sub("_", tool_name)
    if sanitized in available:
        return sanitized
    return None


def build_posture_mcp_call(
    step: PostureStep, available: set[str] | None = None
) -> tuple[str | None, dict]:
    """Map a posture step -> MCP tool name + JSON arguments.

    Returns ``(None, {})`` when the device does not expose the tool: the tag path is
    strictly device-driven, so the caller must log and drop it instead of falling back.
    """
    tool = resolve_posture_tool(POSTURE_CODE_TO_MCP.get(step.code, ""), available)
    if not tool:
        return None, {}
    if step.code == "lt":
        return tool, {"roll_deg": abs(step.deg or DEFAULT_TILT_DEG)}
    if step.code == "rt":
        return tool, {"roll_deg": -abs(step.deg or DEFAULT_TILT_DEG)}
    if step.code == "fw":
        return tool, {"pitch_deg": abs(step.deg or DEFAULT_TILT_DEG)}
    if step.code == "bw":
        return tool, {"pitch_deg": -abs(step.deg or DEFAULT_TILT_DEG)}
    return tool, {}


def posture_enabled(config: dict | None) -> bool:
    """Feature flag helper: ``robot_posture.enable`` (default OFF)."""
    section = (config or {}).get("robot_posture") or {}
    return bool(section.get("enable", False))
