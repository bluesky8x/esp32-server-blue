"""Unified assistant control-tag pipeline (dispatch + stream hold + TTS strip).

Single place for vol:/wx:/mv:/mem:/char:/sleep/tof: handling so connection.py
does not repeat per-path fixes (da_stream, tts_stream, prepare, flush, …).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.utils.tts_tag_sanitize import strip_control_tags_for_tts

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


@dataclass
class TagStreamHold:
    mv: str = ""
    wx: str = ""
    char: str = ""
    mem: str = ""
    sleep: str = ""

    def merged_prefix(self) -> str:
        return self.mv + self.char + self.mem + self.sleep + self.wx

    def clear(self) -> None:
        self.mv = ""
        self.wx = ""
        self.char = ""
        self.mem = ""
        self.sleep = ""


def load_tag_hold_from_conn(conn: ConnectionHandler) -> TagStreamHold:
    return TagStreamHold(
        mv=getattr(conn, "_move_tag_stream_hold", "") or "",
        wx=getattr(conn, "_wx_tag_stream_hold", "") or "",
        char=getattr(conn, "_char_tag_stream_hold", "") or "",
        mem=getattr(conn, "_mem_tag_stream_hold", "") or "",
        sleep=getattr(conn, "_sleep_tag_stream_hold", "") or "",
    )


def save_tag_hold_to_conn(conn: ConnectionHandler, hold: TagStreamHold) -> None:
    conn._move_tag_stream_hold = hold.mv
    conn._wx_tag_stream_hold = hold.wx
    conn._char_tag_stream_hold = hold.char
    conn._mem_tag_stream_hold = hold.mem
    conn._sleep_tag_stream_hold = hold.sleep


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
    if not text:
        return
    from core.utils.memory_tag_codec import apply_mem_tags_from_assistant_text
    from core.utils.character_switch_codec import apply_char_switch_from_assistant_text
    from core.utils.sleep_tag_codec import apply_sleep_tag_from_assistant_text

    if apply_mem:
        apply_mem_tags_from_assistant_text(conn, text, label=label)
    apply_char_switch_from_assistant_text(conn, text, label=label)
    apply_sleep_tag_from_assistant_text(conn, text, label=label)
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
) -> str:
    """Non-streaming path: dispatch tags then return spoken text."""
    from core.utils.robot_move_codec import split_robot_move_tags

    if not text:
        return ""
    dispatch_control_tags_from_text(
        conn,
        text,
        label=label,
        sentence_id=sentence_id,
        defer_post_tts=True,
        apply_mem=True,
    )
    cleaned, _ = split_robot_move_tags(text, trim_edges=trim_edges)
    return strip_control_tags_for_tts(cleaned, trim_edges=trim_edges)


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
    from core.utils.character_switch_codec import hold_incomplete_char_suffix
    from core.utils.memory_tag_codec import hold_incomplete_mem_suffix
    from core.utils.robot_move_codec import (
        finalize_stream_text_for_tts,
        prepare_stream_chunk_for_tts,
    )
    from core.utils.sleep_tag_codec import hold_incomplete_sleep_suffix
    from core.utils.weather_tag_codec import hold_incomplete_wx_suffix

    if apply_mem is None:
        apply_mem = flush

    if flush:
        new_text = hold.merged_prefix() + (new_text or "")
        hold.clear()
    elif hold.char or hold.mem or hold.sleep or hold.wx:
        suffix = hold.char + hold.mem + hold.sleep + hold.wx
        hold.char = ""
        hold.mem = ""
        hold.sleep = ""
        hold.wx = ""
        new_text = suffix + (new_text or "")

    if not new_text and not flush:
        return None, hold, []

    raw = new_text or ""
    work, hold.char = hold_incomplete_char_suffix(raw)
    work, hold.mem = hold_incomplete_mem_suffix(work)
    work, hold.sleep = hold_incomplete_sleep_suffix(work)
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
