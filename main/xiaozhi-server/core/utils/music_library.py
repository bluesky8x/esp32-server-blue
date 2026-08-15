"""Index ./music and resolve files by name (fuzzy) or random fallback."""

from __future__ import annotations

import difflib
import os
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

MatchReason = Literal["search", "dance_track", "random", "no_files", "missing_dir"]

# Strip dance / playback boilerplate when extracting a song title from user STT.
_SONG_QUERY_NOISE_RE = re.compile(
    r"\b(?:"
    r"nhảy|nhay|múa|mua|dance|stream|phát|phat|bật|bat|với|voi|"
    r"bài|bai|nhạc|nhac|music|live|song|track|cho|mình|minh|em|robot|"
    r"hip[\s-]?hop|drill|slap[\s-]?house|embed|online|server|"
    r"random|ngẫu\s*nhiên|ngau\s*nhiên"
    r")\b",
    re.IGNORECASE,
)
_BAI_NHAC_PREFIX_RE = re.compile(
    r"(?:bài|bai|nhạc|nhac|song)\s+(.+)$", re.IGNORECASE
)


def normalize_song_key(name: str) -> str:
    """Filename stem, lowercased, punctuation stripped."""
    stem = os.path.splitext(str(name or ""))[0]
    stem = re.sub(r"[^\w\s\u00C0-\u1EF9]", " ", stem, flags=re.UNICODE)
    return re.sub(r"\s+", " ", stem).strip().lower()


def extract_song_query_from_user_text(text: str | None) -> str | None:
    """Pull a probable song title from the user's utterance."""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    m = _BAI_NHAC_PREFIX_RE.search(raw)
    if m:
        candidate = m.group(1).strip()
        if len(candidate) >= 2:
            return candidate
    cleaned = _SONG_QUERY_NOISE_RE.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) >= 2 and normalize_song_key(cleaned) != normalize_song_key(raw):
        return cleaned
    return None


def find_best_music_match(query: str, music_files: list[str]) -> str | None:
    """Fuzzy match *query* against indexed relative paths; None if no good hit."""
    q = normalize_song_key(query)
    if not q or not music_files:
        return None

    # Substring / exact stem match first.
    substring_hits: list[tuple[int, str]] = []
    for rel in music_files:
        stem = normalize_song_key(rel)
        if not stem:
            continue
        if q == stem:
            return rel
        if q in stem or stem in q:
            substring_hits.append((abs(len(stem) - len(q)), rel))
    if substring_hits:
        substring_hits.sort(key=lambda item: item[0])
        return substring_hits[0][1]

    best_match: str | None = None
    highest_ratio = 0.0
    for rel in music_files:
        stem = normalize_song_key(rel)
        ratio = difflib.SequenceMatcher(None, q, stem).ratio()
        if ratio > highest_ratio and ratio > 0.4:
            highest_ratio = ratio
            best_match = rel
    return best_match


def refresh_music_index(conn: ConnectionHandler) -> dict:
    """Ensure MUSIC_CACHE is loaded and refreshed if stale."""
    from plugins_func.functions.play_music import (
        MUSIC_CACHE,
        get_music_files,
        initialize_music_handler,
    )

    initialize_music_handler(conn)
    refresh_time = float(MUSIC_CACHE.get("refresh_time") or 60)
    if time.time() - float(MUSIC_CACHE.get("scan_time") or 0) > refresh_time:
        music_dir = MUSIC_CACHE["music_dir"]
        exts = MUSIC_CACHE["music_ext"]
        MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
            music_dir, exts
        )
        MUSIC_CACHE["scan_time"] = time.time()
    return MUSIC_CACHE


def find_dance_track_file(track: int, music_dir: str | Path, exts: tuple | list) -> Path | None:
    """Optional hint: dance1.mp3 / dance2.mp3 / dance3.mp3."""
    root = Path(music_dir)
    if not root.is_dir():
        return None
    stem = f"dance{track}"
    normalized: list[str] = []
    for ext in exts:
        e = str(ext).lower()
        if not e.startswith("."):
            e = f".{e}"
        normalized.append(e)
    for ext in normalized:
        direct = root / f"{stem}{ext}"
        if direct.is_file():
            return direct
    for ext in normalized:
        for hit in sorted(root.rglob(f"{stem}{ext}")):
            if hit.is_file():
                return hit
    return None


def resolve_music_path(
    conn: ConnectionHandler,
    *,
    query: str | None = None,
    track: int | None = None,
    prefer_dance_track: bool = False,
) -> tuple[Path | None, MatchReason]:
    """
    Pick a music file under ./music:

    1. Fuzzy search by *query* (user song name)
    2. Optional ``dance{track}.*`` hint when *prefer_dance_track*
    3. Random from index
    """
    cache = refresh_music_index(conn)
    music_dir = Path(cache["music_dir"])
    if not music_dir.is_dir():
        return None, "missing_dir"

    files: list[str] = list(cache.get("music_files") or [])
    if not files:
        return None, "no_files"

    exts = cache.get("music_ext") or (".mp3", ".wav", ".p3")

    search_q = (query or "").strip()
    if not search_q:
        search_q = extract_song_query_from_user_text(
            getattr(conn, "_last_user_text", None)
        ) or ""
    else:
        extracted = extract_song_query_from_user_text(search_q)
        if extracted:
            search_q = extracted

    if search_q:
        hit = find_best_music_match(search_q, files)
        if hit:
            return music_dir / hit, "search"

    if prefer_dance_track and track:
        dance_path = find_dance_track_file(track, music_dir, exts)
        if dance_path is not None:
            return dance_path, "dance_track"

    rel = random.choice(files)
    return music_dir / rel, "random"
