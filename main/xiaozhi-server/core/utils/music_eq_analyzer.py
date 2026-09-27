"""Lightweight EQ / energy analysis → dance music states + realtime timeline."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

MusicStateName = Literal["chill", "groove", "drive", "drop", "flow"]

STATE_ORDER: tuple[MusicStateName, ...] = (
    "chill",
    "groove",
    "drive",
    "drop",
    "flow",
)

# Compact timeline chars (firmware must match MotorDance::ParseTimelineChar).
# 'w' = ĐOẠN VẪY CHÂN (không phải mood — xem _insert_wave_segments / WAVE_CHAR).
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

# Một điệu không nên kéo dài quá ngần này: dài hơn thì người xem thấy nhàm (robot lặp mãi một động
# tác). Vượt hạn ⇒ đoạn kế tiếp đổi sang state khác (xem _spice_timeline).
MAX_STATE_RUN_MS = 10000
# Số ĐIỆU khác nhau tối thiểu của một bài để bước phá nhàm có đủ lựa chọn. Bài chỉ 1-2 điệu (rất
# nhiều đoạn cùng state) thì mở rộng danh sách chọn ra CẢ 5 điệu của hệ (STATE_ORDER) với trọng số
# nền nhỏ — vẫn lấy từ chính bài, không hardcode điệu nào cụ thể.
MIN_SPICE_MOODS = 3
SPICE_FLOOR_WEIGHT = 0.05

# ĐOẠN VẪY CHÂN: cứ mỗi WAVE_WINDOW_SEGMENTS đoạn nhạc thì chèn 1 chữ 'w' vào ĐOẠN SÔI ĐỘNG NHẤT
# trong cửa sổ đó (firmware hiểu 'w' = vẫy chân chào rồi nhún tiếp cho hết đoạn — xem
# GAIT_DANCE_WAVE_* trong blue-v4/config.h).
#   • Không hardcode tên điệu nào: chọn theo chính năng lượng (RMS) đo được.
#   • Đoạn được chọn phải THẬT SỰ mạnh: RMS ≥ WAVE_MIN_ENERGY_RMS (đúng ngưỡng mà _classify_window
#     coi là drive/drop) VÀ ≥ WAVE_MIN_ENERGY_RATIO lần năng lượng trung bình cả bài. Bài êm (mọi
#     đoạn ~0.08) ⇒ không có đoạn vẫy nào.
#   • Các chữ 'w' cách nhau ≥ WAVE_WINDOW_SEGMENTS đoạn nên không bao giờ đứng liền nhau, và không
#     đi qua bảng mood nên _spice_timeline (gọi trước đó) không đổi chúng.
WAVE_CHAR = "w"
WAVE_WINDOW_SEGMENTS = 4  # ~16 s với segment 4 s
WAVE_MIN_ENERGY_RATIO = 0.85
WAVE_MIN_ENERGY_RMS = 0.14  # ngưỡng "sôi động" — khớp mốc rms của _classify_window (drive/drop)

# Tên các chữ ĐẶC BIỆT (không phải mood) — chỉ để log dễ đọc. Firmware hiểu chúng trong
# GaitEngine::RunDance (xem GAIT_DANCE_* trong blue-v4/config.h).
SPECIAL_CHAR_NAMES: dict[str, str] = {
    WAVE_CHAR: "vay-chan",
    "s": "buoc-toi",
    "l": "nghieng-trai",
    "r": "nghieng-phai",
    "p": "chui-toi",
    "n": "ngua-sau",
}

# ĐỘNG TÁC "SINH ĐỘNG" (ngoài 'w' vẫy chân): bước tới ('s'), nghiêng trái/phải ('l'/'r'), chúi tới
# ('p'), ngửa ra sau ('n'). Chọn theo CHÍNH năng lượng của đoạn nhạc — không hardcode điệu nào:
#   • mạnh nhất (≥ EXPRESS_STEP_RATIO lần trung bình bài VÀ ≥ mức sôi động tuyệt đối) → 's' bước
#     tới (động tác to nhất, cần chỗ);
#   • khá mạnh (≥ EXPRESS_TILT_RATIO) → 'l'/'r' nghiêng qua lại, ĐỔI BÊN mỗi lần chèn;
#   • còn lại → 'p'/'n' chúi/ngửa, phiên nhau (bài êm vẫn có động tác, không cần sôi động).
# Cửa sổ chọn LỆCH NHAU (EXPRESS_WINDOW_OFFSET) so với cửa sổ vẫy ⇒ 's'/'l'/… không rơi vào đúng
# đoạn 'w' và không bao giờ đứng sát một đoạn 'w'; mỗi cửa sổ chỉ đổi 1 đoạn.
EXPRESS_WINDOW_SEGMENTS = 4
EXPRESS_WINDOW_OFFSET = 2
EXPRESS_STEP_RATIO = 1.30
EXPRESS_TILT_RATIO = 1.15
EXPRESS_MIN_ENERGY_RMS = WAVE_MIN_ENERGY_RMS
STEP_CHAR = "s"
TILT_LEFT_CHAR = "l"
TILT_RIGHT_CHAR = "r"
BOW_CHAR = "p"
LEAN_CHAR = "n"
EXPRESS_CHARS: frozenset[str] = frozenset(
    {STEP_CHAR, TILT_LEFT_CHAR, TILT_RIGHT_CHAR, BOW_CHAR, LEAN_CHAR}
)


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


def timeline_playback_log(profile: MusicEqProfile) -> str:
    """Human-readable segment schedule for server logs."""
    if not profile.timeline:
        return f"static mood={profile.primary}"
    parts: list[str] = []
    for i, ch in enumerate(profile.timeline):
        state = SPECIAL_CHAR_NAMES.get(ch) or CHAR_TO_STATE.get(ch, "?")
        t0 = i * profile.segment_ms // 1000
        t1 = (i + 1) * profile.segment_ms // 1000
        parts.append(f"{t0:02d}-{t1:02d}s:{state}")
    return " → ".join(parts)


def _spice_timeline(
    timeline: str,
    *,
    segment_ms: int,
    weights: dict[MusicStateName, float],
    max_run_ms: int = MAX_STATE_RUN_MS,
    rng: random.Random | None = None,
) -> str:
    """Phá nhàm: không để MỘT điệu kéo dài quá `max_run_ms`.

    EQ thật có thể trả về hàng chục đoạn cùng một state (gặp thật 24-09: 84 s liên tục "drop") ⇒
    robot lặp mãi một động tác và người xem thấy nhàm. Vượt hạn thì đoạn kế tiếp được đổi sang
    một state KHÁC, chọn NGẪU NHIÊN CÓ TRỌNG SỐ theo EQ cả bài (state nhiều năng lượng hơn thì dễ
    được chọn hơn) nên vẫn hợp nhạc mà không lặp. Seed cố định ⇒ cùng bài cho cùng kết quả.

    Nếu BÀI chỉ có ít điệu (< MIN_SPICE_MOODS, ví dụ gần như một màu "drop") thì việc đổi qua lại
    2-3 điệu vẫn nhàm ⇒ mở rộng danh sách chọn ra cả 5 điệu của hệ với trọng số nền nhỏ, để có
    đủ biến hoá. Các điệu thật của bài vẫn được ưu tiên (trọng số EQ cao hơn).
    """
    if not timeline:
        return timeline
    rng = rng or random.Random(0xC0FFEE)
    max_run = max(1, int(max_run_ms // max(segment_ms, 1)))
    chars = list(timeline)

    song_states = {CHAR_TO_STATE[c] for c in timeline if c in CHAR_TO_STATE}
    if len(song_states) < MIN_SPICE_MOODS:
        # Bài gần như một màu ⇒ đổi qua lại 1-2 điệu vẫn nhàm ⇒ mở rộng ra CẢ 5 điệu của hệ.
        pool = {s: max(float(weights.get(s, 0.0)), SPICE_FLOOR_WEIGHT) for s in STATE_ORDER}
        logger.info(
            "[mv] dance EQ spice: bai chi co %d dieu %s -> mo rong danh sach chon ra 5 dieu",
            len(song_states),
            sorted(song_states),
        )
    else:
        # Đủ điệu thì CHỈ chọn trong chính các điệu CỦA BÀI (trọng số EQ ⇒ điệu "đậm" dễ được
        # chọn hơn), không kéo thêm điệu ngoài bài.
        pool = {s: float(weights.get(s, 0.0)) or 1.0 for s in song_states}

    run_char = chars[0]
    run_len = 1
    for i in range(1, len(chars)):
        if chars[i] == run_char:
            run_len += 1
        else:
            run_char, run_len = chars[i], 1
        if run_len <= max_run:
            continue
        current = CHAR_TO_STATE.get(run_char)
        candidates = {s: w for s, w in pool.items() if s != current}
        if not candidates:
            candidates = {s: 1.0 for s in STATE_ORDER if s != current}
        if not candidates:
            continue
        pick = rng.choices(list(candidates), weights=list(candidates.values()), k=1)[0]
        chars[i] = STATE_TO_CHAR[pick]
        run_char, run_len = chars[i], 1
    return "".join(chars)


def _insert_wave_segments(timeline: str, segment_energy: list[float]) -> str:
    """Chèn chữ 'w' (đoạn vẫy chân chào) vào đoạn SÔI ĐỘNG NHẤT của mỗi cửa sổ WAVE_WINDOW_SEGMENTS.

    `segment_energy` là năng lượng RMS trung bình của từng đoạn (cùng chỉ số với `timeline`).
    Đoạn được chọn còn phải mạnh hơn `WAVE_MIN_ENERGY_RMS` (mức "sôi động" tuyệt đối) và hơn
    `WAVE_MIN_ENERGY_RATIO` lần trung bình cả bài, nếu không thì bài êm sẽ không có đoạn vẫy nào.
    """
    if not timeline or not segment_energy or len(timeline) < WAVE_WINDOW_SEGMENTS:
        return timeline
    mean = float(np.mean(segment_energy)) if segment_energy else 0.0
    if mean <= 0.0:
        return timeline
    chars = list(timeline)
    inserted = 0
    for start in range(0, len(chars), WAVE_WINDOW_SEGMENTS):
        window = segment_energy[start : start + WAVE_WINDOW_SEGMENTS]
        if not window:
            continue
        best = int(np.argmax(window))
        if window[best] < WAVE_MIN_ENERGY_RMS or window[best] < mean * WAVE_MIN_ENERGY_RATIO:
            continue
        if chars[start + best] == WAVE_CHAR:
            continue
        chars[start + best] = WAVE_CHAR
        inserted += 1
    if inserted:
        logger.info("[mv] dance EQ: chen %d doan 'w' (vay chan) vao cho soi dong nhat", inserted)
    return "".join(chars)


def _insert_express_segments(timeline: str, segment_energy: list[float]) -> str:
    """Chèn đoạn ĐỘNG TÁC SINH ĐỘNG (bước tới / nghiêng / chúi / ngửa) vào mỗi cửa sổ đoạn nhạc.

    Chọn đoạn MẠNH NHẤT trong cửa sổ (bỏ qua đoạn đã là 'w' hoặc nằm sát đoạn 'w'), rồi chọn
    động tác theo tỉ lệ năng lượng so với trung bình cả bài — xem EXPRESS_* ở đầu file.
    """
    if not timeline or not segment_energy or len(timeline) < EXPRESS_WINDOW_SEGMENTS:
        return timeline
    mean = float(np.mean(segment_energy)) if segment_energy else 0.0
    if mean <= 0.0:
        return timeline
    chars = list(timeline)
    inserted = 0
    tilt_right = False  # lần chèn đầu nghiêng TRÁI, lần sau PHẢI (lắc qua lắc lại)
    bow = True  # 'p' (chúi) và 'n' (ngửa) phiên nhau
    for start in range(EXPRESS_WINDOW_OFFSET, len(chars), EXPRESS_WINDOW_SEGMENTS):
        window = segment_energy[start : start + EXPRESS_WINDOW_SEGMENTS]
        if not window:
            continue
        pick = -1
        for k in sorted(range(len(window)), key=lambda idx: window[idx], reverse=True):
            idx = start + k
            if chars[idx] == WAVE_CHAR:
                continue  # không đè lên đoạn vẫy chân
            # Không để 2 đoạn động tác sinh động đứng sát nhau (đứng cạnh 'w' thì được: vẫy xong
            # bước tới / nghiêng tiếp cũng tự nhiên, và nhờ vậy đoạn nhạc MẠNH NHẤT không bị bỏ phí
            # chỉ vì đoạn bên cạnh đã là 'w').
            if idx > 0 and chars[idx - 1] in EXPRESS_CHARS:
                continue
            if idx + 1 < len(chars) and chars[idx + 1] in EXPRESS_CHARS:
                continue
            pick = idx
            break
        if pick < 0:
            continue
        energy = segment_energy[pick]
        ratio = energy / mean
        if ratio >= EXPRESS_STEP_RATIO and energy >= EXPRESS_MIN_ENERGY_RMS:
            chars[pick] = STEP_CHAR
        elif ratio >= EXPRESS_TILT_RATIO:
            chars[pick] = TILT_RIGHT_CHAR if tilt_right else TILT_LEFT_CHAR
            tilt_right = not tilt_right
        else:
            chars[pick] = BOW_CHAR if bow else LEAN_CHAR
            bow = not bow
        inserted += 1
    if inserted:
        logger.info(
            "[mv] dance EQ: chen %d doan dong tac sinh dong (buoc-toi/nghieng/chui/ngua)",
            inserted,
        )
    return "".join(chars)


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
    window_rms: list[float] | None = None,
) -> tuple[str, list[float]]:
    """→ (timeline, năng lượng RMS trung bình của từng đoạn)."""
    if not window_scores:
        return "g", []
    win_ms = int(window_samples * 1000 / sample_rate)
    windows_per_segment = max(1, segment_ms // max(win_ms, 1))

    chars: list[str] = []
    segment_energy: list[float] = []
    for seg_start in range(0, len(window_scores), windows_per_segment):
        chunk = window_scores[seg_start : seg_start + windows_per_segment]
        agg = {s: 0.0 for s in STATE_ORDER}
        for ws in chunk:
            for state, score in ws.items():
                agg[state] += score
        primary, _ = _scores_to_primary_states(agg)
        chars.append(STATE_TO_CHAR[primary])
        if window_rms:
            rms_chunk = window_rms[seg_start : seg_start + windows_per_segment]
            segment_energy.append(float(np.mean(rms_chunk)) if rms_chunk else 0.0)
    return ("".join(chars) if chars else "g"), segment_energy


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
    timeline, segment_energy = _build_timeline(
        window_scores,
        sample_rate=loaded.sample_rate,
        window_samples=loaded.sample_rate // 2,
        segment_ms=segment_ms,
        window_rms=rms_vals,
    )
    # Phá nhàm: EQ thật hay trả về run rất dài cùng một state ⇒ chèn đoạn khác cho đỡ lặp.
    timeline = _spice_timeline(timeline, segment_ms=segment_ms, weights=weights)
    # Sinh động: chèn đoạn VẪY CHÂN vào những chỗ sôi động nhất (sau _spice_timeline để không bị đổi).
    timeline = _insert_wave_segments(timeline, segment_energy)
    # Sinh động thêm: bước tới / nghiêng trái-phải / chúi / ngửa — chạy SAU bước vẫy để không đè
    # lên đoạn 'w' (hàm tự bỏ qua đoạn 'w' và đoạn sát nó).
    timeline = _insert_express_segments(timeline, segment_energy)

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
