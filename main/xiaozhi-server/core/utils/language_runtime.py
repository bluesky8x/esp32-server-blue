"""Per-connection locale (vi / en) for ASR, TTS, and text normalization."""

from __future__ import annotations

import re
from typing import Any

TAG = "language_runtime"

SUPPORTED_LOCALES = frozenset({"vi", "en"})

_VI_DIACRITIC_RE = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)
_VI_HINT_RE = re.compile(
    r"\b("
    r"mình|minh|bạn|ban|tôi|toi|em|anh|chị|chi|nha|không|khong|"
    r"được|duoc|quay|quẹo|dừng|dung|trái|trai|phải|phai|"
    r"tiếng|tieng|giây|giay|ơi|lili|kira|coka"
    r")\b",
    re.IGNORECASE,
)
# ASR hallucinations — not vi/en user speech; do not switch locale to English.
_THAI_SCRIPT_RE = re.compile(r"[\u0E00-\u0E7F]")
_CYRILLIC_SCRIPT_RE = re.compile(r"[\u0400-\u04FF]")
_CJK_SCRIPT_RE = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
_FORCE_EN_RE = re.compile(
    r"(?:"
    r"\b(?:speak|talk|reply|respond|use|switch to)\s+(?:in\s+)?english\b|"
    r"\benglish please\b|"
    r"nói tiếng anh|noi tieng anh|tiếng anh nha|tieng anh nha"
    r")",
    re.IGNORECASE,
)
_FORCE_VI_RE = re.compile(
    r"(?:"
    r"\b(?:speak|talk|reply|respond|use|switch to)\s+(?:in\s+)?vietnamese\b|"
    r"nói tiếng việt|noi tieng viet|tiếng việt nha|tieng viet nha"
    r")",
    re.IGNORECASE,
)

_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "vi": {
        "asr_language": "vi",
        "asr_prompt": (
            "Vietnamese (tiếng Việt) or English only. Not Thai. "
            "Use Vietnamese diacritics when the speaker uses Vietnamese. "
            "Omit fillers: um, uh, ừ, ờ. "
            "Do not transcribe background music, song lyrics, TV, or noise. "
            "If only music/noise is heard, return empty."
        ),
        "tts_voice": "vi-VN-HoaiMyNeural",
        "tts_language_label": "越南语",
        "normalize_vietnamese_tts": True,
        "system_error_response": (
            "Xin lỗi, mình bị gián đoạn một chút. Bạn nói lại giúp mình nhé?"
        ),
        "llm_reply_directive": (
            "ACTIVE LOCALE: Vietnamese. Reply in the language the user wants your content in — "
            "normally Vietnamese with proper diacritics. If the user speaks Vietnamese but asks you "
            "to tell/say/read/sing/translate something in English (e.g. \"kể chuyện tiếng Anh\"), "
            "reply in English.\n"
            "Language tag (REQUIRED for English replies): put the tag FIRST at the very start of ANY "
            "reply that is in English, so the English voice is used:\n"
            "  [locale=en] <English reply>\n"
            "Example: user says \"Bạn hãy kể câu chuyện tiếng Anh\" → you MUST reply "
            "\"[locale=en] Once upon a time...\" (the tag is stripped before speech).\n"
            "Vietnamese replies need no tag."
        ),
    },
    "en": {
        "asr_language": "en",
        "asr_prompt": (
            "English or Vietnamese only. Latin script. Not Thai, not Cyrillic. "
            "Omit filler sounds: um, uh, er. "
            "Do not transcribe background music, song lyrics, TV, or noise. "
            "If only music or noise is heard, return empty."
        ),
        "tts_voice": "en-US-JennyNeural",
        "tts_language_label": "English",
        "normalize_vietnamese_tts": False,
        "system_error_response": (
            "Sorry — I lost that for a second. Could you say it again?"
        ),
        "llm_reply_directive": (
            "ACTIVE LOCALE: English. The user is speaking English right now. "
            "Reply entirely in English for this turn, even if earlier turns were Vietnamese. "
            "Do NOT use Vietnamese words or catchphrases "
            "(e.g. say 'One moment' instead of 'Chờ mình chút nhé').\n"
            "Language tag: put a tag FIRST at the very start of your reply so the English voice"
            " is used: [locale=en] <English reply>. The tag is stripped before speech."
        ),
    },
}


def default_locale(config: dict | None) -> str:
    if not config:
        return "vi"
    runtime = config.get("language_runtime") or {}
    locale = str(runtime.get("default_locale") or "vi").lower()
    return locale if locale in SUPPORTED_LOCALES else "vi"


def get_locale_profile(config: dict | None, locale: str) -> dict[str, Any]:
    locale = (locale or "vi").lower()
    if locale not in SUPPORTED_LOCALES:
        locale = "vi"
    base = dict(_DEFAULT_PROFILES.get(locale, _DEFAULT_PROFILES["vi"]))
    if config:
        overrides = (config.get("language_runtime") or {}).get("locales") or {}
        custom = overrides.get(locale) or {}
        if isinstance(custom, dict):
            base.update({k: v for k, v in custom.items() if v is not None})
    return base


def has_unsupported_script(text: str) -> bool:
    """Thai / Cyrillic / CJK — common ASR noise when locale is vi or en."""
    if not text:
        return False
    t = str(text).strip()
    return bool(
        _THAI_SCRIPT_RE.search(t)
        or _CYRILLIC_SCRIPT_RE.search(t)
        or _CJK_SCRIPT_RE.search(t)
    )


def is_unintelligible_asr(text: str) -> bool:
    """Garbage transcript — skip LLM rather than hallucinate a reply."""
    if not text or not str(text).strip():
        return True
    t = str(text).strip()
    if has_unsupported_script(t):
        return True
    if len(t) <= 2 and not _VI_DIACRITIC_RE.search(t):
        letters = [c for c in t if c.isalpha()]
        if len(letters) <= 2:
            return True
    return False


def garbage_asr_response(conn) -> str:
    locale = getattr(conn, "active_locale", None) or default_locale(
        getattr(conn, "config", None)
    )
    profile = get_locale_profile(getattr(conn, "config", None), locale)
    err = profile.get("system_error_response")
    if err:
        return str(err)
    if locale == "en":
        return "Sorry, I didn't catch that. Could you say it again?"
    return "Xin lỗi, mình nghe chưa rõ. Bạn nói lại giúp mình nhé?"


def detect_locale(text: str, current_locale: str = "vi", config: dict | None = None) -> str:
    """Infer vi/en from user text. Sticky unless explicit switch or clear signal."""
    if not text or not str(text).strip():
        return current_locale if current_locale in SUPPORTED_LOCALES else default_locale(config)

    t = str(text).strip()
    if _FORCE_EN_RE.search(t):
        return "en"
    if _FORCE_VI_RE.search(t):
        return "vi"

    # ASR noise (Thai/Cyrillic/CJK) — keep sticky locale, never infer English.
    if has_unsupported_script(t):
        return current_locale if current_locale in SUPPORTED_LOCALES else default_locale(config)

    if _VI_DIACRITIC_RE.search(t):
        return "vi"
    if _VI_HINT_RE.search(t):
        return "vi"

    letters = [c for c in t if c.isalpha()]
    if not letters:
        return current_locale

    ascii_letters = sum(1 for c in letters if ord(c) < 128)
    if ascii_letters / len(letters) >= 0.92:
        return "en"

    return current_locale


def resolve_tts_voice(
    character: str | None, config: dict | None, locale: str
) -> str | None:
    locale = (locale or "vi").lower()
    if config and character:
        voices = config.get("character_tts_voice") or {}
        if isinstance(voices, dict):
            entry = voices.get(character)
            if isinstance(entry, dict):
                hit = entry.get(locale) or entry.get("vi") or entry.get("en")
                if hit:
                    return str(hit)
            elif isinstance(entry, str) and entry:
                return entry
    profile = get_locale_profile(config, locale)
    voice = profile.get("tts_voice")
    return str(voice) if voice else None


def resolve_llm_reply_directive(config: dict | None, locale: str) -> str:
    profile = get_locale_profile(config, locale)
    directive = profile.get("llm_reply_directive")
    if directive:
        return str(directive)
    if (locale or "vi").lower() == "en":
        return _DEFAULT_PROFILES["en"]["llm_reply_directive"]
    return _DEFAULT_PROFILES["vi"]["llm_reply_directive"]


def apply_locale_to_connection(conn, locale: str, *, reason: str = "") -> str:
    """Apply ASR/TTS/error-response profile for locale. Returns applied locale."""
    locale = (locale or "vi").lower()
    if locale not in SUPPORTED_LOCALES:
        locale = default_locale(getattr(conn, "config", None))

    prev = getattr(conn, "active_locale", None)
    profile = get_locale_profile(conn.config, locale)
    conn.active_locale = locale
    conn.normalize_vietnamese_tts = bool(profile.get("normalize_vietnamese_tts", locale == "vi"))

    err = profile.get("system_error_response")
    if err:
        conn.config["system_error_response"] = err

    apply_asr_locale(conn, locale, profile)

    if getattr(conn, "tts", None) and hasattr(conn.tts, "voice"):
        from core.characters.character_registry import get_active_character

        character = get_active_character(conn)
        voice = resolve_tts_voice(character, conn.config, locale)
        if voice:
            conn.tts.voice = voice
        speaches_voice = profile.get("tts_speeches_voice")
        if speaches_voice is not None and hasattr(conn.tts, "speeches_voice"):
            conn.tts.speeches_voice = str(speaches_voice)

    logger = getattr(conn, "logger", None)
    if logger and prev != locale:
        logger.bind(tag=TAG).info(
            f"[locale] {prev or '?'} → {locale}"
            + (f" ({reason})" if reason else "")
        )
        refresh = getattr(conn, "_refresh_character_memory_prompt", None)
        if refresh and getattr(conn, "prompt_manager", None):
            try:
                refresh("")
            except Exception as exc:
                if logger:
                    logger.bind(tag=TAG).warning(
                        f"[locale] prompt refresh failed: {exc}"
                    )
    return locale


def apply_asr_locale(
    conn, locale: str | None = None, profile: dict[str, Any] | None = None
) -> None:
    asr = getattr(conn, "asr", None)
    if not asr:
        return
    locale = locale or getattr(conn, "active_locale", default_locale(conn.config))
    profile = profile or get_locale_profile(conn.config, locale)

    lang = profile.get("asr_language")
    prompt = profile.get("asr_prompt")

    if hasattr(asr, "language"):
        asr.language = lang or None
    if hasattr(asr, "prompt") and prompt:
        asr.prompt = prompt


def update_locale_from_user_text(conn, text: str, *, reason: str = "user_text") -> str:
    """Detect language from user message and apply runtime profile."""
    current = getattr(conn, "active_locale", None) or default_locale(conn.config)
    detected = detect_locale(text, current, conn.config)
    return apply_locale_to_connection(conn, detected, reason=reason)


def prepare_asr_for_next_turn(conn) -> None:
    """Call before speech recognition (uses sticky conn.active_locale)."""
    locale = getattr(conn, "active_locale", None) or default_locale(conn.config)
    apply_asr_locale(conn, locale)
