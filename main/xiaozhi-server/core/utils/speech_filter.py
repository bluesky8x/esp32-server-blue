"""Reject noise/music-only audio and junk ASR transcripts before chatting."""

from __future__ import annotations

import re
import time
from typing import Any, Mapping, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

_SAMPLE_RATE = 16000

# Defaults — override via config.yaml `speech_filter:` (Blue V2 INMP441 + motor EMI)
_DEFAULT_MIN_RMS = 180
_DEFAULT_MIN_DURATION_MS = 450
_DEFAULT_MIN_SPEECH_BAND_RATIO = 0.24
_DEFAULT_MAX_ZCR = 0.42
# Strong RMS bypass: robot mic often has hiss outside 300–3400 Hz but speech is still usable
_DEFAULT_RMS_BYPASS = 1500
_DEFAULT_RMS_BYPASS_MIN_RATIO = 0.18

_FILLER_ONLY_RE = re.compile(
    r"^(?:"
    r"um+|uh+|ah+|er+|hm+|hmm+|mhm+|"
    r"ừ+|ờ+|à+|á+|ah+|oh+|"
    r"thank you|thanks|subscribe|you"
    r")\s*\.?\s*$",
    re.I,
)

_NOISE_TRANSCRIPT_RES = (
    re.compile(r"^\s*$"),
    re.compile(
        r"^\[?\s*(music|applause|silence|noise|instrumental|"
        r"nhạc|tiếng ồn|background noise|wind|rain)\s*\]?\s*\.?\s*$",
        re.I,
    ),
    re.compile(r"^(thank you for watching|thanks for watching|please subscribe)\b", re.I),
    re.compile(r"^[\♪♫🎵🎶\s\[\]()\-–—.,!?]+$"),
    re.compile(r"^\(\s*(nhạc|music|tiếng ồn|noise)\s*\)\s*$", re.I),
    re.compile(r"^[^a-zA-ZÀ-ỹ0-9]+$"),
)

# ASR sometimes hallucinates lyrics fragments on music — short repeated tokens
_MUSIC_HALLUCINATION_RE = re.compile(
    r"^(la la|na na|oh oh|yeah yeah|baby baby|doo doo|la la la)\b",
    re.I,
)


def _thresholds(cfg: Mapping[str, Any] | None) -> dict[str, float]:
    c = cfg or {}
    return {
        "min_rms": float(c.get("min_rms", _DEFAULT_MIN_RMS)),
        "min_duration_ms": float(c.get("min_duration_ms", _DEFAULT_MIN_DURATION_MS)),
        "min_speech_band_ratio": float(
            c.get("min_speech_band_ratio", _DEFAULT_MIN_SPEECH_BAND_RATIO)
        ),
        "max_zcr": float(c.get("max_zcr", _DEFAULT_MAX_ZCR)),
        "rms_bypass": float(c.get("rms_bypass", _DEFAULT_RMS_BYPASS)),
        "rms_bypass_min_ratio": float(
            c.get("rms_bypass_min_ratio", _DEFAULT_RMS_BYPASS_MIN_RATIO)
        ),
        "motor_relax_min_ratio": float(c.get("motor_relax_min_ratio", 0.12)),
        "motor_relax_rms_bypass_ratio": float(
            c.get("motor_relax_rms_bypass_ratio", 0.10)
        ),
    }


def _apply_motor_relax(
    t: dict[str, float], conn: "ConnectionHandler | None"
) -> dict[str, float]:
    """Loosen band-ratio gate briefly after robot motor moves (INMP441 + EMI)."""
    if conn is None:
        return t
    until = getattr(conn, "_speech_filter_relax_until", 0.0)
    if time.monotonic() >= until:
        return t
    out = dict(t)
    out["min_speech_band_ratio"] = min(
        out["min_speech_band_ratio"], out["motor_relax_min_ratio"]
    )
    out["rms_bypass_min_ratio"] = min(
        out["rms_bypass_min_ratio"], out["motor_relax_rms_bypass_ratio"]
    )
    out["motor_relaxed"] = 1.0
    return out


def analyze_pcm(
    pcm_bytes: bytes,
    cfg: Mapping[str, Any] | None = None,
    conn: "ConnectionHandler | None" = None,
) -> dict[str, Any]:
    """Heuristic check: is this chunk likely human speech (not only noise/music)?"""
    t = _apply_motor_relax(_thresholds(cfg), conn)
    if not pcm_bytes or len(pcm_bytes) < 640:
        return {"valid": False, "reason": "too_short_bytes"}

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    if len(audio) < 160:
        return {"valid": False, "reason": "too_few_samples"}

    duration_ms = len(audio) / _SAMPLE_RATE * 1000
    rms = float(np.sqrt(np.mean(audio**2)))
    if rms < t["min_rms"]:
        return {"valid": False, "reason": "too_quiet", "rms": rms, "duration_ms": duration_ms}

    if duration_ms < t["min_duration_ms"]:
        return {"valid": False, "reason": "too_short", "rms": rms, "duration_ms": duration_ms}

    signs = np.sign(audio)
    zcr = float(np.mean(np.abs(np.diff(signs)) > 0) / 2)
    if zcr > t["max_zcr"]:
        return {"valid": False, "reason": "high_zcr", "rms": rms, "zcr": zcr}

    window = np.hanning(len(audio))
    spectrum = np.abs(np.fft.rfft(audio * window)) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / _SAMPLE_RATE)
    speech_mask = (freqs >= 300) & (freqs <= 3400)
    speech_ratio = float(np.sum(spectrum[speech_mask]) / (np.sum(spectrum) + 1e-8))

    if rms >= t["rms_bypass"] and speech_ratio >= t["rms_bypass_min_ratio"]:
        return {
            "valid": True,
            "rms": rms,
            "zcr": zcr,
            "speech_ratio": speech_ratio,
            "duration_ms": duration_ms,
            "bypass": "rms",
        }

    if speech_ratio < t["min_speech_band_ratio"]:
        return {
            "valid": False,
            "reason": "low_speech_band",
            "rms": rms,
            "speech_ratio": speech_ratio,
            "duration_ms": duration_ms,
            "zcr": zcr,
        }

    return {
        "valid": True,
        "rms": rms,
        "zcr": zcr,
        "speech_ratio": speech_ratio,
        "duration_ms": duration_ms,
    }


def is_likely_speech(
    pcm_bytes: bytes,
    cfg: Mapping[str, Any] | None = None,
    conn: "ConnectionHandler | None" = None,
) -> bool:
    return analyze_pcm(pcm_bytes, cfg, conn).get("valid", False)


def is_noise_transcript(text: str) -> bool:
    """True if transcript should be discarded (noise/music/fillers only)."""
    if text is None:
        return True
    t = text.strip()
    if not t:
        return True

    # JSON speaker wrapper — check inner content
    if t.startswith("{") and "content" in t:
        try:
            import json

            data = json.loads(t)
            inner = str(data.get("content") or "").strip()
            if inner:
                return is_noise_transcript(inner)
        except (json.JSONDecodeError, TypeError):
            pass

    if len(t) <= 2:
        return True

    for pat in _NOISE_TRANSCRIPT_RES:
        if pat.search(t):
            return True

    if _FILLER_ONLY_RE.match(t):
        return True

    if _MUSIC_HALLUCINATION_RE.match(t) and len(t) < 40:
        return True

    # Mostly non-linguistic characters
    letters = sum(1 for c in t if c.isalpha())
    if letters == 0:
        return True

    return False
