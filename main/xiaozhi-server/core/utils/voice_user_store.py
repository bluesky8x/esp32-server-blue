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

DEFAULT_STORE_PATH = os.path.join("data", "voice_users.json")


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

        # speaker_id -> {"name", "is_admin", "created", "description"}
        self.users: dict[str, dict] = {}
        self._load()
        self._ensure_admin()

    # ------------------------------------------------------------------ IO
    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.users = data.get("users", {}) or {}
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
        except Exception:
            self.users = {}

    def _save(self) -> None:
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
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
        if self.admin_speaker_id not in self.users:
            self.users[self.admin_speaker_id] = {
                "name": self.admin_name,
                "is_admin": True,
                "created": int(time.time()),
                "description": "Admin (Mr Blue)",
            }
            self._save()

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
