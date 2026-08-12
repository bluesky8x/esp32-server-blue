"""Reject noise/music-only audio and junk ASR transcripts before chatting."""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

import numpy as np

_SAMPLE_RATE = 16000

# Minimum RMS (int16 scale) — below this is silence / mic hiss
_MIN_RMS = 180
# Minimum utterance length to send to ASR
_MIN_DURATION_MS = 450
# Share of energy in typical speech band (300–3400 Hz)
_MIN_SPEECH_BAND_RATIO = 0.32
# Very high ZCR often means noise/hiss
_MAX_ZCR = 0.42

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


def _thresholds(config: Optional[Mapping[str, Any]] = None) -> dict[str, float]:
    sf = (config or {}).get("speech_filter") if config else None
    sf = sf or {}
    return {
        "min_rms": float(sf.get("min_rms", _MIN_RMS)),
        "min_duration_ms": float(sf.get("min_duration_ms", _MIN_DURATION_MS)),
        "min_speech_band_ratio": float(
            sf.get("min_speech_band_ratio", _MIN_SPEECH_BAND_RATIO)
        ),
        "max_zcr": float(sf.get("max_zcr", _MAX_ZCR)),
        "rms_hard_bypass": float(sf.get("rms_hard_bypass", 0)),
        "max_impulse_crest": float(sf.get("max_impulse_crest", 10.0)),
        "min_speech_ms_for_asr": float(sf.get("min_speech_ms_for_asr", 550)),
    }


def analyze_pcm(
    pcm_bytes: bytes, config: Optional[Mapping[str, Any]] = None
) -> dict[str, Any]:
    """Heuristic check: is this chunk likely human speech (not only noise/music)?"""
    thresholds = _thresholds(config)
    if not pcm_bytes or len(pcm_bytes) < 640:
        return {"valid": False, "reason": "too_short_bytes"}

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    if len(audio) < 160:
        return {"valid": False, "reason": "too_few_samples"}

    duration_ms = len(audio) / _SAMPLE_RATE * 1000
    rms = float(np.sqrt(np.mean(audio**2)))
    peak = float(np.max(np.abs(audio)))
    crest = peak / (rms + 1e-8)

    if (
        duration_ms < 900
        and crest >= thresholds["max_impulse_crest"]
        and rms < thresholds["rms_hard_bypass"]
    ):
        return {
            "valid": False,
            "reason": "impulse_click",
            "rms": rms,
            "crest": crest,
            "duration_ms": duration_ms,
        }

    if rms < thresholds["min_rms"]:
        return {"valid": False, "reason": "too_quiet", "rms": rms, "duration_ms": duration_ms}

    if duration_ms < thresholds["min_duration_ms"]:
        return {"valid": False, "reason": "too_short", "rms": rms, "duration_ms": duration_ms}

    bypass = thresholds["rms_hard_bypass"]
    if bypass > 0 and rms >= bypass:
        return {
            "valid": True,
            "rms": rms,
            "duration_ms": duration_ms,
            "reason": "rms_hard_bypass",
        }

    signs = np.sign(audio)
    zcr = float(np.mean(np.abs(np.diff(signs)) > 0) / 2)
    if zcr > thresholds["max_zcr"]:
        return {"valid": False, "reason": "high_zcr", "rms": rms, "zcr": zcr}

    window = np.hanning(len(audio))
    spectrum = np.abs(np.fft.rfft(audio * window)) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / _SAMPLE_RATE)
    speech_mask = (freqs >= 300) & (freqs <= 3400)
    speech_ratio = float(np.sum(spectrum[speech_mask]) / (np.sum(spectrum) + 1e-8))
    if speech_ratio < thresholds["min_speech_band_ratio"]:
        return {
            "valid": False,
            "reason": "low_speech_band",
            "rms": rms,
            "speech_ratio": speech_ratio,
            "duration_ms": duration_ms,
        }

    return {
        "valid": True,
        "rms": rms,
        "zcr": zcr,
        "speech_ratio": speech_ratio,
        "duration_ms": duration_ms,
    }


def is_likely_speech(
    pcm_bytes: bytes, config: Optional[Mapping[str, Any]] = None
) -> bool:
    return analyze_pcm(pcm_bytes, config).get("valid", False)


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
