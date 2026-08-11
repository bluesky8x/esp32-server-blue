import asyncio
import base64
import io
import json
import os
import re
import time
import uuid
import wave
from datetime import datetime

import requests

from config.logger import setup_logging
from core.providers.llm.gemini.gemini import setup_proxy_env
from core.providers.tts.base import TTSProviderBase
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_TTS_PROMPT = (
    "Synthesize speech from the following transcript. "
    "Read it aloud exactly as written. Do not reply in text.\n\n"
    "TRANSCRIPT:\n{text}"
)

# Prebuilt Gemini TTS voices (generateContent API)
GEMINI_TTS_VOICES = frozenset(
    {
        "Zephyr",
        "Puck",
        "Charon",
        "Kore",
        "Fenrir",
        "Leda",
        "Orus",
        "Aoede",
        "Callirrhoe",
        "Autonoe",
        "Enceladus",
        "Iapetus",
        "Umbriel",
        "Algieba",
        "Despina",
        "Erinome",
        "Algenib",
        "Rasalgethi",
        "Laomedeia",
        "Achernar",
        "Alnilam",
        "Schedar",
        "Gacrux",
        "Pulcherrima",
        "Achird",
        "Zubenelgenubi",
        "Vindemiatrix",
        "Sadachbia",
        "Sadaltager",
        "Sulafat",
    }
)


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key", "")
        self.model_name = config.get("model_name", "gemini-2.5-flash-preview-tts")
        self.default_voice = config.get("voice", "Kore")
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = self.default_voice
        self.language_code = config.get("language_code") or None
        self.locale_language_codes = config.get("locale_language_codes") or {}
        self.locale_voices = config.get("locale_voices") or {}
        self.prompt_template = config.get("prompt_template") or DEFAULT_TTS_PROMPT
        self.max_retries = int(config.get("max_retries", 2))
        self.audio_file_type = "wav"
        self.sample_rate = int(config.get("sample_rate", DEFAULT_SAMPLE_RATE))
        self.timeout = int(config.get("timeout", self.tts_timeout))

        http_proxy = (config.get("http_proxy") or "").strip()
        https_proxy = (config.get("https_proxy") or "").strip()
        if http_proxy or https_proxy:
            setup_proxy_env(http_proxy or None, https_proxy or None)

        model_key_msg = check_model_key("TTS", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

    def generate_filename(self, extension=".wav"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    def _resolve_language_code(self) -> str | None:
        locale = getattr(getattr(self, "conn", None), "active_locale", None)
        if locale:
            if self.locale_language_codes:
                code = self.locale_language_codes.get(locale)
                if code:
                    return str(code)
            conn = self.conn
            if conn and getattr(conn, "config", None):
                from core.utils.language_runtime import get_locale_profile

                profile = get_locale_profile(conn.config, locale)
                code = profile.get("gemini_language_code")
                if code:
                    return str(code)
        return self.language_code

    def _resolve_voice(self) -> str:
        voice = self.voice or self.default_voice
        if voice in GEMINI_TTS_VOICES:
            return voice

        # Edge-style voice ids from language_runtime — map via locale_voices
        locale = getattr(getattr(self, "conn", None), "active_locale", None) or "vi"
        if self.locale_voices:
            mapped = self.locale_voices.get(locale)
            if mapped:
                return str(mapped)
        return self.default_voice

    def _build_tts_input(self, text: str) -> str:
        """Gemini TTS models require a clear speech-synthesis preamble."""
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned
        return self.prompt_template.replace("{text}", cleaned)

    @staticmethod
    def _parse_retry_delay_seconds(response_text: str) -> float | None:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            return None
        details = (payload.get("error") or {}).get("details") or []
        for item in details:
            if item.get("@type", "").endswith("RetryInfo"):
                delay = item.get("retryDelay") or item.get("retry_delay")
                if isinstance(delay, str) and delay.endswith("s"):
                    try:
                        return max(float(delay[:-1]), 1.0)
                    except ValueError:
                        pass
        match = re.search(r"retry in ([0-9.]+)s", response_text, re.IGNORECASE)
        if match:
            try:
                return max(float(match.group(1)), 1.0)
            except ValueError:
                pass
        return None

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
        if not pcm_data:
            return b""
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return wav_buffer.getvalue()

    def _build_request_body(self, text: str) -> dict:
        speech_config: dict = {
            "voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": self._resolve_voice()}
            }
        }
        language_code = self._resolve_language_code()
        if language_code:
            speech_config["languageCode"] = language_code

        return {
            "contents": [{"parts": [{"text": self._build_tts_input(text)}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": speech_config,
            },
        }

    @staticmethod
    def _extract_pcm_from_response(data: dict) -> bytes:
        candidates = data.get("candidates") or []
        if not candidates:
            err = data.get("error") or data
            raise Exception(f"Gemini TTS: no candidates - {err}")

        for part in (candidates[0].get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline:
                continue
            b64 = inline.get("data")
            if b64:
                return base64.b64decode(b64)

        raise Exception("Gemini TTS: no audio inlineData in response")

    def _call_api_once(self, text: str) -> tuple[int, str]:
        url = f"{GEMINI_API_BASE}/{self.model_name}:generateContent"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        response = requests.post(
            url,
            json=self._build_request_body(text),
            headers=headers,
            timeout=self.timeout,
        )
        return response.status_code, response.text

    def _call_api(self, text: str) -> bytes:
        last_error = "Gemini TTS request failed"
        for attempt in range(self.max_retries + 1):
            status_code, body = self._call_api_once(text)
            if status_code == 200:
                return self._extract_pcm_from_response(json.loads(body))

            last_error = f"Gemini TTS failed: {status_code} - {body}"
            if status_code == 429 and attempt < self.max_retries:
                delay = self._parse_retry_delay_seconds(body) or 15.0
                logger.bind(tag=TAG).warning(
                    f"Gemini TTS rate limited, retrying in {delay:.0f}s "
                    f"({attempt + 1}/{self.max_retries})"
                )
                time.sleep(delay)
                continue
            break

        raise Exception(last_error)

    async def text_to_speak(self, text, output_file):
        try:
            pcm_data = await asyncio.to_thread(self._call_api, text)
            if not pcm_data:
                raise Exception("Gemini TTS returned empty audio")

            wav_data = self._pcm_to_wav(pcm_data, self.sample_rate)
            if output_file:
                out_dir = os.path.dirname(output_file)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(output_file, "wb") as audio_file:
                    audio_file.write(wav_data)
                return None
            return wav_data
        except Exception as e:
            raise Exception(f"Gemini TTS请求失败: {e}") from e
