import asyncio
import os
import re
import uuid
import edge_tts
from edge_tts.exceptions import NoAudioReceived
from datetime import datetime
from core.providers.tts.base import TTSProviderBase

_EDGE_TTS_TIMEOUT_S = 12

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "speech_rate", -100, 100, 0, int),
        ("ttsPitch", "pitch_rate", -100, 100, 0, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice")
        self.audio_file_type = config.get("format", "mp3")

        volume = config.get("volume", "50")
        self.volume = int(volume) if volume else 50

        speech_rate = config.get("rate", "0")
        self.speech_rate = int(speech_rate) if speech_rate else 0

        pitch_rate = config.get("pitch", "0")
        self.pitch_rate = int(pitch_rate) if pitch_rate else 0

        # 应用百分比调整
        self._apply_percentage_params(config)

        self.edge_rate = f"{self.speech_rate:+}%"
        self.edge_volume = f"{self.volume:+}%"
        self.edge_pitch = f"{self.pitch_rate:+}Hz"
        self.fallback_voice = config.get("fallback_voice", "zh-CN-XiaoxiaoNeural")

    @staticmethod
    def _sanitize_for_voice(text: str) -> str:
        """Strip scripts the active voice cannot speak (avoids wrong-language TTS)."""
        if not text:
            return text
        cleaned = _THAI_RE.sub("", text)
        return cleaned.strip() or text

    def generate_filename(self, extension=".mp3"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    async def _collect_stream(self, communicate, output_file):
        if output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            audio_bytes = 0
            with open(output_file, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                        audio_bytes += len(chunk["data"])
            return audio_bytes

        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data

    async def _stream_to_target(self, text, voice, output_file):
        communicate = edge_tts.Communicate(
            text,
            voice=voice,
            rate=self.edge_rate,
            volume=self.edge_volume,
            pitch=self.edge_pitch,
        )
        try:
            return await asyncio.wait_for(
                self._collect_stream(communicate, output_file),
                timeout=_EDGE_TTS_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            if output_file and os.path.exists(output_file):
                os.remove(output_file)
            raise Exception(
                f"Edge TTS timed out after {_EDGE_TTS_TIMEOUT_S}s"
            ) from exc
        except NoAudioReceived:
            if output_file and os.path.exists(output_file):
                os.remove(output_file)
            return 0 if output_file else b""

    async def text_to_speak(self, text, output_file):
        try:
            text = self._sanitize_for_voice(text)
            result = await self._stream_to_target(text, self.voice, output_file)
            got_audio = (result > 0) if output_file else bool(result)
            if (
                not got_audio
                and self.fallback_voice
                and self.fallback_voice != self.voice
                and _CJK_RE.search(text)
            ):
                result = await self._stream_to_target(
                    text, self.fallback_voice, output_file
                )
            if output_file:
                if result <= 0:
                    raise Exception("No audio was received. Please verify that your parameters are correct.")
                return None
            if not result:
                raise Exception("No audio was received. Please verify that your parameters are correct.")
            return result
        except Exception as e:
            error_msg = f"Edge TTS请求失败: {e}"
            raise Exception(error_msg)  # 抛出异常，让调用方捕获