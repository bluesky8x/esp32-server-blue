import os
import json
import uuid
import requests
from typing import Any
from config.logger import setup_logging
from datetime import datetime
from core.providers.tts.base import TTSProviderBase

TAG = __name__
logger = setup_logging()

# Locale tags use the same [locale=xx] form the connection parses; here we
# read conn.active_locale at synthesis time so the correct TTS backend is used.
_SUPPORTED_LOCALES = frozenset({"vi", "en"})


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.url = config.get("url")
        self.method = config.get("method", "GET")
        self.headers = config.get("headers", {})
        self.audio_file_type = config.get("format", "wav")
        self.output_file = config.get("output_dir", "tmp/")
        self.params = config.get("params")
        self.default_voice = config.get("default_voice", "default")
        self.voice = config.get("voice") or self.default_voice
        self.speeches_voice = config.get("speeches_voice", self.default_voice)

        # Optional per-locale backend overrides. Example:
        #   locales:
        #     vi: {url: ".../8882", model: ..., voice: ..., params: {...}}
        #     en: {url: ".../8883", model: ..., voice: ..., params: {...}}
        self.locales = config.get("locales") or {}
        if isinstance(self.locales, dict):
            # Coerce string-encoded params per locale like the top-level params.
            for loc, lconf in self.locales.items():
                if isinstance(lconf, dict) and isinstance(lconf.get("params"), str):
                    try:
                        self.locales[loc]["params"] = json.loads(lconf["params"])
                    except json.JSONDecodeError:
                        logger.bind(tag=TAG).warning(
                            f"[tts] locale {loc} params unparsable, ignoring"
                        )
                        self.locales[loc]["params"] = None

        if isinstance(self.params, str):
            try:
                self.params = json.loads(self.params)
            except json.JSONDecodeError:
                raise ValueError("Custom TTS配置参数出错,无法将字符串解析为对象")
        elif not isinstance(self.params, dict):
            raise TypeError("Custom TTS配置参数出错, 请参考配置说明")

    def generate_filename(self):
        return os.path.join(self.output_file, f"tts-{datetime.now().date()}@{uuid.uuid4().hex}.{self.audio_file_type}")

    def _active_locale(self) -> str:
        """Locale of the current connection, normalized to vi/en."""
        conn = getattr(self, "conn", None)
        locale = (getattr(conn, "active_locale", None) or "vi").lower()
        return locale if locale in _SUPPORTED_LOCALES else "vi"

    def _locale_config(self) -> dict:
        """Per-locale override block for the active locale (empty if none)."""
        locale = self._active_locale()
        block = self.locales.get(locale)
        return block if isinstance(block, dict) else {}

    def _resolve_param(self, value, text: str, model: str, voice: str) -> Any:
        if not isinstance(value, str):
            return value
        replacements = {
            "{prompt_text}": text,
            "{model}": str(model),
            "{voice}": str(voice),
        }
        for token, repl in replacements.items():
            if token in value:
                value = value.replace(token, repl)
        return value

    async def text_to_speak(self, text, output_file):
        # Resolve the backend for the active locale.
        lconf = self._locale_config()
        url = lconf.get("url") or self.url
        params = lconf.get("params") or self.params
        model = lconf.get("model") or getattr(self, "voice", None) or self.default_voice
        voice = lconf.get("voice") or getattr(self, "speeches_voice", None) or self.default_voice
        fmt = lconf.get("format") or self.audio_file_type

        request_params = {}
        for k, v in params.items():
            request_params[k] = self._resolve_param(v, text, model, voice)

        timeout = getattr(self, "tts_timeout", 15)
        try:
            if self.method.upper() == "POST":
                resp = requests.post(
                    url, json=request_params, headers=self.headers, timeout=timeout
                )
            else:
                resp = requests.get(
                    url, params=request_params, headers=self.headers, timeout=timeout
                )
        except Exception as exc:
            error_msg = f"Custom TTS请求异常: {exc}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)

        if resp.status_code == 200:
            if output_file:
                with open(output_file, "wb") as file:
                    file.write(resp.content)
            else:
                return resp.content
        else:
            error_msg = f"Custom TTS请求失败: {resp.status_code} - {resp.text}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)

