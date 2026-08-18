"""Index ./music and resolve files by name (fuzzy), online search, or random fallback."""

from __future__ import annotations

import difflib
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

logger = logging.getLogger(__name__)

MatchReason = Literal["search", "online", "dance_track", "random", "no_files", "missing_dir"]

_EXPLICIT_SONG_MARKERS = re.compile(
    r"(?:"
    r"(?:theo|cùng|với)\s+(?:bài\s+hát|bai\s+hat|bài|bai|ca\s+khúc|ca\s+khuc|bản\s+nhạc|ban\s+nhac|nhạc|nhac|song|track)|"
    r"(?:bài\s+hát|bai\s+hat|bài|bai|ca\s+khúc|ca\s+khuc|bản\s+nhạc|ban\s+nhac)\s+|"
    r"dance\s+(?:to|along\s+to|with)\s+|"
    r"(?:play|stream)\s+(?:song|track)\s+"
    r")\s*(.+)$",
    re.IGNORECASE,
)

_GENERIC_SONG_TERMS = frozenset({
    "", "di", "đi", "coi", "xem", "nha", "nhe", "nhé", "ne", "nè", "ngay", "luon", "luôn",
    "a", "ạ", "oi", "ơi", "voi", "với", "ho", "hộ", "gium", "giùm", "giup", "giúp",
    "mot", "một", "vai", "vài", "chut", "chút", "xiu", "xíu",
    "nua", "nữa", "tiep", "tiếp", "lai", "lại", "them", "thêm", "nua di", "nữa đi",
    "dance", "nhay", "nhảy", "mua", "múa", "music", "song", "nhac", "nhạc",
    "hip hop", "hiphop", "drill", "slap house", "house", "edm", "remix", "disco", "pop",
    "random", "ngau nhien", "ngẫu nhiên", "bat ky", "bất kỳ", "bất kì", "tu do", "tự do",
    "vui", "vui ve", "vui vẻ", "soi dong", "sôi động", "hay", "dep", "đẹp",
    "gi do", "gì đó", "nao do", "nào đó", "gi vui vui", "gì vui vui", "mot bai", "một bài",
    "ban", "bạn", "minh", "mình", "toi", "tôi", "em", "anh", "chi", "chị", "robot", "kira", "lili",
    "please", "now", "for me", "along", "to", "can you", "could you", "let's", "lets", "just", "for", "me", "a", "the",
    "again", "more", "one more time", "once more", "keep dancing", "something", "anything"
})

_TRAILING_NOISE_RE = re.compile(
    r"[\s,.]+(?:đi|nha|nhé|nè|coi|xem|với|ạ|ơi|nào|giùm|giúp|hộ|kìa|chứ|thôi|nhé\s*em|nha\s*em|đi\s*nha|đi\s*nhé|đi\s*em|đi\s*nào|nữa\s*đi|tiếp\s*đi|lại\s*đi|cho\s*mình\s*xem|cho\s*em\s*xem|cho\s*anh\s*xem|cho\s*tôi\s*xem|please|now|for\s+me)+[\s,.]*$",
    re.IGNORECASE,
)

_LEADING_NOISE_RE = re.compile(
    r"^[\s,.]*(?:can\s+you|could\s+you|please|let\'s|lets|just|bạn\s+có\s+thể|hãy|cùng|to|for|with|theo|cùng|với|bài\s+hát|bai\s+hat|bài|bai|nhạc|nhac|bản\s+nhạc|ban\s+nhac|ca\s+khúc|ca\s+khuc|song|track|một\s+bài|mot\s+bai)\s+",
    re.IGNORECASE,
)


def normalize_song_key(name: str) -> str:
    """Filename stem, lowercased, punctuation stripped."""
    stem = os.path.splitext(str(name or ""))[0]
    stem = re.sub(r"[^\w\s\u00C0-\u1EF9]", " ", stem, flags=re.UNICODE)
    return re.sub(r"\s+", " ", stem).strip().lower()


def _clean_candidate_song_query(candidate: str | None) -> str | None:
    if not candidate:
        return None
    c = str(candidate).strip()
    for _ in range(2):
        c = _TRAILING_NOISE_RE.sub("", c).strip()
        c = _LEADING_NOISE_RE.sub("", c).strip()
        c = re.sub(r"^(?:hát|hat)\s+", "", c, flags=re.IGNORECASE).strip()
    c = re.sub(r"\s+", " ", c).strip()
    c = re.sub(r"^[^\w\s\u00C0-\u1EF9]+|[^\w\s\u00C0-\u1EF9]+$", "", c).strip()
    if len(c) < 2 or c.lower() in _GENERIC_SONG_TERMS:
        return None
    return c


def extract_song_query_from_user_text(text: str | None) -> str | None:
    """
    Pull a probable song title from the user's utterance.
    Requires explicit song markers (e.g. 'bài ...', 'dance to ...') so regular
    conversational dance requests (e.g. 'Bạn hãy nhảy nữa', 'nhảy đi') do NOT
    falsely trigger internet song searches.
    """
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    m = _EXPLICIT_SONG_MARKERS.search(raw)
    if m:
        return _clean_candidate_song_query(m.group(1))
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
    allow_online_search: bool = True,
) -> tuple[Path | None, MatchReason]:
    """
    Pick a music file under ./music or search online:

    1. Fuzzy search local files by *query* (user song name)
    2. Search online (SoundCloud / YouTube) via yt-dlp if *allow_online_search* and *query* provided
    3. Optional ``dance{track}.*`` hint when *prefer_dance_track*
    4. Random from index
    """
    cache = refresh_music_index(conn)
    music_dir = Path(cache["music_dir"])
    files: list[str] = list(cache.get("music_files") or [])
    exts = cache.get("music_ext") or (".mp3", ".wav", ".p3")

    search_q = (query or "").strip()
    if search_q:
        extracted = extract_song_query_from_user_text(search_q)
        if extracted:
            search_q = extracted

    # 1. Try local music library first
    if search_q and music_dir.is_dir() and files:
        hit = find_best_music_match(search_q, files)
        if hit:
            return music_dir / hit, "search"

    # 2. Try online search if requested and song query provided
    if search_q and allow_online_search:
        from core.utils.internet_music import search_and_download_online_music
        online_path, online_title = search_and_download_online_music(search_q)
        if online_path and online_path.is_file():
            return online_path, "online"

    # 3. Try dance track preset hint
    if prefer_dance_track and track and music_dir.is_dir():
        dance_path = find_dance_track_file(track, music_dir, exts)
        if dance_path is not None:
            return dance_path, "dance_track"

    # 4. Fallback to random from local index
    if music_dir.is_dir() and files:
        rel = random.choice(files)
        return music_dir / rel, "random"

    if not music_dir.is_dir():
        return None, "missing_dir"

    return None, "no_files"
