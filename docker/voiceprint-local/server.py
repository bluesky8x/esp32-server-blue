"""Self-hosted local speaker-verification service powered by 3D-Speaker (ModelScope).

Drop-in compatible with the HTTP contract the xiaozhi-server's VoiceprintProvider
expects (same as the upstream voiceprint-api):

    GET  /voiceprint/health?key=<key>       -> {"status":"healthy","total_voiceprints":N}
    POST /voiceprint/identify  (Bearer key) -> {"speaker_id":..., "score":...}
    POST /voiceprint/register  (Bearer key) -> {"status":"ok","speaker_id":...}

Everything runs on YOUR machine:
  - pretrained model (default CAM++) is downloaded ONCE from ModelScope,
  - afterwards inference is fully offline (CPU or GPU),
  - enrolled voice samples are stored locally as .wav files + a small JSON index
    (no external database, no 3rd-party service).

NOTE on the scoring method: the ModelScope speaker-verification pipeline for these
models is the *light* pipeline, which outputs a pairwise similarity score
``{"score": ...}`` for two WAVs (it does not expose a single-input embedding).
So /identify compares the incoming utterance against every enrolled sample and
returns the best score — simple, reliable, and fine for a handful of users.

Config: env vars or a YAML file (see voiceprint-local.yaml).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

CONFIG = {
    "key": os.environ.get("VOICEPRINT_KEY", "abcd"),
    "model_id": os.environ.get(
        "VOICEPRINT_MODEL_ID", "iic/speech_campplus_sv_zh_en_16k-common_advanced"
    ),
    "model_revision": os.environ.get("VOICEPRINT_MODEL_REVISION", "v1.0.0"),
    # min cosine similarity to return a speaker_id (0..1)
    "threshold": float(os.environ.get("VOICEPRINT_THRESHOLD", "0.55")),
    "data_dir": os.environ.get("VOICEPRINT_DATA_DIR", "data"),
    "device": os.environ.get("VOICEPRINT_DEVICE", "cpu"),
    "host": os.environ.get("VOICEPRINT_HOST", "0.0.0.0"),
    "port": int(os.environ.get("VOICEPRINT_PORT", "8005")),
}

# Optional YAML override (e.g. /app/data/voiceprint-local.yaml)
_YAML_PATH = Path(os.environ.get("VOICEPRINT_YAML", "data/voiceprint-local.yaml"))
try:
    import yaml

    if _YAML_PATH.is_file():
        with open(_YAML_PATH, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        for key, value in loaded.items():
            if key in CONFIG:
                CONFIG[key] = value
except Exception:
    pass

KEY = str(CONFIG["key"])
THRESHOLD = float(CONFIG["threshold"])
MODEL_ID = str(CONFIG["model_id"])
MODEL_REVISION = str(CONFIG["model_revision"])
DEVICE = str(CONFIG["device"])
DATA_DIR = Path(CONFIG["data_dir"])
WAV_DIR = DATA_DIR / "wavs"
INDEX_PATH = DATA_DIR / "voiceprints.json"

WAV_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="voiceprint-local (3D-Speaker)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.RLock()


# --------------------------------------------------------------------------
# Model (light speaker-verification pipeline — pairwise scoring)
# --------------------------------------------------------------------------

_sv_pipeline = None
_pipeline_lock = threading.Lock()


def get_pipeline():
    global _sv_pipeline
    if _sv_pipeline is not None:
        return _sv_pipeline
    with _pipeline_lock:
        if _sv_pipeline is None:
            import logging

            logging.getLogger("modelscope").setLevel(logging.WARNING)
            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks

            print(
                f"[voiceprint] loading 3D-Speaker model: {MODEL_ID} "
                f"({MODEL_REVISION}) device={DEVICE}"
            )
            _sv_pipeline = pipeline(
                task=Tasks.speaker_verification,
                model=MODEL_ID,
                model_revision=MODEL_REVISION,
                device=DEVICE,
            )
            print("[voiceprint] model loaded — running locally")
    return _sv_pipeline


def _score_pair(wav_a: str, wav_b: str) -> float:
    """Similarity in [0,1] between two WAVs via the light SV pipeline."""
    sv = get_pipeline()
    out = sv([wav_a, wav_b])
    if isinstance(out, dict) and "score" in out:
        try:
            return float(out["score"])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


# --------------------------------------------------------------------------
# Local speaker store (reference WAVs)
# --------------------------------------------------------------------------

def _load_index() -> dict:
    if INDEX_PATH.is_file():
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def _save_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)


def _wav_path(speaker_id: str) -> Path:
    safe = "".join(c for c in speaker_id if c.isalnum() or c in "_-.") or "speaker"
    return WAV_DIR / f"{safe}.wav"


def _register_wav(speaker_id: str, wav_bytes: bytes) -> None:
    with _lock:
        _wav_path(speaker_id).write_bytes(wav_bytes)
        index = _load_index()
        now = int(time.time())
        existing = index.get(speaker_id, {})
        index[speaker_id] = {
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "sample_count": int(existing.get("sample_count", 0)) + 1,
        }
        _save_index(index)


def _list_speaker_ids(restrict: list[str] | None = None) -> list[str]:
    with _lock:
        ids = list(_load_index().keys())
    if restrict:
        return [sid for sid in ids if sid in restrict]
    return ids


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def _check_key(key: str | None) -> bool:
    return bool(key) and key == KEY


def _check_bearer(authorization: str | None) -> bool:
    if not authorization:
        return False
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return parts[1].strip() == KEY


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/voiceprint/health")
def health(key: str = Query(default="")):
    if not _check_key(key):
        raise HTTPException(status_code=401, detail="invalid key")
    return JSONResponse(
        {"status": "healthy", "total_voiceprints": len(_list_speaker_ids())}
    )


@app.post("/voiceprint/identify")
async def identify(
    file: bytes = File(...),
    speaker_ids: str = Form(default=""),
    authorization: str | None = Header(default=None),
):
    if not _check_bearer(authorization):
        raise HTTPException(status_code=401, detail="invalid bearer token")
    if not file:
        raise HTTPException(status_code=400, detail="missing audio file")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        fh.write(file)
        test_path = fh.name
    try:
        restrict = [s.strip() for s in speaker_ids.split(",") if s.strip()] or None
        ids = _list_speaker_ids(restrict)

        best_id = None
        best_score = 0.0
        for sid in ids:
            ref = _wav_path(sid)
            if not ref.is_file():
                continue
            try:
                score = _score_pair(test_path, str(ref))
            except Exception:
                continue
            if score > best_score:
                best_score = score
                best_id = sid

        if best_id is None or best_score < THRESHOLD:
            return JSONResponse({"speaker_id": None, "score": round(best_score, 4)})
        return JSONResponse({"speaker_id": best_id, "score": round(best_score, 4)})
    finally:
        try:
            os.unlink(test_path)
        except OSError:
            pass


@app.post("/voiceprint/register")
async def register(
    speaker_id: str = Form(...),
    file: bytes = File(...),
    authorization: str | None = Header(default=None),
):
    if not _check_bearer(authorization):
        raise HTTPException(status_code=401, detail="invalid bearer token")
    if not speaker_id or not file:
        raise HTTPException(status_code=400, detail="speaker_id and audio file required")

    _register_wav(speaker_id, file)
    return JSONResponse(
        {"status": "ok", "speaker_id": speaker_id, "score": round(THRESHOLD, 2)}
    )


if __name__ == "__main__":
    # Warm up the model so the first real request is fast.
    try:
        get_pipeline()
    except Exception as exc:  # pragma: no cover
        print(f"[voiceprint] model warmup failed: {exc}")
    uvicorn.run(app, host=CONFIG["host"], port=CONFIG["port"], log_level="info")
