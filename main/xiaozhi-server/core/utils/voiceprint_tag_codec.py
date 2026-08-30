"""Voiceprint control tag in LLM replies (same pattern as mv:*, mem:*, sleep).

Protocol: append ``vpr:<command>`` at the **very end** of the assistant reply,
e.g. ``Vâng ạ. Vui lòng nói mật khẩu xác nhận nhé. vpr:resample``.

The tag is stripped before TTS; the server dispatches the command.

Commands:
  resample — start the admin voice re-enrollment flow (password + sample).
             Gated by the multi-user voice feature (voiceprint.enroll_enabled).
             The spoken password ("abcd1234") is the authentication gate — the
             server verifies it in the enrollment state machine.
  enroll[:<name>] — start NEW-user enrollment, ONLY when the admin asks to add
             a user (e.g. "bạn Lucy muốn làm quen với bạn"). The optional name
             after ``:`` comes from the LLM (never hardcoded on the server) and
             pre-fills the new user's name; without a name the robot asks for
             it. Unknown voices are ignored by default — this tag is the only
             trigger for onboarding.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

VPR_COMMANDS = frozenset({"resample", "enroll"})

_VPR_TAG_RE = re.compile(
    r"\bvpr\s*:\s*(resample|enroll)(?:\s*:\s*([^\"'\n\r]{1,30}))?\b",
    re.IGNORECASE,
)
VPR_TAG_STRIP_RE = _VPR_TAG_RE

_INCOMPLETE_VPR_SUFFIX_RE = re.compile(
    r"(?:\s+vpr(?:\s*:\s*(?:[a-z]{1,12})(?:\s*:\s*[^\"'\n\r]{0,20})?)?)$",
    re.IGNORECASE,
)


def extract_vpr_tags(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(1).lower() for m in _VPR_TAG_RE.finditer(str(text))]


def extract_vpr_enroll_name(text: str) -> str | None:
    """Name carried by ``vpr:enroll:<name>`` (from the LLM), if any."""
    if not text:
        return None
    for m in _VPR_TAG_RE.finditer(str(text)):
        if m.group(1).lower() == "enroll" and m.group(2):
            name = m.group(2).strip().strip("\"'")
            if name:
                return name
    return None


def has_vpr_tag(text: str) -> bool:
    return bool(extract_vpr_tags(text))


def strip_vpr_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = _VPR_TAG_RE.sub("", str(text))
    # A trailing "vpr:enroll:" (colon with no name) leaves a stray ":" — drop it.
    cleaned = re.sub(r"\s*:\s*$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip() if trim_edges else cleaned


def hold_incomplete_vpr_suffix(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    if has_vpr_tag(text):
        return text, ""
    m = _INCOMPLETE_VPR_SUFFIX_RE.search(text)
    if m and m.group(0).strip():
        return text[: m.start()], text[m.start() :]
    return text, ""


def apply_vpr_tags_from_assistant_text(
    conn: "ConnectionHandler", text: str, *, label: str = ""
) -> bool:
    """Dispatch vpr:* tags. Only the admin may trigger a voice re-sample."""
    tags = extract_vpr_tags(text)
    if not tags:
        return False
    store = getattr(conn, "voice_user_store", None)
    if not store or not getattr(store, "enroll_enabled", False):
        logger = getattr(conn, "logger", None)
        if logger:
            logger.bind(tag="vpr_tag").info(
                f"[vpr] ignored {tags} — voiceprint multi-user feature off"
            )
        return False

    handled = False
    for cmd in tags:
        if cmd == "resample":
            from core.utils.voice_enroll import set_enroll_state

            set_enroll_state(conn, {"stage": "ask_password"})
            handled = True
        elif cmd == "enroll":
            from core.utils.voice_enroll import _clean_name, set_enroll_state

            raw_name = extract_vpr_enroll_name(text)
            clean = _clean_name(raw_name) if raw_name else ""
            if clean and not store.is_reserved_name(clean):
                set_enroll_state(
                    conn, {"stage": "sample", "pending_name": clean}
                )
            else:
                set_enroll_state(conn, {"stage": "ask_name"})
            handled = True
    logger = getattr(conn, "logger", None)
    if logger and handled:
        logger.bind(tag="vpr_tag").info(
            f"[vpr] armed {tags} (enroll/resample) from={label or 'assistant'}"
        )
    return handled
