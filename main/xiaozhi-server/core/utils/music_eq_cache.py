"""Disk + in-memory cache for dance EQ / MCP timeline profiles."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from core.utils.music_eq_analyzer import (
    MusicEqProfile,
    MusicStateName,
    STATE_ORDER,
    analyze_music_eq,
)

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
CACHE_DIR_NAME = ".eq_cache"

# path_key -> (mtime_ns, size, profile)
_memory_cache: dict[str, tuple[int, int, MusicEqProfile]] = {}


def _path_key(path: Path) -> str:
    return str(path.resolve())


def cache_dir_for_music(music_file: Path) -> Path:
    """Store cache next to the music library root (./music/.eq_cache)."""
    resolved = music_file.resolve()
    root = resolved.parent
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "music" or (parent / ".eq_cache").is_dir():
            root = parent
            break
    return root / CACHE_DIR_NAME


def cache_file_for(music_file: Path) -> Path:
    digest = hashlib.sha256(_path_key(music_file).encode("utf-8")).hexdigest()[:24]
    safe_name = music_file.stem[:48] or "track"
    return cache_dir_for_music(music_file) / f"{safe_name}_{digest}.json"


def profile_to_dict(profile: MusicEqProfile, *, source_path: str, mtime_ns: int, size: int) -> dict:
    return {
        "version": CACHE_VERSION,
        "source_path": source_path,
        "mtime_ns": mtime_ns,
        "size": size,
        "primary": profile.primary,
        "states": list(profile.states),
        "weights": {k: float(profile.weights.get(k, 0.0)) for k in STATE_ORDER},
        "tempo_hint": profile.tempo_hint,
        "bass_ratio": float(profile.bass_ratio),
        "energy": float(profile.energy),
        "timeline": profile.timeline,
        "segment_ms": int(profile.segment_ms),
    }


def profile_from_dict(data: dict) -> MusicEqProfile:
    primary = data.get("primary", "groove")
    if primary not in STATE_ORDER:
        primary = "groove"
    states_raw = data.get("states") or [primary]
    states: tuple[MusicStateName, ...] = tuple(
        s for s in states_raw if s in STATE_ORDER
    ) or (primary,)  # type: ignore[assignment]
    tempo = data.get("tempo_hint", "medium")
    if tempo not in ("slow", "medium", "fast"):
        tempo = "medium"
    weights_raw = data.get("weights") or {}
    weights = {s: float(weights_raw.get(s, 0.0)) for s in STATE_ORDER}
    return MusicEqProfile(
        primary=primary,  # type: ignore[arg-type]
        states=states,
        weights=weights,
        tempo_hint=tempo,  # type: ignore[arg-type]
        bass_ratio=float(data.get("bass_ratio", 0.0)),
        energy=float(data.get("energy", 0.0)),
        timeline=str(data.get("timeline") or ""),
        segment_ms=int(data.get("segment_ms") or 6000),
    )


def _load_disk_cache(music_file: Path, mtime_ns: int, size: int) -> MusicEqProfile | None:
    cache_path = cache_file_for(music_file)
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if int(data.get("mtime_ns", -1)) != mtime_ns or int(data.get("size", -1)) != size:
        return None
    try:
        return profile_from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def _save_disk_cache(
    music_file: Path, profile: MusicEqProfile, *, mtime_ns: int, size: int
) -> None:
    cache_path = cache_file_for(music_file)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = profile_to_dict(
            profile,
            source_path=_path_key(music_file),
            mtime_ns=mtime_ns,
            size=size,
        )
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("dance EQ cache write failed %s: %s", cache_path, exc)


def get_or_analyze_music_eq(music_file: str | Path) -> MusicEqProfile:
    """Return cached EQ timeline for *music_file*, analyzing only on miss."""
    path = Path(music_file)
    if not path.is_file():
        return analyze_music_eq(path)

    stat = path.stat()
    mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))
    size = int(stat.st_size)
    key = _path_key(path)

    mem = _memory_cache.get(key)
    if mem is not None and mem[0] == mtime_ns and mem[1] == size:
        logger.info("[mv] dance EQ cache hit (memory): %s", path.name)
        return mem[2]

    cached = _load_disk_cache(path, mtime_ns, size)
    if cached is not None:
        logger.info("[mv] dance EQ cache hit (disk): %s", path.name)
        _memory_cache[key] = (mtime_ns, size, cached)
        return cached

    logger.info("[mv] dance EQ analyzing: %s", path.name)
    profile = analyze_music_eq(path)
    _save_disk_cache(path, profile, mtime_ns=mtime_ns, size=size)
    _memory_cache[key] = (mtime_ns, size, profile)
    return profile
