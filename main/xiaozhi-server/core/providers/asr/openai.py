import asyncio
import time
import os
import re
from config.logger import setup_logging
from typing import Optional, Tuple, List
from core.providers.asr.dto.dto import InterfaceType
from core.providers.asr.base import ASRProviderBase

import requests

TAG = __name__
logger = setup_logging()

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")

class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        self.interface_type = InterfaceType.NON_STREAM
        self.api_key = config.get("api_key")
        self.api_url = config.get("base_url")
        self.model = config.get("model_name")
        self.output_dir = config.get("output_dir")
        self.delete_audio_file = delete_audio_file
        self.language = config.get("language") or None
        self._default_language = self.language
        self.prompt = config.get("prompt") or (
            "Vietnamese (tiếng Việt) or English only. "
            "Do not transcribe as Thai. "
            "Use Vietnamese diacritics when the speaker uses Vietnamese. "
            "Omit filler sounds: um, uh, ah, ừ, ờ. "
            "Do not transcribe background music, song lyrics, TV, or noise. "
            "If only music or noise is heard, return empty."
        )
        self._default_prompt = self.prompt

        os.makedirs(self.output_dir, exist_ok=True)

    def requires_file(self) -> bool:
        return True

    def _post_transcribe(self, file_path: str, data: dict, headers: dict, timeout: float = 60.0):
        """Sync transcribe POST (run in a worker thread so it never blocks the event loop)."""
        with open(file_path, "rb") as audio_file:
            return requests.post(
                self.api_url,
                files={"file": audio_file},
                data=data,
                headers=headers,
                timeout=timeout,
            )

    async def speech_to_text(self, opus_data: List[bytes], session_id: str, artifacts=None) -> Tuple[Optional[str], Optional[str]]:
        file_path = None
        try:
            if artifacts is None:
                return "", None
            file_path = artifacts.file_path
                
            logger.bind(tag=TAG).info(f"file path: {file_path}")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
            }
            
            data = {"model": self.model}
            if self.language:
                data["language"] = self.language
            if self.prompt:
                data["prompt"] = self.prompt

            start_time = time.time()
            # Run the network call off the event loop — the blocking requests.post
            # previously stalled the whole server during transcription.
            response = await asyncio.to_thread(
                self._post_transcribe, file_path, data, headers
            )
            logger.bind(tag=TAG).debug(
                f"语音识别耗时: {time.time() - start_time:.3f}s | 结果: {response.text}"
            )

            if response.status_code == 200:
                text = response.json().get("text", "")
                if text and _THAI_RE.search(text) and (self.language or "vi") == "vi":
                    logger.bind(tag=TAG).warning(
                        f"ASR returned Thai script, retrying with language=vi: {text[:80]}"
                    )
                    text = await self._retry_vietnamese(file_path, headers) or text
                return text, file_path
            else:
                raise Exception(f"API请求失败: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}")
            return "", None

    async def _retry_vietnamese(self, file_path: str, headers: dict) -> str:
        """Retry once with language locked to Vietnamese when Thai was mis-detected."""
        data = {
            "model": self.model,
            "language": "vi",
            "prompt": self.prompt,
        }
        try:
            response = await asyncio.to_thread(
                self._post_transcribe, file_path, data, headers, 60.0
            )
            if response.status_code == 200:
                retry_text = response.json().get("text", "")
                if retry_text and not _THAI_RE.search(retry_text):
                    return retry_text
        except Exception as e:
            logger.bind(tag=TAG).warning(f"ASR Vietnamese retry failed: {e}")
        return ""
        
