"""Local persistent store for voice-enrolled users + admin identity.

This is the "names" side of voice recognition. The actual voice samples live in
the external voiceprint-api service (registered via /voiceprint/register); this
store maps a speaker_id to a human-readable name and remembers who the admin
("Mr Blue") is, plus which names are reserved.

Stored at ``data/voice_users.json`` (gitignored) so enrolled users survive
restarts.
"""

from __future__ import annotations

import json
import os
import threading
import time

from datetime import date

DEFAULT_STORE_PATH = os.path.join("data", "voice_users.json")

_GLOBAL_VOICE_USER_STORE: VoiceUserStore | None = None


def get_voice_user_store(config: dict | None = None, path: str | None = None) -> VoiceUserStore:
    global _GLOBAL_VOICE_USER_STORE
    if _GLOBAL_VOICE_USER_STORE is None or (path and _GLOBAL_VOICE_USER_STORE.path != path):
        _GLOBAL_VOICE_USER_STORE = VoiceUserStore(config=config, path=path)
    return _GLOBAL_VOICE_USER_STORE


class VoiceUserStore:
    def __init__(self, config: dict | None = None, path: str | None = None):
        cfg = config or {}
        self._lock = threading.RLock()
        self.path = path or cfg.get("user_store_path") or DEFAULT_STORE_PATH

        self.admin_name = (cfg.get("admin_name") or "Mr Blue").strip()
        self.admin_password = str(cfg.get("admin_password") or "abcd1234")
        self.admin_speaker_id = (
            cfg.get("admin_speaker_id") or "mr_blue"
        ).strip()
        # Feature flag: multi-user enrollment + admin (Mr Blue) flows.
        # Default OFF (legacy behavior) — set voiceprint.enroll_enabled: true
        # in data/.config.yaml to enable.
        self.enroll_enabled = bool(cfg.get("enroll_enabled", False))

        self.reserved_names: set[str] = set()
        for name in cfg.get("reserved_names") or []:
            if str(name).strip():
                self.reserved_names.add(str(name).strip().lower())
        self.reserved_names.add(self.admin_name.strip().lower())
        self.reserved_names.add("admin")
        self.reserved_names.add("quản trị viên")
        self.reserved_names.add("robot")
        self.reserved_names.add("kira")
        self.reserved_names.add("lili")

        # speaker_id -> {"name", "is_admin", "created", "description", "daily_stories"}
        self.users: dict[str, dict] = {}
        self._load()
        self._ensure_admin()

    # ------------------------------------------------------------------ IO
    def _ensure_user_daily_stories(self, user_info: dict) -> dict:
        today = date.today().isoformat()
        ds = user_info.get("daily_stories")
        if not isinstance(ds, dict):
            ds = {"date": today, "vi_count": 0, "en_count": 0}
            user_info["daily_stories"] = ds
        elif ds.get("date") != today:
            ds["date"] = today
            ds["vi_count"] = 0
            ds["en_count"] = 0
        return ds

    def _resolve_user_entry(self, speaker: str | None = None) -> tuple[str, dict] | tuple[None, None]:
        """Find matching user dict in self.users by speaker_id or name."""
        if speaker:
            spk_clean = str(speaker).strip()
            # 1. Direct speaker_id match
            if spk_clean in self.users:
                info = self.users[spk_clean]
                self._ensure_user_daily_stories(info)
                return spk_clean, info
            # 2. Match by display name (case-insensitive)
            spk_lower = spk_clean.lower()
            for sid, info in self.users.items():
                if str(info.get("name", "")).strip().lower() == spk_lower:
                    self._ensure_user_daily_stories(info)
                    return sid, info
            # 3. Normalized / partial match
            for sid, info in self.users.items():
                cand_name = str(info.get("name", "")).strip().lower()
                if spk_lower in cand_name or cand_name in spk_lower or spk_lower.replace(" ", "_") == sid:
                    self._ensure_user_daily_stories(info)
                    return sid, info

        # If only one non-admin user exists, map to them when speaker is unspecified
        non_admin_users = [(sid, info) for sid, info in self.users.items() if not info.get("is_admin") and sid != "default"]
        if len(non_admin_users) == 1:
            sid, info = non_admin_users[0]
            self._ensure_user_daily_stories(info)
            return sid, info

        # Otherwise map to admin or first registered user
        if self.admin_speaker_id in self.users:
            info = self.users[self.admin_speaker_id]
            self._ensure_user_daily_stories(info)
            return self.admin_speaker_id, info
        elif self.users:
            sid = next(iter(self.users.keys()))
            info = self.users[sid]
            self._ensure_user_daily_stories(info)
            return sid, info

        return None, None

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.users = data.get("users", {}) or {}
                # Remove legacy "default" key if present
                self.users.pop("default", None)
                self.admin_speaker_id = (
                    data.get("admin_speaker_id") or self.admin_speaker_id
                )
                self.admin_name = data.get("admin_name") or self.admin_name
                self.admin_password = (
                    data.get("admin_password") or self.admin_password
                )
                for name in data.get("reserved_names") or []:
                    if str(name).strip():
                        self.reserved_names.add(str(name).strip().lower())
                for info in self.users.values():
                    if isinstance(info, dict):
                        self._ensure_user_daily_stories(info)
        except Exception:
            self.users = {}

    def _save(self) -> None:
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            self.users.pop("default", None)
            for info in self.users.values():
                if isinstance(info, dict):
                    self._ensure_user_daily_stories(info)
            payload = {
                "admin_name": self.admin_name,
                "admin_speaker_id": self.admin_speaker_id,
                "admin_password": self.admin_password,
                "reserved_names": sorted(self.reserved_names),
                "users": self.users,
            }
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _ensure_admin(self) -> None:
        today = date.today().isoformat()
        if self.admin_speaker_id not in self.users:
            self.users[self.admin_speaker_id] = {
                "name": self.admin_name,
                "is_admin": True,
                "created": int(time.time()),
                "description": "Admin (Mr Blue)",
                "daily_stories": {"date": today, "vi_count": 0, "en_count": 0},
            }
            self._save()
        else:
            self._ensure_user_daily_stories(self.users[self.admin_speaker_id])

    # ----------------------------------------------------- Story Tracking
    def get_story_counts(self, speaker: str | None = None) -> tuple[int, int]:
        with self._lock:
            _, info = self._resolve_user_entry(speaker)
            if info is None:
                return 0, 0
            ds = self._ensure_user_daily_stories(info)
            return int(ds.get("vi_count", 0)), int(ds.get("en_count", 0))

    def record_story(self, is_english: bool = False, speaker: str | None = None) -> None:
        with self._lock:
            _, info = self._resolve_user_entry(speaker)
            if info is None:
                return
            ds = self._ensure_user_daily_stories(info)
            if is_english:
                ds["en_count"] = int(ds.get("en_count", 0)) + 1
            else:
                ds["vi_count"] = int(ds.get("vi_count", 0)) + 1
            self._save()

    def render_story_status(self, speaker: str | None = None) -> str:
        """Per-user Vietnamese storytelling status for the LLM.

        Deliberately does NOT expose counts, any limit, or config. The model must
        decline naturally (as if taking a short rest) without ever revealing that
        a limit exists or comparing languages.
        """
        with self._lock:
            if speaker and str(speaker).strip() and str(speaker).strip() != "未知说话人":
                _, info = self._resolve_user_entry(speaker)
                if info:
                    ds = self._ensure_user_daily_stories(info)
                    user_display = info.get("name") or speaker
                    vi_stories = int(ds.get("vi_count", 0))

                    lines = [f"## Storytelling Status (Speaker: {user_display})"]
                    if vi_stories >= 5:
                        lines.append(
                            f"- Vietnamese storytelling for {user_display}: NOT available right now. "
                            f"If {user_display} asks for a Vietnamese story, decline naturally as if "
                            "you just want a short rest (e.g. your voice is a bit tired), then gently "
                            "offer an English story instead or keep chatting — like a friendly choice, "
                            "never a rule. NEVER mention a number, limit, 'unlimited', count, rule, or "
                            "any Vietnamese/English comparison. Append the hidden marker `story:no` "
                            "at the end of the refusal."
                        )
                    else:
                        lines.append(
                            f"- Vietnamese storytelling for {user_display}: available right now — "
                            "tell stories happily when asked."
                        )
                    return "\n".join(lines)

            # General overview for all enrolled users (no counts / no limit exposed).
            today_str = date.today().isoformat()
            lines = ["## Storytelling Status (Per User)"]
            lines.append(f"- Date: {today_str}")
            for sid, info in self.users.items():
                if sid == "default":
                    continue
                ds = self._ensure_user_daily_stories(info)
                name = info.get("name") or sid
                vi_cnt = int(ds.get("vi_count", 0))
                if vi_cnt >= 5:
                    lines.append(
                        f"- {name}: Vietnamese storytelling not available right now — "
                        "decline naturally, never reveal a reason."
                    )
                else:
                    lines.append(
                        f"- {name}: Vietnamese storytelling available right now."
                    )
            lines.append(
                "Decline Vietnamese storytelling only per the status above, and always do it "
                "naturally — never mention any limit, number, 'unlimited', or vi/en comparison."
            )
            return "\n".join(lines)

    # -------------------------------------------------------------- queries
    def is_reserved_name(self, name: str) -> bool:
        return bool(name and str(name).strip().lower() in self.reserved_names)

    def get_admin(self) -> dict | None:
        with self._lock:
            return dict(self.users.get(self.admin_speaker_id, {}))

    def is_admin_name(self, name: str) -> bool:
        return bool(
            name and str(name).strip().lower() == self.admin_name.strip().lower()
        )

    def is_admin_speaker(self, speaker_name: str | None) -> bool:
        """True when a recognized speaker name matches the admin identity."""
        return self.is_admin_name(speaker_name or "")

    def get_user_by_name(self, name: str) -> dict | None:
        if not name:
            return None
        target = str(name).strip().lower()
        with self._lock:
            for info in self.users.values():
                if str(info.get("name", "")).strip().lower() == target:
                    return dict(info)
        return None

    def get_user_by_id(self, speaker_id: str) -> dict | None:
        with self._lock:
            info = self.users.get(speaker_id)
            return dict(info) if info else None

    def get_speaker_id_for_name(self, name: str) -> str | None:
        info = self.get_user_by_name(name)
        if not info:
            return None
        with self._lock:
            for sid, candidate in self.users.items():
                if candidate is info:
                    return sid
        return None

    def all_users(self) -> list[dict]:
        with self._lock:
            return [dict(info) for info in self.users.values()]

    def add_user(
        self,
        name: str,
        speaker_id: str,
        *,
        is_admin: bool = False,
        description: str = "",
    ) -> bool:
        """Register a user. Returns False if the name is reserved/blocked."""
        clean_name = (name or "").strip()
        if not clean_name or not speaker_id:
            return False
        if not is_admin and self.is_reserved_name(clean_name):
            return False
        with self._lock:
            self.users[speaker_id] = {
                "name": clean_name,
                "is_admin": is_admin or self.is_admin_name(clean_name),
                "created": int(time.time()),
                "description": description or "",
            }
            self._save()
        return True

    def set_admin_speaker_id(self, speaker_id: str) -> None:
        with self._lock:
            self.admin_speaker_id = speaker_id
            if speaker_id not in self.users:
                self.users[speaker_id] = {
                    "name": self.admin_name,
                    "is_admin": True,
                    "created": int(time.time()),
                    "description": "Admin (Mr Blue)",
                }
            else:
                self.users[speaker_id]["is_admin"] = True
            self._save()
