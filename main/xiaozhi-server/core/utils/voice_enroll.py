"""Voice-enrollment / identity flow for multi-user voice recognition.

Handles two conversational flows, hooked from ``startToChat``:

1. **Admin re-sample** — the admin says "tái lập mẫu giọng nói admin", confirms
   the password ("abcd1234") by voice, then speaks a sample to re-register the
   admin voiceprint.
2. **New-user onboarding** — started ONLY when the admin asks to add a user
   and the LLM appends the ``vpr:enroll[:<name>]`` tag (see
   core/utils/voiceprint_tag_codec.py). The robot asks for the name (or uses the
   name carried by the tag), then samples the new user's voice and registers it
   under that name (reserved names like "Mr Blue" are rejected). An
   unrecognized voice on its own is ignored — no auto-onboarding.

The current utterance's audio is captured on the connection as
``conn._last_voice_wav`` by the ASR layer.
"""

from __future__ import annotations

import io
import re
import uuid
import wave
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

_UNKNOWN_SPEAKER = "未知说话人"

# Minimum sample length (ms) to accept a voice sample. Shorter samples give a
# weak embedding and cause misidentification — ask the user to repeat longer.
MIN_SAMPLE_MS = 3000

# NOTE: the admin re-sample flow is now triggered by the LLM-appended
# ``vpr:resample`` tag (see core/utils/voiceprint_tag_codec.py), NOT by matching
# the spoken phrase. This handler only drives the password/sample state machine.


def _wav_duration_ms(wav_bytes: bytes) -> int:
    """Estimate WAV duration in ms from raw WAV bytes (16k mono 16-bit)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            if rate > 0:
                return int(wf.getnframes() * 1000 / rate)
    except Exception:
        pass
    return 0

_NAME_CLEAN_RE = re.compile(r"[^\w\s\u00C0-\u1EF9]", flags=re.UNICODE)


def get_enroll_state(conn: "ConnectionHandler") -> Optional[dict]:
    return getattr(conn, "_voice_enroll_state", None)


def set_enroll_state(conn: "ConnectionHandler", state: Optional[dict]) -> None:
    conn._voice_enroll_state = state


def clear_enroll_state(conn: "ConnectionHandler") -> None:
    conn._voice_enroll_state = None


def _normalize(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip()).lower()


# Vietnamese number words → digits, so a spoken password like
# "ABCD một hai ba bốn" is normalized to "abcd1234".
_VI_NUM_WORDS = {
    "không": "0", "một": "1", "mot": "1", "mốt": "1",
    "hai": "2", "ba": "3", "bốn": "4", "bon": "4", "bố": "4",
    "năm": "5", "nam": "5", "lăm": "5",
    "sáu": "6", "sau": "6", "bảy": "7", "bay": "7", "bẩy": "7",
    "tám": "8", "tam": "8", "chín": "9", "chin": "9",
    "mười": "10", "muoi": "10",
}


def _password_matches(text: str, password: str) -> bool:
    """Lenient password match.

    - case-insensitive
    - all whitespace removed (ASR inserts spaces between letters/words)
    - Vietnamese number words converted to digits ("một hai ba bốn" -> "1234")
    """
    if not text or not password:
        return False

    def normalize(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"\s+", "", s)
        for word, digit in _VI_NUM_WORDS.items():
            s = s.replace(word, digit)
        return s

    return normalize(text) == normalize(password)


# Guard: detect the admin re-sample request. "mẫu" is optional on purpose
# (ASR hears "tái lập giọng nói admin" / "phải lập giọng nói admin" / "bài lập
# mẫu giọng nói admin"), so we key on the core phrase "lập (mẫu )?giọng nói admin".
_ADMIN_REENROLL_RE = re.compile(
    r"lập\s+(?:mẫu\s+)?giọng\s+nói\s+admin"
    r"|lap\s+(?:mau\s+)?giong\s+noi\s+admin"
    r"|re[- ]?sample\s+(?:the\s+)?admin\s+voice"
    r"|re[- ]?setup\s+(?:the\s+)?admin\s+voice",
    re.IGNORECASE,
)


def is_admin_reenroll_request(text: str) -> bool:
    return bool(text and _ADMIN_REENROLL_RE.search(text))


_QUESTION_WORDS = frozenset({
    "ai", "gì", "gi", "sao", "nào", "nao", "đâu", "dau", "hả", "ha",
    "chứ", "chu", "không", "khong", "có biết", "co biet", "thế nào",
    "the nao", "tại sao", "tai sao", "như thế nào", "nhu the nao",
})

# The user's NAME is decided by the LLM (it reads the admin's request and emits
# ``vpr:enroll:<name>``, e.g. "bạn Lucy muốn làm quen" -> ``vpr:enroll:Lucy``).
# We do NOT hardcode word lists here — _clean_name only applies light structural
# checks (length / word count / question words) so a full sentence is never
# registered as a name.
def _clean_name(text: str) -> str:
    if not text:
        return ""
    cleaned = _NAME_CLEAN_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # A name is short (2..20 chars, at most 4 words). Reject full sentences so
    # command-like speech or questions are never registered as a user name.
    if len(cleaned) < 2 or len(cleaned) > 20:
        return ""
    words = cleaned.lower().split()
    if len(words) > 4:
        return ""
    if any(w in _QUESTION_WORDS for w in words):
        return ""
    return cleaned


def _make_speaker_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug[:32] or f"user_{uuid.uuid4().hex[:8]}"


def _speak(conn: "ConnectionHandler", text: str) -> None:
    conn.client_abort = False
    conn.sentence_id = str(uuid.uuid4().hex)
    from core.handle.intentHandler import speak_txt

    speak_txt(conn, text)


async def handle_voice_enroll_turn(
    conn: "ConnectionHandler",
    speaker_name: Optional[str],
    text: str,
) -> bool:
    """Consume a turn for voice enrollment/identity. Returns True if handled."""
    provider = getattr(conn, "voiceprint_provider", None)
    store = getattr(conn, "voice_user_store", None)
    if not provider or not getattr(provider, "enabled", False) or not store:
        return False
    # Feature flag: when voiceprint.enroll_enabled is false, the whole
    # multi-user / admin voice feature is off (legacy behavior).
    if not getattr(store, "enroll_enabled", False):
        return False

    state = get_enroll_state(conn)
    text = text or ""

    # Only the ADMIN (or a reserved name) must never be enrolled as a new
    # user — cancel in-progress enrollment if they speak. A REGULAR known user
    # (e.g. voiceprint says "Anna" while admin wants to enroll "Bông") must
    # NOT cancel: the admin explicitly asked to register this person under the
    # requested name, even if their voice matches an existing user.
    if speaker_name and speaker_name != _UNKNOWN_SPEAKER:
        if store.is_reserved_name(speaker_name) or store.is_admin_name(speaker_name):
            if state and state.get("stage") in ("ask_name", "sample"):
                clear_enroll_state(conn)
                logger = getattr(conn, "logger", None)
                if logger:
                    logger.bind(tag="voice_enroll").info(
                        f"[enroll] cancelled enrollment — reserved/admin speaker "
                        f"{speaker_name!r}"
                    )
                return False

    # --- Active enrollment state machine ---
    # (started by the LLM-appended ``vpr:resample`` tag, see voiceprint_tag_codec)
    if state:
        stage = state.get("stage")

        if stage == "ask_password":
            if _password_matches(text, store.admin_password):
                set_enroll_state(conn, {"stage": "admin_sample"})
                _speak(
                    conn,
                    "Mật khẩu chính xác. Vui lòng đọc lại một đoạn dài "
                    "khoảng 5 giây, ví dụ: \"Hôm nay trời thật đẹp, mình đi "
                    "dạo và hát một bài thật vui\", để lấy mẫu giọng nói "
                    "của admin.",
                )
            else:
                clear_enroll_state(conn)
                _speak(conn, "Mật khẩu không đúng. Đã hủy thao tác tái lập giọng.")
            return True

        if stage == "admin_sample":
            wav = getattr(conn, "_last_voice_wav", None)
            if not wav or _wav_duration_ms(wav) < MIN_SAMPLE_MS:
                _speak(
                    conn,
                    "Mẫu giọng hơi ngắn, nhận diện sẽ không chính xác. "
                    "Vui lòng đọc lại một đoạn dài hơn, khoảng 5 giây nhé.",
                )
                return True
            ok = await provider.register_speaker(store.admin_speaker_id, wav)
            if ok:
                provider.add_speaker(
                    store.admin_speaker_id, store.admin_name, "Admin (Mr Blue)"
                )
                store.add_user(
                    store.admin_name,
                    store.admin_speaker_id,
                    is_admin=True,
                    description="Admin (Mr Blue)",
                )
                clear_enroll_state(conn)
                _speak(
                    conn,
                    f"Đã cập nhật mẫu giọng nói của {store.admin_name}. "
                    "Cảm ơn bạn!",
                )
            else:
                clear_enroll_state(conn)
                _speak(conn, "Lưu mẫu giọng nói thất bại. Vui lòng thử lại sau.")
            return True

        if stage == "ask_name":
            # If the user suddenly asks to re-sample the admin voice, abandon the
            # name flow so the LLM can handle it (vpr:resample tag).
            if is_admin_reenroll_request(text):
                clear_enroll_state(conn)
                return False
            name = _clean_name(text)
            if not name:
                _speak(conn, "Mình chưa nghe rõ tên. Bạn nói lại tên của bạn nhé?")
                return True
            if store.is_reserved_name(name):
                _speak(
                    conn,
                    f"Tên \"{name}\" không thể sử dụng. "
                    "Bạn hãy chọn một tên khác nhé.",
                )
                return True
            state["pending_name"] = name
            state["stage"] = "sample"
            set_enroll_state(conn, state)
            _speak(
                conn,
                f"Cảm ơn {name}! Vui lòng đọc lại một đoạn dài hơn để mình "
                f"lưu giọng nói chính xác, ví dụ: \"Xin chào mọi người, mình "
                f"tên là {name}. Hôm nay trời đẹp quá, mình rất vui được nói "
                f"chuyện với bạn.\"",
            )
            return True

        if stage == "sample":
            name = state.get("pending_name") or ""
            wav = getattr(conn, "_last_voice_wav", None)
            if not wav or _wav_duration_ms(wav) < MIN_SAMPLE_MS:
                _speak(
                    conn,
                    "Mẫu giọng hơi ngắn, nhận diện sẽ không chính xác. "
                    f"{name} ơi, vui lòng đọc lại một đoạn dài hơn, "
                    "khoảng 5 giây nhé.",
                )
                return True
            speaker_id = _make_speaker_id(name)
            ok = await provider.register_speaker(speaker_id, wav)
            if ok and store.add_user(name, speaker_id):
                provider.add_speaker(speaker_id, name, "")
                clear_enroll_state(conn)
                _speak(
                    conn,
                    f"Đã lưu giọng nói của {name}. Rất vui được gặp bạn!",
                )
            else:
                clear_enroll_state(conn)
                _speak(conn, "Lưu giọng nói thất bại. Vui lòng thử lại sau.")
            return True

    # --- Guard: admin re-sample request must reach the LLM (never onboarding),
    # so the LLM can append the vpr:resample tag. ---
    if is_admin_reenroll_request(text):
        return False

    # --- Unknown voice -> ignore by default ---
    # A new/unrecognized voice does NOT start enrollment on its own. The flow is
    # started ONLY when the admin asks to add a user and the LLM appends the
    # ``vpr:enroll[:<name>]`` tag (see core/utils/voiceprint_tag_codec.py).
    return False
