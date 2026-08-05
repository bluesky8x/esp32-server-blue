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

        if isinstance(self.params, str):
            try:
                self.params = json.loads(self.params)
            except json.JSONDecodeError:
                raise ValueError("Custom TTS配置参数出错,无法将字符串解析为对象")
        elif not isinstance(self.params, dict):
            raise TypeError("Custom TTS配置参数出错, 请参考配置说明")

    def generate_filename(self):
        return os.path.join(self.output_file, f"tts-{datetime.now().date()}@{uuid.uuid4().hex}.{self.audio_file_type}")

    def _resolve_param(self, value, text: str) -> Any:
        if not isinstance(value, str):
            return value
        model = getattr(self, "voice", None) or self.default_voice
        voice = getattr(self, "speeches_voice", None) or self.default_voice
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
        request_params = {}
        for k, v in self.params.items():
            request_params[k] = self._resolve_param(v, text)

        timeout = getattr(self, "tts_timeout", 15)
        if self.method.upper() == "POST":
            resp = requests.post(
                self.url, json=request_params, headers=self.headers, timeout=timeout
            )
        else:
            resp = requests.get(
                self.url, params=request_params, headers=self.headers, timeout=timeout
            )
        if resp.status_code == 200:
            if output_file:
                with open(output_file, "wb") as file:
                    file.write(resp.content)
            else:
                return resp.content
        else:
            error_msg = f"Custom TTS请求失败: {resp.status_code} - {resp.text}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)  # 抛出异常，让调用方捕获
