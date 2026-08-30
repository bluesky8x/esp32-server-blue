"""Unified assistant control-tag pipeline (dispatch + stream hold + TTS strip).

Single place for vol:/wx:/mv:/mem:/char:/sleep/tof: handling so connection.py
does not repeat per-path fixes (da_stream, tts_stream, prepare, flush, …).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.utils.character_switch_codec import (
    apply_char_switch_from_assistant_text,
    hold_incomplete_char_suffix,
)
from core.utils.language_runtime import apply_locale_to_connection  # noqa: F401  (re-export convenience)
from core.utils.locale_tag_codec import (
    apply_locale_tag_from_assistant_text,
    extract_locale_from_assistant_text,
    hold_incomplete_locale_suffix,
)
from core.utils.memory_tag_codec import (
    apply_mem_tags_from_assistant_text,
    hold_incomplete_mem_suffix,
)
from core.utils.robot_move_codec import (
    finalize_stream_text_for_tts,
    prepare_stream_chunk_for_tts,
    split_robot_move_tags,
)
from core.utils.sleep_tag_codec import (
    apply_sleep_tag_from_assistant_text,
    hold_incomplete_sleep_suffix,
)
from core.utils.tts_tag_sanitize import (
    may_contain_control_tags,
    strip_control_tags_for_tts,
)
from core.utils.weather_tag_codec import hold_incomplete_wx_suffix
from core.utils.voiceprint_tag_codec import (
    apply_vpr_tags_from_assistant_text,
    hold_incomplete_vpr_suffix,
)

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


@dataclass
class TagStreamHold:
    mv: str = ""
    wx: str = ""
    char: str = ""
    mem: str = ""
    sleep: str = ""
    vpr: str = ""
    locale: str = ""

    def merged_prefix(self) -> str:
        return self.locale + self.mv + self.char + self.mem + self.sleep + self.vpr + self.wx

    def has_suffix_hold(self) -> bool:
        return bool(self.locale or self.char or self.mem or self.sleep or self.vpr or self.wx)

    def clear(self) -> None:
        self.mv = ""
        self.wx = ""
        self.char = ""
        self.mem = ""
        self.sleep = ""
        self.vpr = ""
        self.locale = ""


def get_tag_hold_from_conn(conn: ConnectionHandler) -> TagStreamHold:
    hold = getattr(conn, "_tag_stream_hold", None)
    if isinstance(hold, TagStreamHold):
        return hold
    hold = TagStreamHold()
    conn._tag_stream_hold = hold
    return hold


def load_tag_hold_from_conn(conn: ConnectionHandler) -> TagStreamHold:
    return get_tag_hold_from_conn(conn)


def save_tag_hold_to_conn(conn: ConnectionHandler, hold: TagStreamHold) -> None:
    conn._tag_stream_hold = hold


def load_tag_hold_from_tc(tc: dict[str, Any]) -> TagStreamHold:
    return TagStreamHold(
        mv=tc.get("_da_move_hold", "") or "",
        wx=tc.get("_da_wx_hold", "") or "",
    )


def save_tag_hold_to_tc(tc: dict[str, Any], hold: TagStreamHold) -> None:
    tc["_da_move_hold"] = hold.mv
    tc["_da_wx_hold"] = hold.wx


def dispatch_control_tags_from_text(
    conn: ConnectionHandler,
    text: str,
    *,
    label: str,
    sentence_id: str | None = None,
    defer_post_tts: bool = True,
    apply_mem: bool = True,
) -> None:
    """Scan assistant text for control tags and queue device/server actions."""
    if not text or not may_contain_control_tags(text):
        return
    if apply_mem:
        apply_mem_tags_from_assistant_text(conn, text, label=label)
    # Per-reply language routing — must run before TTS so the backend is chosen.
    if extract_locale_from_assistant_text(text):
        applied = apply_locale_tag_from_assistant_text(conn, text, label=label)
        if applied:
            locale = extract_locale_from_assistant_text(text)
            logger = getattr(conn, "logger", None)
            if logger:
                logger.bind(tag="locale_tag").info(
                    f"[locale] LLM tag -> {locale} ({label})"
                )
    apply_char_switch_from_assistant_text(conn, text, label=label)
    apply_sleep_tag_from_assistant_text(conn, text, label=label)
    apply_vpr_tags_from_assistant_text(conn, text, label=label)
    conn._dispatch_vol_from_assistant_text(
        text, label=label, defer_post_tts=defer_post_tts
    )
    conn._dispatch_tof_from_assistant_text(
        text, label=label, defer_post_tts=defer_post_tts
    )
    conn._dispatch_wx_from_assistant_text(
        text, label=label, defer_post_tts=defer_post_tts
    )
    if sentence_id is not None:
        conn._dispatch_mv_from_assistant_text(
            sentence_id, text, label=label, defer_post_tts=defer_post_tts
        )


def prepare_final_assistant_text_for_tts(
    conn: ConnectionHandler,
    text: str,
    *,
    sentence_id: str | None = None,
    label: str = "prepare_llm_text_for_tts",
    trim_edges: bool = False,
    dispatch: bool = True,
) -> str:
    """Non-streaming path: dispatch tags then return spoken text."""
    if not text:
        return ""
    if dispatch:
        dispatch_control_tags_from_text(
            conn,
            text,
            label=label,
            sentence_id=sentence_id,
            defer_post_tts=True,
            apply_mem=True,
        )
    if may_contain_control_tags(text):
        cleaned, _ = split_robot_move_tags(text, trim_edges=trim_edges)
        return strip_control_tags_for_tts(cleaned, trim_edges=trim_edges)
    return text.strip() if trim_edges else text


def process_assistant_stream_chunk(
    conn: ConnectionHandler,
    new_text: str,
    hold: TagStreamHold,
    *,
    sentence_id: str | None,
    label: str,
    flush: bool = False,
    default_mv_sec: int,
    max_mv_sec: int,
    allow_mv_inference: bool = False,
    apply_mem: bool | None = None,
) -> tuple[str | None, TagStreamHold, list]:
    """Streaming path: merge holds, dispatch tags, return TTS-safe fragment."""
    if apply_mem is None:
        apply_mem = flush

    if flush:
        new_text = hold.merged_prefix() + (new_text or "")
        hold.clear()
    elif hold.has_suffix_hold():
        suffix = hold.locale + hold.char + hold.mem + hold.sleep + hold.vpr + hold.wx
        hold.locale = ""
        hold.char = ""
        hold.mem = ""
        hold.sleep = ""
        hold.vpr = ""
        hold.wx = ""
        new_text = suffix + (new_text or "")

    if not new_text and not flush:
        return None, hold, []

    # Hot path: plain speech chunk with no pending tag suffix.
    if (
        not flush
        and not hold.mv
        and not may_contain_control_tags(new_text)
    ):
        spoken = new_text
        if not spoken or not spoken.strip():
            return None, hold, []
        return spoken, hold, []

    raw = new_text or ""
    work, hold.char = hold_incomplete_char_suffix(raw)
    work, hold.locale = hold_incomplete_locale_suffix(work)
    work, hold.mem = hold_incomplete_mem_suffix(work)
    work, hold.sleep = hold_incomplete_sleep_suffix(work)
    work, hold.vpr = hold_incomplete_vpr_suffix(work)
    work, hold.wx = hold_incomplete_wx_suffix(work, allow_complete=flush)

    mv_steps: list = []
    if flush and work:
        cleaned, mv_steps = finalize_stream_text_for_tts(
            work,
            default_sec=default_mv_sec,
            max_sec=max_mv_sec,
            allow_inference=allow_mv_inference,
        )
        hold.mv = ""
    else:
        chunk = hold.mv + work
        cleaned, hold.mv, mv_steps = prepare_stream_chunk_for_tts(
            chunk, default_sec=default_mv_sec, max_sec=max_mv_sec
        )

    dispatch_control_tags_from_text(
        conn,
        work or raw,
        label=label,
        sentence_id=sentence_id,
        defer_post_tts=True,
        apply_mem=apply_mem,
    )

    if mv_steps:
        conn._dispatch_robot_move_steps(
            sentence_id, mv_steps, defer_post_tts=True
        )
    elif cleaned:
        mv_label = f"{label}_final" if flush else label
        conn._dispatch_mv_from_assistant_text(
            sentence_id, cleaned, label=mv_label, defer_post_tts=True
        )

    spoken = strip_control_tags_for_tts(cleaned or "", trim_edges=flush)
    if not spoken or not spoken.strip():
        return None, hold, mv_steps
    return spoken, hold, mv_steps
