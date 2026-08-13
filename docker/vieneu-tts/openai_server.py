"""
OpenAI-compatible /v1/audio/speech shim for VieNeu-TTS v3 Turbo (CPU/ONNX).

Used by esp32-server-blue CustomTTS provider.
"""
from __future__ import annotations

import io
import os
import wave
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from vieneu import Vieneu

SAMPLE_RATE = 48_000
DEFAULT_VOICE = os.environ.get("VIENEU_DEFAULT_VOICE", "Ngọc Lan")
VIENEU_REPO = os.environ.get("VIENEU_REPO", "")
VIENEU_REF = os.environ.get("VIENEU_REF", "")
HOST = os.environ.get("VIENEU_HOST", "0.0.0.0")
PORT = int(os.environ.get("VIENEU_PORT", "8882"))

vieneu: Optional[Vieneu] = None


class SpeechRequest(BaseModel):
    input: str
    voice: Optional[str] = None
    model: Optional[str] = None
    response_format: str = "wav"
    speed: float = 1.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global vieneu
    print("Loading VieNeu-TTS v3 Turbo (ONNX CPU)...")
    vieneu = Vieneu(backend="onnx")
    print("VieNeu-TTS ready.")
    yield


app = FastAPI(title="VieNeu-TTS OpenAI shim", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "engine": "vieneu-v3-turbo-onnx", "sample_rate": SAMPLE_RATE}


@app.get("/info")
def info():
    """Which git repo/ref this container was built from (verify after rebuild)."""
    preset_count = 0
    sample_voices: list[str] = []
    if vieneu is not None:
        try:
            preset = vieneu.list_preset_voices()
            preset_count = len(preset)
            for item in preset[:6]:
                if isinstance(item, (tuple, list)) and len(item) >= 1:
                    sample_voices.append(str(item[0]))
                else:
                    sample_voices.append(str(item))
        except Exception:
            pass
    return {
        "repo": VIENEU_REPO or None,
        "ref": VIENEU_REF or None,
        "default_voice": DEFAULT_VOICE,
        "preset_voice_count": preset_count,
        "sample_voices": sample_voices,
        "upstream_pnnbao": "pnnbao97" in (VIENEU_REPO or "").lower(),
    }


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "vieneu-v3-turbo", "object": "model"}]}


@app.get("/voices")
def voices():
    if vieneu is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    try:
        preset = vieneu.list_preset_voices()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    out = []
    for item in preset:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            label, vid = item
            out.append({"name": label, "id": vid})
        else:
            out.append({"name": str(item), "id": str(item)})
    return out


def _synthesize_wav(text: str, voice: Optional[str]) -> bytes:
    if vieneu is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is empty")

    chosen = voice or DEFAULT_VOICE
    chunks = []
    try:
        for chunk in vieneu.infer_stream(text, voice=chosen):
            if chunk is not None and len(chunk):
                chunks.append(chunk)
    except Exception as exc:  # noqa: BLE001
        hint = f"voice={chosen!r}"
        try:
            names = [
                (item[0] if isinstance(item, (tuple, list)) else str(item))
                for item in vieneu.list_preset_voices()
            ]
            hint += f"; available: {', '.join(names[:8])}"
            if len(names) > 8:
                hint += ", ..."
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"synthesis failed ({hint}): {exc}") from exc

    if not chunks:
        raise HTTPException(
            status_code=500,
            detail=f"empty synthesis for voice={chosen!r} — check GET /voices",
        )

    audio = np.concatenate(chunks)
    pcm = (np.asarray(audio) * 32767).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    if req.response_format not in ("wav", "pcm"):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported response_format: {req.response_format}",
        )
    wav = _synthesize_wav(req.input, req.voice)
    return Response(content=wav, media_type="audio/wav")


def main():
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
