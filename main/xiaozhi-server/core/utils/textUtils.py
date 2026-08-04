import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
EMOJI_MAP = {
    "😂": "funny",
    "😭": "crying",
    "😠": "angry",
    "😔": "sad",
    "😍": "loving",
    "😲": "surprised",
    "😱": "shocked",
    "🤔": "thinking",
    "😌": "relaxed",
    "😴": "sleepy",
    "😜": "silly",
    "🙄": "confused",
    "😶": "neutral",
    "🙂": "happy",
    "😆": "laughing",
    "😳": "embarrassed",
    "😉": "winking",
    "😎": "cool",
    "🤤": "delicious",
    "😘": "kissy",
    "😏": "confident",
}
EMOJI_RANGES = [
    (0x1F600, 0x1F64F),
    (0x1F300, 0x1F5FF),
    (0x1F680, 0x1F6FF),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
]


def get_string_no_punctuation_or_emoji(s):
    """去除字符串首尾的标点符号和表情符号（保留空格，避免流式分片丢字间距）"""
    chars = list(s)
    # 处理开头的字符
    start = 0
    while start < len(chars) and is_punctuation_or_emoji(chars[start]):
        start += 1
    # 处理结尾的字符
    end = len(chars) - 1
    while end >= start and is_punctuation_or_emoji(chars[end]):
        end -= 1
    return "".join(chars[start : end + 1])


def needs_stream_space_between(prev: str, nxt: str) -> bool:
    """Deprecated: auto-insert caused false splits like M + ình. Use normalize_vietnamese_tts_text."""
    return False


def _collapse_single_letter_prefix(prev: str, nxt: str) -> tuple[str, str]:
    """M + ình / M + ' ình' / 'M ' + ình → M + ình (no spurious space)."""
    prev_core = prev.rstrip()
    if len(prev_core) == 1 and prev_core.isalpha():
        if prev.endswith(" ") or nxt.startswith(" "):
            nxt = nxt.lstrip(" ")
        prev = prev_core
    return prev, nxt


def join_stream_text_chunks(prev: str, nxt: str) -> str:
    """Join streaming TTS chunks; never insert spaces (fixes M ình regression)."""
    if not prev:
        return nxt or ""
    if not nxt:
        return prev
    prev, nxt = _collapse_single_letter_prefix(prev, nxt)
    return prev + nxt


# Stream/LLM glues syllables; Edge TTS breaks on spurious single-letter splits.
_VI_TTS_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bM\s+ình\b", re.IGNORECASE), "Mình"),
    (re.compile(r"\bT\s+ôi\b", re.IGNORECASE), "Tôi"),
    (re.compile(r"\bE\s+m\b", re.IGNORECASE), "Em"),
    (re.compile(r"\bA\s+nh\b", re.IGNORECASE), "Anh"),
    (re.compile(r"\bC\s+ảm\s+ơn\b", re.IGNORECASE), "Cảm ơn"),
    (re.compile(r"\bCảmơn\b", re.IGNORECASE), "Cảm ơn"),
    (re.compile(r"\bXin\s+ơn\b", re.IGNORECASE), "Xin ơn"),
    (re.compile(r"\bXinơn\b", re.IGNORECASE), "Xin ơn"),
)


def normalize_vietnamese_tts_text(text: str) -> str:
    """Fix common Vietnamese spacing artifacts before TTS."""
    if not text:
        return text
    for pattern, replacement in _VI_TTS_FIXES:
        text = pattern.sub(replacement, text)
    return text


def is_punctuation_or_emoji(char):
    """检查字符是否为指定标点或表情符号（不含空格）"""
    # 定义需要去除的中英文标点（包括全角/半角）
    punctuation_set = {
        "，",
        ",",  # 中文逗号 + 英文逗号
        "。",
        ".",  # 中文句号 + 英文句号
        "！",
        "!",  # 中文感叹号 + 英文感叹号
        "“",
        "”",
        '"',  # 中文双引号 + 英文引号
        "：",
        ":",  # 中文冒号 + 英文冒号
        "-",
        "－",  # 英文连字符 + 中文全角横线
        "、",  # 中文顿号
        "[",
        "]",  # 方括号
        "【",
        "】",  # 中文方括号
    }
    if char in punctuation_set:
        return True
    return is_emoji(char)


async def get_emotion(conn: "ConnectionHandler", text):
    """获取文本内的情绪消息"""
    from core.characters.character_registry import get_active_character

    emoji = "🙂"
    emotion = "happy"
    for char in text:
        if char in EMOJI_MAP:
            emoji = char
            emotion = EMOJI_MAP[char]
            break
    try:
        await conn.websocket.send(
            json.dumps(
                {
                    "type": "llm",
                    "text": emoji,
                    "emotion": emotion,
                    "session_id": conn.session_id,
                }
            )
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"发送情绪表情失败，错误:{e}")

    if get_active_character(conn):
        await send_character_behavior(conn, emotion)

    return


async def send_character_behavior(conn: "ConnectionHandler", emotion: str | None):
    """Behavior Engine → client (Live2D / future motor)."""
    from core.characters.character_registry import get_active_character, plan_behaviors

    character = get_active_character(conn) or "kira"
    user_text = getattr(conn, "_last_user_text", "") or getattr(
        conn, "_kira_last_user_text", ""
    )
    actions = plan_behaviors(character, user_text, emotion)
    if not actions:
        return
    try:
        await conn.websocket.send(
            json.dumps(
                {
                    "type": "behavior",
                    "actions": actions,
                    "session_id": conn.session_id,
                }
            )
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"发送行为消息失败，错误:{e}")


def is_emoji(char):
    """检查字符是否为emoji表情"""
    code_point = ord(char)
    return any(start <= code_point <= end for start, end in EMOJI_RANGES)


def check_emoji(text):
    """去除文本中的所有emoji表情"""
    return "".join(char for char in text if not is_emoji(char) and char != "\n")
