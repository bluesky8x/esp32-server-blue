"""Split long assistant text into TTS-safe chunks (sentence-first, size-capped)."""

from __future__ import annotations

import re

# VieNeu / local HTTP TTS: ~150–180 chars ≈ 5–8s speech; 15s timeout safe margin.
DEFAULT_MAX_CHARS = 160
DEFAULT_MAX_WORDS = 35

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;])\s+")
_VI_CONJ_SPLIT_RE = re.compile(
    r"\s+(?:nhưng|nên|mà|còn|vì|với|hoặc|hay)\s+",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _within_limits(text: str, max_chars: int, max_words: int) -> bool:
    return len(text) <= max_chars and _word_count(text) <= max_words


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def _split_at_word_boundary(text: str, max_chars: int) -> tuple[str, str]:
    text = text.strip()
    if len(text) <= max_chars:
        return text, ""
    cut = text.rfind(" ", 0, max_chars + 1)
    if cut <= 0:
        return text[:max_chars].strip(), text[max_chars:].strip()
    return text[:cut].strip(), text[cut:].strip()


def _split_long_piece(text: str, *, max_chars: int, max_words: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if _within_limits(text, max_chars, max_words):
        return [text]

    markers = list(_VI_CONJ_SPLIT_RE.finditer(text))
    parts = _VI_CONJ_SPLIT_RE.split(text)
    if len(parts) > 1:
        chunks: list[str] = []
        for i, piece in enumerate(parts):
            piece = piece.strip()
            if not piece:
                continue
            if i > 0 and i - 1 < len(markers):
                conj = markers[i - 1].group(0).strip()
                piece = f"{conj} {piece}"
            if _within_limits(piece, max_chars, max_words):
                chunks.append(piece)
            else:
                chunks.extend(
                    _split_long_piece(piece, max_chars=max_chars, max_words=max_words)
                )
        return _merge_tiny_tails(chunks, max_chars=max_chars, max_words=max_words)

    chunks = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        clause = clause.strip()
        if not clause:
            continue
        if _within_limits(clause, max_chars, max_words):
            chunks.append(clause)
            continue
        rest = clause
        while rest and not _within_limits(rest, max_chars, max_words):
            head, rest = _split_at_word_boundary(rest, max_chars)
            if head:
                chunks.append(head)
            if head == rest:
                break
        if rest and rest.strip():
            if _within_limits(rest, max_chars, max_words):
                chunks.append(rest.strip())
            else:
                chunks.extend(_split_long_piece(rest, max_chars=max_chars, max_words=max_words))
    return _merge_tiny_tails(chunks, max_chars=max_chars, max_words=max_words)


def _merge_tiny_tails(
    chunks: list[str], *, max_chars: int, max_words: int
) -> list[str]:
    if len(chunks) < 2:
        return chunks
    out = list(chunks)
    if len(out[-1]) < 28 and len(out) >= 2:
        merged = f"{out[-2]} {out[-1]}".strip()
        if _within_limits(merged, max_chars, max_words):
            out = out[:-2] + [merged]
    return out


def split_text_for_tts(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_words: int = DEFAULT_MAX_WORDS,
) -> list[str]:
    """Return ordered chunks suitable for one TTS HTTP request each."""
    text = (text or "").strip()
    if not text:
        return []
    if _within_limits(text, max_chars, max_words):
        return [text]

    sentences = _split_sentences(text)
    if not sentences:
        sentences = [text]

    out: list[str] = []
    buf = ""
    for sent in sentences:
        if not sent:
            continue
        candidate = f"{buf} {sent}".strip() if buf else sent
        if _within_limits(candidate, max_chars, max_words):
            buf = candidate
            continue
        if buf:
            out.append(buf)
            buf = ""
        if _within_limits(sent, max_chars, max_words):
            buf = sent
        else:
            out.extend(_split_long_piece(sent, max_chars=max_chars, max_words=max_words))
    if buf:
        out.append(buf)
    return _merge_tiny_tails(
        [c for c in out if c.strip()], max_chars=max_chars, max_words=max_words
    )


def take_tts_prefix(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_words: int = DEFAULT_MAX_WORDS,
    punctuations: tuple[str, ...] = (".", "?", "!", "…", "。", "？", "！"),
    first_sentence: bool = False,
) -> tuple[str, str]:
    """Take one speakable prefix from buffer; return (prefix, remainder)."""
    text = text or ""
    if not text.strip():
        return "", ""

    if _within_limits(text.strip(), max_chars, max_words):
        return text.strip(), ""

    puncs = punctuations
    if first_sentence:
        puncs = (",", "，", "、", *punctuations)

    best = -1
    for punct in puncs:
        pos = text.find(punct)
        if pos != -1:
            end = pos + len(punct)
            if best == -1 or end < best:
                best = end

    if best != -1:
        prefix = text[:best].strip()
        rest = text[best:].lstrip()
        if prefix:
            if not _within_limits(prefix, max_chars, max_words):
                parts = _split_long_piece(prefix, max_chars=max_chars, max_words=max_words)
                if parts:
                    head = parts[0]
                    tail = text[len(head) :].lstrip()
                    return head, tail
            return prefix, rest

    chunks = split_text_for_tts(text, max_chars=max_chars, max_words=max_words)
    if not chunks:
        return text.strip(), ""
    if len(chunks) == 1:
        return chunks[0], ""
    return chunks[0], text[len(chunks[0]) :].lstrip()
