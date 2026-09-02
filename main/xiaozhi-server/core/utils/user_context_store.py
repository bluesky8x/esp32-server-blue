"""Per-user per-day conversation context persistence.

Each registered speaker keeps their own conversation text (so multiple users'
topics don't mix). One file per speaker per DAY, so recent days stay distinct:

    <server>/data/<character>/users/<speaker>/<YYYY-MM-DD>.txt
    (e.g. data/kira/users/Mr Blue/2026-08-30.txt)

File format — one line per utterance, role-prefixed so we can rebuild the
LLM dialogue with correct roles:

    user: Mình thích cà phê
    assistant: Dạ, mình nhớ bạn thích cà phê

Rules:
- ``save_context`` writes today's file, capped at ``MAX_LINES`` (20) lines.
- ``load_context`` loads the most recent ``CONTEXT_KEEP_DAYS`` (3) day files.
- Files older than ``STALE_CONTEXT_DAYS`` (3) days are auto-deleted.
- ``save_context`` is a plain sync call — call it from a background thread.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_LINES = 20  # max lines kept per day file (smaller context per user)
SAVE_INTERVAL_SEC = 120  # only save if the last save is older than 2 minutes
CONTEXT_KEEP_DAYS = 3  # load the 3 most recent day files into context
STALE_CONTEXT_DAYS = 3  # delete day files older than this
_CLEANUP_INTERVAL_SEC = 3600  # run the stale-file scan at most once per hour
_last_cleanup_time: float = 0.0

_SAFE_CHAR_RE = re.compile(r"[^a-z0-9_]", re.IGNORECASE)


def _server_data_dir() -> Path:
    # <server>/data  (core/utils/user_context_store.py -> xiaozhi-server)
    return Path(__file__).resolve().parent.parent.parent / "data"


def _character_dir(character: str) -> Path:
    name = (_SAFE_CHAR_RE.sub("_", str(character or "kira").strip().lower()).strip("_")) or "kira"
    return _server_data_dir() / name / "users"


def _speaker_dir(character: str, speaker: str) -> Path:
    safe = re.sub(r"[^\w\u00C0-\u1EF9]+", "_", (speaker or "").strip(), flags=re.UNICODE).strip("_")
    safe = safe[:40] or "unknown"
    return _character_dir(character) / safe


def _day_path(character: str, speaker: str, day: str) -> Path:
    return _speaker_dir(character, speaker) / f"{day}.txt"


def _day_files(character: str, speaker: str) -> list[Path]:
    """Day files for a speaker, newest first (by name = YYYY-MM-DD)."""
    d = _speaker_dir(character, speaker)
    if not d.is_dir():
        return []
    return sorted(d.glob("*.txt"), reverse=True)


def _role_prefix(role: str) -> str:
    return "user" if str(role).lower() == "user" else "assistant"


def cleanup_stale_contexts(max_age_days: int = STALE_CONTEXT_DAYS) -> int:
    """Delete per-user context files untouched for > max_age_days. Returns count."""
    removed = 0
    data_dir = _server_data_dir()
    if not data_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    for char_dir in data_dir.iterdir():
        users = char_dir / "users"
        if not users.is_dir():
            continue
        for f in users.rglob("*.txt"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
    return removed


def _maybe_cleanup() -> None:
    """Throttled stale-file cleanup, run from the save worker thread."""
    global _last_cleanup_time
    now = time.time()
    if now - _last_cleanup_time < _CLEANUP_INTERVAL_SEC:
        return
    _last_cleanup_time = now
    try:
        removed = cleanup_stale_contexts()
        if removed:
            logger.info("[user_context] cleaned %d stale context file(s)", removed)
    except Exception:
        pass


def save_context(character: str, speaker: str, pairs: list[tuple[str, str]]) -> None:
    """Persist the latest MAX_LINES (role, content) pairs to the speaker's file.

    ``pairs`` = [(role, content), ...] in conversation order. Runs synchronously;
    call it from a worker thread so it never blocks the event loop.
    """
    _maybe_cleanup()
    if not speaker:
        return
    lines = [f"{_role_prefix(role)}: {text}" for role, text in pairs if text]
    if not lines:
        return
    keep = lines[-MAX_LINES:]
    day = time.strftime("%Y-%m-%d")
    path = _day_path(character, speaker, day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        logger.info(
            "[user_context] saved %d lines -> %s (speaker=%s)",
            len(keep), path, speaker,
        )
    except Exception as exc:
        logger.warning("[user_context] save failed %s: %s", path, exc)


def load_context(character: str, speaker: str) -> list[tuple[str, str]]:
    """Load the CONTEXT_KEEP_DAYS most recent day files as [(role, content)].

    Files are read oldest → newest so the rebuilt history stays in order.
    """
    if not speaker:
        return []
    files = list(reversed(_day_files(character, speaker)[:CONTEXT_KEEP_DAYS]))
    if not files:
        return []
    pairs: list[tuple[str, str]] = []
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning("[user_context] load failed %s: %s", path, exc)
            continue
        for line in raw[-MAX_LINES:]:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("user:"):
                pairs.append(("user", line[5:].strip()))
            elif line.lower().startswith("assistant:"):
                pairs.append(("assistant", line[10:].strip()))
            else:
                # No role marker (legacy/raw line) — treat as user speech.
                pairs.append(("user", line))
    if not pairs:
        return []
    logger.info(
        "[user_context] loaded %d lines from %d day file(s) (speaker=%s)",
        len(pairs), len(files), speaker,
    )
    return pairs
