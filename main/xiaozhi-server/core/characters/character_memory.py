"""Per-character user/relationship memory store (Kira, Lili, ...)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.characters.character_render import render_static_memory

_EPHEMERAL_RE = re.compile(
    r"\b(thời tiết|weather|hôm nay mấy độ|forecast|"
    r"hỏi python|how to|làm sao để|what is|tell me about)\b",
    re.I,
)

_MAX_LIKES = 12
_MAX_STABLE_PREFS = 10
_MAX_INSIDE_JOKES = 5
_MAX_SHARED_TOPICS = 10

_STORES: dict[str, "CharacterMemoryStore"] = {}


@dataclass
class UserMemory:
    name: str = ""
    preferred_name: str = ""
    favorite_language: str = ""
    likes: list[str] = field(default_factory=list)
    birthday: str = ""
    timezone: str = ""
    stable_preferences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "preferred_name": self.preferred_name,
            "favorite_language": self.favorite_language,
            "likes": self.likes,
            "birthday": self.birthday,
            "timezone": self.timezone,
            "stable_preferences": self.stable_preferences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UserMemory:
        data = data or {}
        return cls(
            name=str(data.get("name") or ""),
            preferred_name=str(data.get("preferred_name") or ""),
            favorite_language=str(data.get("favorite_language") or ""),
            likes=list(data.get("likes") or [])[:_MAX_LIKES],
            birthday=str(data.get("birthday") or ""),
            timezone=str(data.get("timezone") or ""),
            stable_preferences=list(data.get("stable_preferences") or [])[:_MAX_STABLE_PREFS],
        )


@dataclass
class RelationshipMemory:
    first_met: str = ""
    friendliness: int = 0
    inside_jokes: list[str] = field(default_factory=list)
    shared_topics: list[str] = field(default_factory=list)
    conversation_style: str = "casual"

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_met": self.first_met,
            "friendliness": self.friendliness,
            "inside_jokes": self.inside_jokes,
            "shared_topics": self.shared_topics,
            "conversation_style": self.conversation_style,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RelationshipMemory:
        data = data or {}
        return cls(
            first_met=str(data.get("first_met") or ""),
            friendliness=int(data.get("friendliness") or 0),
            inside_jokes=list(data.get("inside_jokes") or [])[:_MAX_INSIDE_JOKES],
            shared_topics=list(data.get("shared_topics") or [])[:_MAX_SHARED_TOPICS],
            conversation_style=str(data.get("conversation_style") or "casual"),
        )


@dataclass
class CharacterMemoryState:
    user: UserMemory = field(default_factory=UserMemory)
    relationship: RelationshipMemory = field(default_factory=RelationshipMemory)
    turn_count: int = 0


def render_dynamic_memory(state: CharacterMemoryState) -> str:
    u = state.user
    r = state.relationship
    lines = ["## User Memory"]
    display = u.preferred_name or u.name
    if display:
        lines.append(f"Name / preferred: {display}")
    if u.favorite_language:
        lines.append(f"Favorite language: {u.favorite_language}")
    if u.likes:
        lines.append(f"Likes: {', '.join(u.likes)}")
    if u.birthday:
        lines.append(f"Birthday: {u.birthday}")
    if u.timezone:
        lines.append(f"Timezone: {u.timezone}")
    if u.stable_preferences:
        lines.append("Stable preferences:")
        for p in u.stable_preferences:
            lines.append(f"- {p}")
    if len(lines) == 1:
        lines.append("(Not much known yet — learn naturally, do not ask everything at once.)")

    lines.extend(["", "## Relationship Memory"])
    if r.first_met:
        lines.append(f"First met: {r.first_met}")
    lines.append(f"Friendliness: {r.friendliness}/100")
    lines.append(f"Conversation style: {r.conversation_style}")
    if r.shared_topics:
        lines.append(f"Shared topics: {', '.join(r.shared_topics)}")
    if r.inside_jokes:
        lines.append(f"Inside jokes / nicknames: {', '.join(r.inside_jokes)}")
    lines.append(f"Tone hint: {_friendliness_tone(r.friendliness)}")
    lines.append("")
    lines.append(
        "Use this memory naturally. Greet by name when known. "
        "Do NOT mention 'memory' or 'database'. Do NOT store ephemeral questions."
    )
    return "\n".join(lines)


def render_full_memory(character_id: str, device_id: str) -> str:
    state = CharacterMemoryStore(character_id).load(device_id)
    return f"{render_static_memory(character_id)}\n\n{render_dynamic_memory(state)}"


def _friendliness_tone(level: int) -> str:
    if level < 20:
        return "warm but slightly reserved — still getting to know each other"
    if level < 60:
        return "friendly and open — comfortable casual chat"
    return "close friend — relaxed, caring, can reference shared history"


class CharacterMemoryStore:
    def __init__(self, character_id: str):
        self.character_id = character_id.lower()
        self.data_dir = os.path.join("data", self.character_id, "users")
        os.makedirs(self.data_dir, exist_ok=True)

    def _path(self, device_id: str) -> str:
        safe = re.sub(r"[^\w\-]", "_", device_id or "default")
        return os.path.join(self.data_dir, f"{safe}.json")

    def load(self, device_id: str) -> CharacterMemoryState:
        path = self._path(device_id)
        if not os.path.exists(path):
            return self._migrate_legacy(device_id)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return CharacterMemoryState(
                user=UserMemory.from_dict(data.get("user")),
                relationship=RelationshipMemory.from_dict(data.get("relationship")),
                turn_count=int(data.get("turn_count") or 0),
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return CharacterMemoryState()

    def _migrate_legacy(self, device_id: str) -> CharacterMemoryState:
        if self.character_id != "kira":
            return CharacterMemoryState()
        legacy_name = re.sub(r"[^\w\-]", "_", device_id or "default")
        legacy = os.path.join("data", "kira", f"{legacy_name}.json")
        if not os.path.exists(legacy):
            return CharacterMemoryState()
        try:
            with open(legacy, encoding="utf-8") as f:
                old = json.load(f)
            state = CharacterMemoryState(
                relationship=RelationshipMemory(friendliness=int(old.get("friendship") or 0)),
                turn_count=int(old.get("turn_count") or 0),
            )
            for fact in old.get("facts") or []:
                _apply_legacy_fact(state, str(fact))
            self.save(device_id, state)
            return state
        except (json.JSONDecodeError, OSError):
            return CharacterMemoryState()

    def save(self, device_id: str, state: CharacterMemoryState) -> None:
        path = self._path(device_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "user": state.user.to_dict(),
                    "relationship": state.relationship.to_dict(),
                    "turn_count": state.turn_count,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def prepare_turn(self, device_id: str, user_text: str) -> CharacterMemoryState:
        state = self.load(device_id)
        if user_text and not _EPHEMERAL_RE.search(user_text):
            extract_stable_facts(state, user_text, self.character_id)
            self.save(device_id, state)
        return state

    def after_turn(
        self,
        device_id: str,
        user_text: str,
        assistant_text: str,
        *,
        rude: bool = False,
    ) -> CharacterMemoryState:
        state = self.load(device_id)
        state.turn_count += 1
        if not state.relationship.first_met:
            state.relationship.first_met = date.today().isoformat()
        if rude:
            state.relationship.friendliness = max(0, state.relationship.friendliness - 5)
        else:
            state.relationship.friendliness = min(100, state.relationship.friendliness + 2)
        if user_text and not _EPHEMERAL_RE.search(user_text):
            extract_stable_facts(state, user_text, self.character_id)
        self.save(device_id, state)
        return state


_LIKE_TAIL_RE = re.compile(
    r"\s+(?:nhé|nha|đi|đấy|đó|nữa|ạ|a|ha|hả|hén|hen|luôn|vậy|thế)\s*$",
    re.I,
)
_LIKE_SKIP_RE = re.compile(
    r"^(?:hỏi|biết|tìm hiểu|học cách|làm sao|how to|what is|nói tiếng)\b",
    re.I,
)
_GENERIC_LIKE_PATTERNS = (
    r"sở thích(?:\s+của\s+(?:mình|tôi|em|anh))?\s*(?:là|:)\s*(.+)",
    r"(?:lưu|nhớ|ghi nhớ)(?:\s+lại)?\s+sở thích(?:\s+của\s+(?:mình|tôi|em|anh))?\s*(?:là|:)\s*(.+)",
    r"(?:lưu|nhớ|ghi nhớ)(?:\s+lại)?(?:\s+(?:giúp|cho)\s+(?:mình|tôi|em))?\s+(?:là|rằng)\s+(?:mình|tôi|em|i)\s+thích\s+(.+)",
    r"(?:mình|tôi|em|anh|chị|bạn|i)\s+thích\s+(.+)",
    r"(?<!\w)thích\s+(.+)",
    r"(?:remember|save)\s+(?:that\s+)?(?:i\s+)?(?:like|love)\s+(.+)",
    r"i\s+(?:really\s+)?(?:like|love)\s+(.+)",
)
_LIKE_ALIASES = {
    "cà phê": "coffee",
    "ca phe": "coffee",
    "lập trình": "programming",
    "code": "programming",
    "mèo": "cats",
    "meo": "cats",
    "nhạc": "music",
    "openai": "AI",
    "chatgpt": "AI",
    "khủng long": "dinosaurs",
    "bóng đá": "football",
}
_INVALID_NAME_RE = re.compile(
    r"\b(gì|không|sao|tại sao|phải không|hay không|đúng không|"
    r"what|why|how|who|where|when)\b|[?？]",
    re.I,
)
_QUESTION_LIKE_RE = re.compile(
    r"[?？]|"
    r"\b(gì|ai|sao|tại sao|phải không|hay không|đúng không|"
    r"bao nhiêu|ở đâu|khi nào|thế nào|what|who|why|how)\b",
    re.I,
)


def _is_valid_name(name: str) -> bool:
    n = name.strip()
    if len(n) < 2 or len(n) > 30:
        return False
    if _INVALID_NAME_RE.search(n):
        return False
    words = n.lower().split()
    if all(w in {"gì", "không", "sao", "à", "a", "hả", "ha", "nhỉ", "hen"} for w in words):
        return False
    return True


def _clean_like_phrase(raw: str) -> str:
    s = raw.strip().strip("\"'")
    s = re.sub(r"^(?:của mình| của tôi| của em)\s+(?:là|:)\s*", "", s, flags=re.I)
    s = re.sub(r"^(?:mình|tôi|em|i)\s+thích\s+", "", s, flags=re.I)
    s = re.split(r"\s+và\s+|\s+and\s+|\.|!", s, maxsplit=1)[0].strip()
    return _LIKE_TAIL_RE.sub("", s).strip()[:50]


def _is_valid_like(s: str) -> bool:
    if len(s) < 2 or len(s) > 50:
        return False
    if _QUESTION_LIKE_RE.search(s):
        return False
    if _LIKE_SKIP_RE.search(s):
        return False
    if _EPHEMERAL_RE.search(s):
        return False
    if re.search(r"^nói tiếng (anh|việt)$|^(english|vietnamese)$", s, re.I):
        return False
    return True


def _normalize_like(label: str) -> str:
    return _LIKE_ALIASES.get(label.strip().lower(), label.strip())


def _extract_generic_likes(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pat in _GENERIC_LIKE_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            for part in re.split(r"\s+và\s+|\s+and\s+|,", m.group(1).strip()):
                cleaned = _clean_like_phrase(part)
                if not _is_valid_like(cleaned):
                    continue
                normalized = _normalize_like(cleaned)
                key = normalized.lower()
                if key not in seen:
                    seen.add(key)
                    found.append(normalized)
    return found


def _apply_legacy_fact(state: CharacterMemoryState, fact: str) -> None:
    low = fact.lower()
    if "coffee" in low or "cà phê" in low:
        _add_like(state, "coffee")
    elif "cat" in low or "mèo" in low:
        _add_like(state, "cats")
    elif "music" in low or "nhạc" in low:
        _add_like(state, "music")
    else:
        _add_preference(state, fact)


def _add_like(state: CharacterMemoryState, item: str) -> None:
    key = item.strip().lower()
    if key and key not in [x.lower() for x in state.user.likes]:
        state.user.likes.append(item)
        state.user.likes = state.user.likes[:_MAX_LIKES]


def _add_topic(state: CharacterMemoryState, topic: str) -> None:
    t = topic.strip()
    if t and t not in state.relationship.shared_topics:
        state.relationship.shared_topics.append(t)
        state.relationship.shared_topics = state.relationship.shared_topics[:_MAX_SHARED_TOPICS]


def _add_preference(state: CharacterMemoryState, pref: str) -> None:
    p = pref.strip()
    if p and p not in state.user.stable_preferences:
        state.user.stable_preferences.append(p)
        state.user.stable_preferences = state.user.stable_preferences[:_MAX_STABLE_PREFS]


def _add_inside_joke(state: CharacterMemoryState, joke: str) -> None:
    j = joke.strip()
    if j and j not in state.relationship.inside_jokes:
        state.relationship.inside_jokes.append(j)
        state.relationship.inside_jokes = state.relationship.inside_jokes[:_MAX_INSIDE_JOKES]


def extract_stable_facts(
    state: CharacterMemoryState, text: str, character_id: str = "kira"
) -> None:
    t = text.strip()
    if not t:
        return

    char_name = character_id.title()

    for pat in (
        r"(?:tên (?:mình|tôi|em|anh) là|mình tên|tôi tên|my name is|i am|i'm)\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9\s]{0,30})",
        r"(?:call me|gọi (?:mình|tôi|em) là)\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9\s]{0,20})",
    ):
        m = re.search(pat, t, re.I)
        if m:
            name = m.group(1).strip().title()
            if _is_valid_name(name):
                state.user.name = name
                if not state.user.preferred_name:
                    state.user.preferred_name = name

    m = re.search(r"(?:gọi em|call (?:me|her|him))\s+([A-Za-zÀ-ỹ]{2,20})", t, re.I)
    if m:
        nick = m.group(1).strip()
        if _is_valid_name(nick):
            state.user.preferred_name = nick
            _add_inside_joke(state, f"User calls {char_name} '{nick}'")

    m = re.search(
        r"(?:sinh nhật|birthday)(?:\s+(?:là|is))?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        t,
        re.I,
    )
    if m:
        state.user.birthday = m.group(1)

    m = re.search(r"(?:timezone|múi giờ)\s*(?:là|:)?\s*(Asia/[A-Za-z_]+|UTC[+-]?\d*)", t, re.I)
    if m:
        state.user.timezone = m.group(1)

    if re.search(r"thích (?:nói )?tiếng anh|prefer english|speak english", t, re.I):
        state.user.favorite_language = "English"
        _add_preference(state, "Prefers English")
    elif re.search(r"thích (?:nói )?tiếng việt|prefer vietnamese|speak vietnamese", t, re.I):
        state.user.favorite_language = "Vietnamese"
        _add_preference(state, "Prefers Vietnamese")

    if re.search(r"trả lời ngắn|short answers?|keep it brief", t, re.I):
        _add_preference(state, "Prefers short answers")
    if re.search(r"ghét emoji|no emoji|don't use emoji", t, re.I):
        _add_preference(state, "Dislikes emoji")
    if re.search(r"thích ví dụ|real examples?|practical examples?", t, re.I):
        _add_preference(state, "Likes practical examples")
    if re.search(r"đang học tiếng anh|learning english", t, re.I):
        _add_preference(state, "Learning English")

    like_map = [
        (r"\b(cà phê|coffee)\b", "coffee"),
        (r"\b(ai|openai|chatgpt)\b", "AI"),
        (r"\b(lập trình|programming|code)\b", "programming"),
        (r"\b(flutter)\b", "Flutter"),
        (r"\b(startup)\b", "startup"),
        (r"\b(mèo|cats?)\b", "cats"),
        (r"\b(chó|dogs?)\b", "dogs"),
        (r"\b(nhạc|music|piano)\b", "music"),
        (r"\b(khủng long|dinosaur)\b", "dinosaurs"),
        (r"\b(lego)\b", "LEGO"),
        (r"\b(bóng đá|football)\b", "football"),
        (r"\b(trà sữa|bubble tea)\b", "bubble tea"),
    ]
    for pat, label in like_map:
        if re.search(pat, t, re.I):
            _add_like(state, label)
            _add_topic(state, label)

    for label in _extract_generic_likes(t):
        _add_like(state, label)
        _add_topic(state, label)
