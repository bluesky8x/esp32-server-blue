"""Lightweight EQ / energy analysis → dance music states + realtime timeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

MusicStateName = Literal["chill", "groove", "drive", "drop", "flow"]

STATE_ORDER: tuple[MusicStateName, ...] = (
    "chill",
    "groove",
    "drive",
    "drop",
    "flow",
)

# Compact timeline chars (firmware must match MotorDance::ParseTimelineChar).
STATE_TO_CHAR: dict[MusicStateName, str] = {
    "chill": "c",
    "groove": "g",
    "drive": "v",
    "drop": "D",
    "flow": "f",
}
CHAR_TO_STATE: dict[str, MusicStateName] = {
    v: k for k, v in STATE_TO_CHAR.items()
}

SEGMENT_MS_BY_TEMPO: dict[str, int] = {
    "slow": 8000,
    "medium": 6000,
    "fast": 4000,
}


@dataclass(frozen=True)
class MusicEqProfile:
    primary: MusicStateName
    states: tuple[MusicStateName, ...]
    weights: dict[MusicStateName, float] = field(default_factory=dict)
    tempo_hint: Literal["slow", "medium", "fast"] = "medium"
    bass_ratio: float = 0.0
    energy: float = 0.0
    timeline: str = ""
    segment_ms: int = 6000

    def to_mcp_dict(self) -> dict:
        payload = {
            "mood": self.primary,
            "states": ",".join(self.states),
            "tempo": self.tempo_hint,
            "segment_ms": self.segment_ms,
        }
        if self.timeline:
            payload["timeline"] = self.timeline
        return payload


@dataclass(frozen=True)
class LoadedAudio:
    samples: np.ndarray
    sample_rate: int


def default_profile_for_track(track: int) -> MusicEqProfile:
    presets: dict[int, tuple[MusicStateName, tuple[MusicStateName, ...], str]] = {
        1: ("groove", ("groove", "drive"), "gvgvgvgv"),
        2: ("drop", ("groove", "drop", "drive"), "gDDvgDDvgD"),
        3: ("drop", ("drop", "drive"), "DvDvDvDv"),
    }
    primary, states, timeline = presets.get(track, ("groove", ("groove", "flow"), "gfgfgfgf"))
    weights = {s: (0.5 if s == primary else 0.1) for s in STATE_ORDER}
    for s in states:
        weights[s] = max(weights[s], 0.25)
    return MusicEqProfile(
        primary=primary,
        states=states,
        weights=weights,
        timeline=timeline,
        segment_ms=6000,
    )


def profile_summary(profile: MusicEqProfile) -> str:
    seg_count = len(profile.timeline) if profile.timeline else 0
    return (
        f"{profile.primary} [{','.join(profile.states)}] "
        f"tempo={profile.tempo_hint} timeline={seg_count}x{profile.segment_ms}ms"
    )


def _empty_profile() -> MusicEqProfile:
    return MusicEqProfile(
        primary="groove",
        states=("groove", "flow"),
        weights={s: (0.5 if s == "groove" else 0.1) for s in STATE_ORDER},
        tempo_hint="medium",
        timeline="gfgfgf",
        segment_ms=6000,
    )


def _load_audio_mono(file_path: str | Path) -> LoadedAudio | None:
    path = Path(file_path)
    if not path.is_file():
        return None
    try:
        from pydub import AudioSegment
    except ImportError:
        return None
    try:
        audio = AudioSegment.from_file(str(path))
    except Exception:
        return None
    audio = audio.set_channels(1).set_frame_rate(22050)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    if samples.size == 0:
        return None
    peak = float(np.max(np.abs(samples))) or 1.0
    return LoadedAudio(samples=samples / peak, sample_rate=22050)


def _classify_window(bass_r: float, mid_r: float, treble_r: float, rms: float) -> dict[MusicStateName, float]:
    scores: dict[MusicStateName, float] = {s: 0.05 for s in STATE_ORDER}
    if rms < 0.06:
        scores["chill"] += 0.55
        scores["flow"] += 0.25
    elif rms < 0.14:
        scores["groove"] += 0.35
        scores["flow"] += 0.3
        scores["chill"] += 0.15
    else:
        scores["drive"] += 0.35
        scores["drop"] += 0.25
        scores["groove"] += 0.15

    if bass_r > 0.42:
        scores["drop"] += 0.35
        scores["drive"] += 0.2
    if mid_r > 0.45:
        scores["groove"] += 0.25
    if treble_r > 0.38 and rms > 0.1:
        scores["drive"] += 0.15

    total = sum(scores.values()) or 1.0
    return {k: v / total for k, v in scores.items()}


def _scores_to_primary_states(scores: dict[MusicStateName, float]) -> tuple[MusicStateName, tuple[MusicStateName, ...]]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    primary = ranked[0][0]
    states: list[MusicStateName] = [primary]
    for name, w in ranked[1:3]:
        if w >= 0.18:
            states.append(name)
    return primary, tuple(states)


def _window_scores_for_audio(loaded: LoadedAudio) -> tuple[list[dict[MusicStateName, float]], list[float]]:
    sr = loaded.sample_rate
    win = sr // 2
    samples = loaded.samples
    if len(samples) < win:
        return [], []

    window_scores: list[dict[MusicStateName, float]] = []
    rms_vals: list[float] = []
    for start in range(0, len(samples) - win, win):
        chunk = samples[start : start + win]
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        rms_vals.append(rms)
        spectrum = np.abs(np.fft.rfft(chunk))
        freqs = np.fft.rfftfreq(len(chunk), 1.0 / sr)
        bass = float(np.mean(spectrum[(freqs >= 20) & (freqs < 250)] ** 2))
        mid = float(np.mean(spectrum[(freqs >= 250) & (freqs < 2000)] ** 2))
        treble = float(np.mean(spectrum[(freqs >= 2000) & (freqs < 8000)] ** 2))
        total = bass + mid + treble + 1e-9
        window_scores.append(
            _classify_window(bass / total, mid / total, treble / total, rms)
        )
    return window_scores, rms_vals


def _build_timeline(
    window_scores: list[dict[MusicStateName, float]],
    *,
    sample_rate: int,
    window_samples: int,
    segment_ms: int,
) -> str:
    if not window_scores:
        return "g"
    win_ms = int(window_samples * 1000 / sample_rate)
    windows_per_segment = max(1, segment_ms // max(win_ms, 1))

    chars: list[str] = []
    for seg_start in range(0, len(window_scores), windows_per_segment):
        chunk = window_scores[seg_start : seg_start + windows_per_segment]
        agg = {s: 0.0 for s in STATE_ORDER}
        for ws in chunk:
            for state, score in ws.items():
                agg[state] += score
        primary, _ = _scores_to_primary_states(agg)
        chars.append(STATE_TO_CHAR[primary])
    return "".join(chars) if chars else "g"


def analyze_music_eq(file_path: str | Path) -> MusicEqProfile:
    """Full-song EQ profile + compact realtime timeline."""
    loaded = _load_audio_mono(file_path)
    if loaded is None:
        return _empty_profile()

    window_scores, rms_vals = _window_scores_for_audio(loaded)
    if not window_scores:
        return _empty_profile()

    weights = {s: 0.0 for s in STATE_ORDER}
    for ws in window_scores:
        for state, score in ws.items():
            weights[state] += score
    total_w = sum(weights.values()) or 1.0
    weights = {k: v / total_w for k, v in weights.items()}

    primary, states = _scores_to_primary_states(weights)

    avg_energy = float(np.mean(rms_vals))
    energy_var = float(np.var(rms_vals))
    if avg_energy > 0.16 or energy_var > 0.004:
        tempo_hint = "fast"
    elif avg_energy < 0.08:
        tempo_hint = "slow"
    else:
        tempo_hint = "medium"

    segment_ms = SEGMENT_MS_BY_TEMPO[tempo_hint]
    timeline = _build_timeline(
        window_scores,
        sample_rate=loaded.sample_rate,
        window_samples=loaded.sample_rate // 2,
        segment_ms=segment_ms,
    )

    return MusicEqProfile(
        primary=primary,
        states=states,
        weights=weights,
        tempo_hint=tempo_hint,
        bass_ratio=float(np.mean([ws["drop"] + ws["drive"] for ws in window_scores])),
        energy=avg_energy,
        timeline=timeline,
        segment_ms=segment_ms,
    )
