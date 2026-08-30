"""Kokoro-82M TTS HTTP server — OpenAI-compatible /v1/audio/speech.

Routes English text to Kokoro-82M (female voices like af_heart / af_bella /
af_nicole) running on CPU via kokoro-onnx. Vietnamese is NOT supported by
Kokoro — keep VieNeu for vi.

Request body (matches CustomTTS / OpenAI TTS):
    {"input": "text", "voice": "af_heart", "speed": 1.0, "response_format": "wav"}
Response: WAV bytes (sample_rate from KOKORO_SAMPLE_RATE).
"""

import io
import os
import re
import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from kokoro_onnx import Kokoro

app = FastAPI(title="Kokoro-TTS", version="1.0")

_MODEL = os.getenv("KOKORO_MODEL", "/models/kokoro.onnx")
_VOICES = os.getenv("KOKORO_VOICES", "/models/voices.bin")
_DEFAULT_VOICE = os.getenv("KOKORO_DEFAULT_VOICE", "af_heart")
_SAMPLE_RATE = int(os.getenv("KOKORO_SAMPLE_RATE", "24000"))

_kokoro: Kokoro | None = None

# Voice names are like af_heart, af_bella, am_michael. Only accept safe ones.
_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z0-9]+$", re.IGNORECASE)


def _load() -> None:
    global _kokoro
    if _kokoro is not None:
        return
    # kokoro-onnx's bundled tokenizer phonemizes English via espeak-ng
    # (installed in the image) — no misaki/G2P dependency needed.
    _kokoro = Kokoro(_MODEL, _VOICES)


@app.on_event("startup")
async def _startup() -> None:
    _load()


@app.get("/health")
async def health():
    return {"status": "ok", "engine": "kokoro-82m-onnx", "sample_rate": _SAMPLE_RATE}


@app.get("/voices")
async def voices():
    _load()
    return {"voices": sorted(_kokoro.get_voices()), "default": _DEFAULT_VOICE}


@app.post("/v1/audio/speech")
async def speech(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    text = (body.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty input")

    voice = str(body.get("voice") or _DEFAULT_VOICE)
    if not _VOICE_RE.match(voice):
        raise HTTPException(status_code=400, detail=f"unsafe voice name: {voice!r}")
    speed = float(body.get("speed", 1.0))
    if not 0.5 <= speed <= 2.0:
        speed = 1.0

    _load()
    if voice not in _kokoro.get_voices():
        voice = _DEFAULT_VOICE
        if voice not in _kokoro.get_voices():
            raise HTTPException(status_code=500, detail="no usable voice")

    try:
        samples, _ = _kokoro.create(
            text, voice=voice, speed=speed, lang="en-us"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"synth failed: {exc}")

    samples = np.asarray(samples, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, _SAMPLE_RATE, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return Response(content=buf.read(), media_type="audio/wav")
