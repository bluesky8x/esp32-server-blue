"""Search and download online music (SoundCloud / YouTube) for live dance and playback."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# In-memory lookup: normalized_query -> (Path, title)
_ONLINE_MUSIC_CACHE: dict[str, tuple[Path, str]] = {}


def _normalize_query_key(query: str) -> str:
    cleaned = re.sub(r"[^\w\s\u00C0-\u1EF9]", " ", query or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _get_cache_dir(custom_dir: Path | str | None = None) -> Path:
    if custom_dir:
        p = Path(custom_dir)
    else:
        # Default to ./tmp/music_cache relative to xiaozhi-server root
        p = Path(__file__).resolve().parent.parent.parent / "tmp" / "music_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def search_and_download_online_music(
    query: str | None,
    cache_dir: Path | str | None = None,
) -> tuple[Path | None, str | None]:
    """
    Search online music by title/artist query and return (local_mp3_path, song_title).
    Uses SoundCloud via yt-dlp first. Results are cached on disk and in memory.
    """
    if not query or not str(query).strip():
        return None, None

    raw_query = str(query).strip()
    norm_key = _normalize_query_key(raw_query)
    if not norm_key or len(norm_key) < 2:
        return None, None

    # 1. Check in-memory cache
    if norm_key in _ONLINE_MUSIC_CACHE:
        cached_path, cached_title = _ONLINE_MUSIC_CACHE[norm_key]
        if cached_path.is_file():
            logger.info("[music] online music cache hit (memory): %s -> %s", raw_query, cached_path.name)
            return cached_path, cached_title

    target_cache_dir = _get_cache_dir(cache_dir)

    # 2. Check disk cache by query hash or matching filename
    q_hash = hashlib.sha256(norm_key.encode("utf-8")).hexdigest()[:16]
    for existing in target_cache_dir.glob("*.mp3"):
        stem = existing.stem.lower()
        if f"[{q_hash}]" in stem:
            title = existing.stem.split(" [")[0] if " [" in existing.stem else existing.stem
            _ONLINE_MUSIC_CACHE[norm_key] = (existing, title)
            logger.info("[music] online music cache hit (disk): %s -> %s", raw_query, existing.name)
            return existing, title

    logger.info("[music] searching online music for query: %r", raw_query)

    try:
        import yt_dlp
    except ImportError:
        logger.error("[music] yt-dlp is not installed, cannot search online music")
        return None, None

    safe_slug = re.sub(r"[^a-zA-Z0-9\u00C0-\u1EF9_-]+", "_", norm_key)[:40]
    outtmpl = str(target_cache_dir / f"{safe_slug}_[%(id)s]_[{q_hash}].%(ext)s")

    base_ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }

    # Try SoundCloud first, then YouTube search
    for search_prefix, provider_name in (("scsearch1", "SoundCloud"), ("ytsearch1", "YouTube")):
        try:
            opts = dict(base_ydl_opts)
            opts["default_search"] = search_prefix
            with yt_dlp.YoutubeDL(opts) as ydl:
                search_query = f"{search_prefix}:{raw_query}"
                info = ydl.extract_info(search_query, download=True)
                if info and "entries" in info and info["entries"]:
                    entry = info["entries"][0]
                    if entry:
                        title = entry.get("title") or raw_query
                        for mp3_candidate in target_cache_dir.glob(f"*{q_hash}*.mp3"):
                            if mp3_candidate.is_file():
                                logger.info(
                                    "[music] online search success (%s): %s (%s, %d bytes)",
                                    provider_name,
                                    title,
                                    mp3_candidate.name,
                                    mp3_candidate.stat().st_size,
                                )
                                _ONLINE_MUSIC_CACHE[norm_key] = (mp3_candidate, title)
                                return mp3_candidate, title
        except Exception as exc:
            logger.warning("[music] %s search failed for %r: %s", provider_name, raw_query, exc)

    logger.warning("[music] no online music found for query: %r", raw_query)
    return None, None

