"""Weather lookup tags (Intent nointent — same pattern as vol: / mv:)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# wx:Ho Chi Minh@tomorrow  wx:local@d1  wx:Hà Nội@d0-2  wx:local  wx:
WX_TAG_RE = re.compile(
    r"\bwx\s*:\s*"
    r"(?:"
    r"(local|[A-Za-zÀ-ỹ0-9][A-Za-zÀ-ỹ0-9\s,.-]{0,48})"
    r"(?:@([a-z0-9+-]+))?"
    r"|@([a-z0-9+-]+)"
    r")?\s*$",
    re.IGNORECASE,
)
WX_TAG_STRIP_RE = re.compile(
    r"\bwx\s*:\s*"
    r"(?:"
    r"(?:local|[A-Za-zÀ-ỹ0-9][A-Za-zÀ-ỹ0-9\s,.-]{0,48})"
    r"@[a-z0-9+-]+"
    r"|@[a-z0-9+-]+"
    r")\b",
    re.IGNORECASE,
)
# Suffix leaked when streaming stripped "wx:Ho Chi" but left "Minh@3d" (word + @time).
WX_LEAKED_TIME_SUFFIX_RE = re.compile(
    r"(?:\s+[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9]{0,24})?@[a-z0-9+-]+\s*$",
    re.IGNORECASE,
)

_BAD_WX_LOCATION_RE = re.compile(
    r"\b(is|are|was|wrong|bad|not|the|and|or|nha|nhé|nhe)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WeatherLookupRequest:
    """Parsed wx: tag — location + day offset range (0=today in place timezone)."""

    location: str  # "" = local / default
    time_key: str  # normalized: d0, d1, d0-2, d1-3, ...
    start_offset: int
    end_offset: int
    include_current: bool

    @property
    def cache_key(self) -> str:
        loc = self.location or "local"
        return f"{loc}@{self.time_key}"


def _normalize_wx_location(raw: str | None) -> str | None:
    if raw is None:
        return ""
    loc = raw.strip()
    if not loc or loc.lower() == "local":
        return ""
    if len(loc) > 40 or _BAD_WX_LOCATION_RE.search(loc):
        return None
    return loc


def normalize_time_spec(spec: str | None) -> WeatherLookupRequest | None:
    """Parse @time suffix into day offsets (inclusive). Returns None if invalid."""
    raw = (spec or "").strip().lower()
    if not raw or raw in {"today", "d0", "hom-nay", "homnay", "hôm-nay", "hôm nay"}:
        return None  # caller applies default d0

    aliases_d1 = {"tomorrow", "d1", "mai", "ngay-mai", "ngaymai", "ngày-mai", "ngày mai"}
    aliases_d2 = {"d2", "day2", "ngay-kia", "ngaykia", "ngày-kia", "ngày kia", "ngay-mot", "ngày mốt"}

    if raw in aliases_d1:
        return _range_request("", "d1", 1, 1, False)
    if raw in aliases_d2:
        return _range_request("", "d2", 2, 2, False)

    m = re.fullmatch(r"(?:d)?(\d+)-(\d+)", raw)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if end < start or end > 6:
            return None
        key = f"d{start}-{end}"
        return _range_request("", key, start, end, start == 0)

    m = re.fullmatch(r"(?:next)?(\d+)d", raw)
    if m:
        count = int(m.group(1))
        if count < 1 or count > 7:
            return None
        end = count - 1
        key = f"d0-{end}" if end else "d0"
        return _range_request("", key, 0, end, True)

    m = re.fullmatch(r"d(\d+)", raw)
    if m:
        day = int(m.group(1))
        if day > 6:
            return None
        key = f"d{day}"
        return _range_request("", key, day, day, day == 0)

    return None


def _range_request(
    location: str,
    time_key: str,
    start: int,
    end: int,
    include_current: bool,
) -> WeatherLookupRequest:
    return WeatherLookupRequest(
        location=location,
        time_key=time_key,
        start_offset=start,
        end_offset=end,
        include_current=include_current,
    )


def default_weather_request(location: str = "") -> WeatherLookupRequest:
    return WeatherLookupRequest(
        location=location,
        time_key="d0",
        start_offset=0,
        end_offset=0,
        include_current=True,
    )


def extract_weather_request_from_assistant_text(text: str) -> WeatherLookupRequest | None:
    """Return location + time range when assistant appended wx: tag at end."""
    if not text:
        return None
    match = WX_TAG_RE.search(text.rstrip())
    if not match:
        return None

    loc_raw = match.group(1)
    time_raw = match.group(2) or match.group(3)

    if loc_raw is None and time_raw is None:
        location = ""
    elif loc_raw is None and time_raw:
        location = ""
    else:
        location = _normalize_wx_location(loc_raw)
        if location is None:
            return None

    if not time_raw:
        return default_weather_request(location)

    parsed = normalize_time_spec(time_raw)
    if parsed is None:
        return None
    return WeatherLookupRequest(
        location=location,
        time_key=parsed.time_key,
        start_offset=parsed.start_offset,
        end_offset=parsed.end_offset,
        include_current=parsed.include_current,
    )


def extract_weather_location_from_assistant_text(text: str) -> str | None:
    req = extract_weather_request_from_assistant_text(text)
    if req is None:
        return None
    return req.location


def hold_incomplete_wx_suffix(
    text: str, *, allow_complete: bool = False
) -> tuple[str, str]:
    """Hold trailing wx:… during streaming so partial tags are not spoken or dispatched."""
    if not text:
        return "", ""
    if allow_complete and extract_weather_request_from_assistant_text(text.rstrip()) is not None:
        return text, ""
    match = re.search(r"\bwx\s*:.*$", text, re.IGNORECASE)
    if match:
        return text[: match.start()], text[match.start() :]
    return text, ""


def strip_wx_tags(text: str, *, trim_edges: bool = False) -> str:
    if not text:
        return ""
    cleaned = text
    # End-anchored complete wx: tags (same rule as dispatch — avoids eating "wx:Ho Chi"
    # from "wx:Ho Chi Minh@3d" due to an early \\b after "Chi").
    while True:
        stripped = cleaned.rstrip()
        match = WX_TAG_RE.search(stripped)
        if not match or match.end() != len(stripped):
            break
        cleaned = stripped[: match.start()]
    cleaned = WX_TAG_STRIP_RE.sub("", cleaned)
    cleaned = WX_LEAKED_TIME_SUFFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if trim_edges:
        return cleaned.strip()
    return cleaned
