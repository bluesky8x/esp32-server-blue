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


class _DownloadTooLargeError(Exception):
    """Internal signal raised from a yt-dlp progress hook to abort an oversized download."""


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


def find_cached_online_music(
    query: str | None,
    cache_dir: Path | str | None = None,
) -> tuple[Path | None, str | None]:
    """Search the on-disk music cache by song name (fuzzy) — no network.

    Previously downloaded files are named ``<slug>_[<yt id>]_[<hash>].mp3``.
    This resolves them by title so a known song plays straight from cache
    instead of triggering a fresh online search/download. Call it before the
    online step.
    """
    if not query or not str(query).strip():
        return None, None
    raw_query = str(query).strip()
    norm_key = _normalize_query_key(raw_query)
    if not norm_key or len(norm_key) < 2:
        return None, None

    target = _get_cache_dir(cache_dir)
    candidates: list[str] = []
    mapping: dict[str, Path] = {}
    for mp3 in sorted(target.glob("*.mp3")):
        stem = mp3.stem
        # Strip trailing "[<yt id>]_[<hash>]" from the cached filename.
        title_part = re.sub(r"_\[[^\]]*\]_\[[a-f0-9]{16}\]$", "", stem)
        title = (title_part or stem).replace("_", " ").strip()
        if not title:
            continue
        key = _normalize_query_key(title)
        if key in mapping:
            continue
        mapping[key] = mp3
        candidates.append(title)

    if not candidates:
        return None, None

    from difflib import SequenceMatcher

    from core.utils.music_library import find_best_music_match, normalize_song_key

    hit = find_best_music_match(norm_key, candidates)
    if hit:
        # Cache hits must be confident — a weak fuzzy match here would play a
        # totally different song; prefer falling through to the online search.
        qk = normalize_song_key(norm_key)
        hk = normalize_song_key(hit)
        ratio = SequenceMatcher(None, qk, hk).ratio()
        if ratio >= 0.6:
            path = mapping.get(_normalize_query_key(hit))
            if path and path.is_file():
                logger.info(
                    "[music] cache hit (by name): %s -> %s", raw_query, path.name
                )
                return path, hit
        else:
            logger.info(
                "[music] cache name too weak (%.2f) for %r — going online",
                ratio,
                hit,
            )

    # Fallback: exact query-hash cache key (same lookup as the download path).
    # NOTE: f"[{q_hash}]" must be a plain substring check — using it inside a
    # glob (e.g. f"*[{q_hash}]*.mp3") treats the brackets as a CHARACTER CLASS
    # and matches almost every cached file (the "Tuyết lạnh → baby_shark" bug).
    q_hash = hashlib.sha256(norm_key.encode("utf-8")).hexdigest()[:16]
    for existing in target.glob("*.mp3"):
        if f"[{q_hash}]" not in existing.name:
            continue
        if not existing.is_file():
            continue
        title_part = re.sub(r"_\[[^\]]*\]_\[[a-f0-9]{16}\]$", "", existing.stem)
        title = (title_part or existing.stem).replace("_", " ").strip()
        logger.info(
            "[music] cache hit (by hash): %s -> %s", raw_query, existing.name
        )
        return existing, title
    return None, None


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

    # Target: MP3 at 192kbps ~ 1.44 MB/min → 10 MB ≈ ~7 min cap
    MAX_FILESIZE_BYTES = 10 * 1024 * 1024  # 10 MB

    abort_state = {"triggered": False}

    def _too_large_hook(data: dict) -> None:
        """Hard-stop the transfer the moment we cross the cap.

        yt-dlp's ``max_filesize`` only works when the provider reports a size up
        front — YouTube/SoundCloud often don't — so without this hook the whole
        ~100 MB stream is pulled before any post-check runs.
        """
        if data.get("status") != "downloading":
            return
        total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        downloaded = int(data.get("downloaded_bytes") or 0)
        if total > MAX_FILESIZE_BYTES or downloaded > MAX_FILESIZE_BYTES:
            abort_state["triggered"] = True
            raise _DownloadTooLargeError(max(total, downloaded))

    def _cleanup_partial() -> None:
        """Remove .part / .ytdl leftovers from an aborted download."""
        for leftover in target_cache_dir.glob(f"*{q_hash}*"):
            if leftover.suffix in (".part", ".ytdl") or ".part" in leftover.name:
                try:
                    leftover.unlink()
                except Exception:
                    pass

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
        "max_filesize": MAX_FILESIZE_BYTES,  # skip if provider reports size > 10 MB
        "progress_hooks": [_too_large_hook],  # hard-stop mid-stream at 10 MB
    }

    # Try SoundCloud first, then YouTube search
    for search_prefix, provider_name in (("scsearch1", "SoundCloud"), ("ytsearch1", "YouTube")):
        try:
            abort_state["triggered"] = False
            opts = dict(base_ydl_opts)
            opts["default_search"] = search_prefix
            with yt_dlp.YoutubeDL(opts) as ydl:
                search_query = f"{search_prefix}:{raw_query}"
                info = ydl.extract_info(search_query, download=True)
                if abort_state["triggered"]:
                    # extract_info may swallow the abort; clean up and try next source
                    _cleanup_partial()
                    continue
                if info and "entries" in info and info["entries"]:
                    entry = info["entries"][0]
                    if entry:
                        title = entry.get("title") or raw_query
                        for mp3_candidate in target_cache_dir.glob(f"*{q_hash}*.mp3"):
                            if not mp3_candidate.is_file():
                                continue
                            size = mp3_candidate.stat().st_size
                            if size > MAX_FILESIZE_BYTES:
                                logger.warning(
                                    "[music] %s result too large (%d MB > 10 MB), skipping: %s",
                                    provider_name,
                                    size // (1024 * 1024),
                                    mp3_candidate.name,
                                )
                                try:
                                    mp3_candidate.unlink()
                                except Exception:
                                    pass
                                break
                            logger.info(
                                "[music] online search success (%s): %s (%s, %d bytes)",
                                provider_name,
                                title,
                                mp3_candidate.name,
                                size,
                            )
                            _ONLINE_MUSIC_CACHE[norm_key] = (mp3_candidate, title)
                            return mp3_candidate, title
        except _DownloadTooLargeError as exc:
            size_mb = (exc.args[0] if exc.args else 0) // (1024 * 1024)
            logger.warning(
                "[music] %s download aborted >10 MB (stopped at ~%d MB), skipping",
                provider_name,
                size_mb,
            )
            _cleanup_partial()
        except Exception as exc:
            logger.warning("[music] %s search failed for %r: %s", provider_name, raw_query, exc)

    logger.warning("[music] no online music found for query: %r", raw_query)
    return None, None

