import httpx
import asyncio
import json
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

OPEN_METEO_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; xiaozhi-blue/1.0; +https://github.com/xinnan-tech/xiaozhi-esp32-server)"
    )
}

# Common VN aliases — Open-Meteo may resolve "Sài Gòn" to HK without these.
LOCATION_ALIASES: dict[str, str] = {
    "sài gòn": "Ho Chi Minh",
    "saigon": "Ho Chi Minh",
    "sai gon": "Ho Chi Minh",
    "tp hcm": "Ho Chi Minh",
    "tphcm": "Ho Chi Minh",
    "hcm": "Ho Chi Minh",
    "hồ chí minh": "Ho Chi Minh",
    "ho chi minh city": "Ho Chi Minh",
    "thành phố hồ chí minh": "Ho Chi Minh",
    "hà nội": "Hanoi",
    "ha noi": "Hanoi",
    "đà nẵng": "Da Nang",
    "da nang": "Da Nang",
    "huế": "Hue",
    "hue": "Hue",
    "cần thơ": "Can Tho",
    "can tho": "Can Tho",
}

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "获取某个地点的天气，用户应提供一个位置，比如用户说杭州天气，参数为：杭州。"
            "如果用户说的是省份，默认用省会城市。如果用户说的不是省份或城市而是一个地名，默认用该地所在省份的省会城市。"
            "重要：本地未来7天天气已在上下文中提供，用户未指明其他城市时绝对不要调用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名，例如杭州。可选参数，如果不提供则不传",
                },
                "lang": {
                    "type": "string",
                    "description": "返回用户使用的语言code，例如zh_CN/zh_HK/en_US/ja_JP等，默认zh_CN",
                },
            },
            "required": ["lang"],
        },
    },
}

# WMO weather interpretation codes (Open-Meteo)
_WMO_VI: dict[int, str] = {
    0: "trời quang",
    1: "hơi mây",
    2: "có mây",
    3: "nhiều mây",
    45: "sương mù",
    48: "sương mù đóng băng",
    51: "mưa phùn nhẹ",
    53: "mưa phùn",
    55: "mưa phùn dày",
    56: "mưa phùn lạnh nhẹ",
    57: "mưa phùn lạnh",
    61: "mưa nhẹ",
    63: "mưa vừa",
    65: "mưa to",
    66: "mưa lạnh nhẹ",
    67: "mưa lạnh",
    71: "tuyết nhẹ",
    73: "tuyết vừa",
    75: "tuyết dày",
    77: "hạt tuyết",
    80: "mưa rào nhẹ",
    81: "mưa rào",
    82: "mưa rào mạnh",
    85: "mưa tuyết nhẹ",
    86: "mưa tuyết",
    95: "dông",
    96: "dông có mưa đá",
    99: "dông mưa đá mạnh",
}

_WMO_EN: dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "light snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "heavy hail thunderstorm",
}

_WMO_ZH: dict[int, str] = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "小冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "小冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "大阵雨",
    85: "小阵雪",
    86: "阵雪",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "强雷暴伴冰雹",
}


def _weather_config(conn: "ConnectionHandler") -> dict[str, Any]:
    return conn.config.get("plugins", {}).get("get_weather", {})


def _normalize_location_query(location: str) -> str:
    key = location.strip().casefold()
    return LOCATION_ALIASES.get(key, location.strip())


def _speech_lang(*, locale: str = "vi", lang: str | None = None) -> str:
    if locale == "en":
        return "en"
    if locale == "vi":
        return "vi"
    if lang and lang.startswith("en"):
        return "en"
    if lang and lang.startswith("zh"):
        return "zh"
    return "vi"


def _geo_language(speech_lang: str) -> str:
    return {"en": "en", "zh": "zh"}.get(speech_lang, "vi")


def wmo_label(code: int, speech_lang: str) -> str:
    table = {"en": _WMO_EN, "zh": _WMO_ZH}.get(speech_lang, _WMO_VI)
    return table.get(int(code), table.get(95, "unknown"))


def _round_temp(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value)))


def _round_one(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    v = float(value)
    if abs(v) >= 10:
        return int(round(v))
    return round(v, 1)


def _daily_value(daily: dict[str, Any], key: str, idx: int) -> Any:
    values = daily.get(key) or []
    if idx >= len(values):
        return None
    return values[idx]


def _fmt_clock_time(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    # Open-Meteo returns e.g. 2026-08-13T05:42 or full ISO
    if "T" in iso_value:
        return iso_value.split("T", 1)[1][:5]
    return iso_value[:5] if len(iso_value) >= 5 else iso_value


def _wind_compass(degrees: float | int | None) -> str | None:
    if degrees is None:
        return None
    labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((float(degrees) + 22.5) // 45) % 8
    return labels[idx]


def _build_current_payload(current: dict[str, Any]) -> dict[str, Any]:
    code = current.get("weather_code", 0)
    payload: dict[str, Any] = {
        "temperature_c": _round_temp(current.get("temperature_2m")),
        "feels_like_c": _round_temp(current.get("apparent_temperature")),
        "humidity_pct": _round_one(current.get("relative_humidity_2m")),
        "cloud_cover_pct": _round_one(current.get("cloud_cover")),
        "wind_kmh": _round_one(current.get("wind_speed_10m")),
        "wind_direction": _wind_compass(current.get("wind_direction_10m")),
        "rain_mm": _round_one(current.get("rain")),
        "precipitation_mm": _round_one(current.get("precipitation")),
        "weather_code": code,
        "condition": wmo_label(code, "en"),
    }
    return {k: v for k, v in payload.items() if v is not None}


def _build_day_payload(daily: dict[str, Any], idx: int) -> dict[str, Any]:
    code = _daily_value(daily, "weather_code", idx) or 0
    payload: dict[str, Any] = {
        "offset_days": idx,
        "date": _daily_value(daily, "time", idx),
        "condition": wmo_label(code, "en"),
        "weather_code": code,
        "high_c": _round_temp(_daily_value(daily, "temperature_2m_max", idx)),
        "low_c": _round_temp(_daily_value(daily, "temperature_2m_min", idx)),
        "feels_like_high_c": _round_temp(
            _daily_value(daily, "apparent_temperature_max", idx)
        ),
        "feels_like_low_c": _round_temp(
            _daily_value(daily, "apparent_temperature_min", idx)
        ),
        "rain_mm": _round_one(_daily_value(daily, "precipitation_sum", idx)),
        "rain_chance_pct": _round_one(
            _daily_value(daily, "precipitation_probability_max", idx)
        ),
        "wind_max_kmh": _round_one(_daily_value(daily, "wind_speed_10m_max", idx)),
        "uv_index": _round_one(_daily_value(daily, "uv_index_max", idx)),
        "sunrise": _fmt_clock_time(_daily_value(daily, "sunrise", idx)),
        "sunset": _fmt_clock_time(_daily_value(daily, "sunset", idx)),
    }
    return {k: v for k, v in payload.items() if v is not None}


async def geocode_place(
    name: str,
    *,
    language: str = "vi",
    country_code: str | None = None,
    geocoding_url: str = OPEN_METEO_GEO_URL,
) -> dict[str, Any] | None:
    params: dict[str, Any] = {"name": name, "count": 5, "language": language}
    if country_code:
        params["countryCode"] = country_code
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
        response = await client.get(geocoding_url, params=params, headers=HEADERS)
    if response.status_code != 200:
        logger.bind(tag=TAG).error(f"Open-Meteo geocode HTTP {response.status_code}")
        return None
    data = response.json()
    if data.get("error"):
        logger.bind(tag=TAG).error(f"Open-Meteo geocode error: {data.get('reason')}")
        return None
    results = data.get("results") or []
    return results[0] if results else None


async def resolve_geocode(
    location_name: str,
    *,
    speech_lang: str,
    prefer_country: str | None = "VN",
    geocoding_url: str = OPEN_METEO_GEO_URL,
) -> dict[str, Any] | None:
    query = _normalize_location_query(location_name)
    language = _geo_language(speech_lang)

    geo = await geocode_place(
        query, language=language, country_code=prefer_country, geocoding_url=geocoding_url
    )
    if geo:
        return geo

    geo = await geocode_place(query, language=language, geocoding_url=geocoding_url)
    if geo:
        return geo

    if prefer_country and "," not in query:
        return await geocode_place(
            f"{query}, {prefer_country}",
            language=language,
            country_code=prefer_country,
            geocoding_url=geocoding_url,
        )
    return None


async def fetch_open_meteo_forecast(
    lat: float,
    lon: float,
    *,
    timezone: str | None = None,
    forecast_url: str = OPEN_METEO_FORECAST_URL,
) -> dict[str, Any] | None:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,rain,wind_speed_10m,wind_direction_10m,"
            "weather_code,cloud_cover"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "apparent_temperature_max,apparent_temperature_min,"
            "precipitation_sum,precipitation_probability_max,"
            "uv_index_max,wind_speed_10m_max,sunrise,sunset"
        ),
        "timezone": timezone or "auto",
        "forecast_days": 7,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.get(forecast_url, params=params, headers=HEADERS)
    if response.status_code != 200:
        logger.bind(tag=TAG).error(f"Open-Meteo forecast HTTP {response.status_code}")
        return None
    data = response.json()
    if not data.get("current") or not data.get("daily"):
        logger.bind(tag=TAG).error("Open-Meteo forecast missing current/daily fields")
        return None
    return data


def build_weather_raw_payload(
    geo: dict[str, Any],
    forecast: dict[str, Any],
    *,
    time_request: "WeatherLookupRequest | None" = None,
) -> dict[str, Any]:
    """Locale-neutral JSON for LLM — filtered to the requested day range."""
    from core.utils.weather_tag_codec import WeatherLookupRequest, default_weather_request

    req = time_request or default_weather_request()
    current = forecast["current"]
    daily = forecast["daily"]
    day_count = len(daily.get("time") or [])
    all_days = [_build_day_payload(daily, idx) for idx in range(day_count)]

    start = max(0, min(req.start_offset, len(all_days) - 1))
    end = max(start, min(req.end_offset, len(all_days) - 1))
    selected = all_days[start : end + 1]

    period = _period_labels(req, selected)

    payload: dict[str, Any] = {
        "place": geo.get("name") or "",
        "country_code": geo.get("country_code"),
        "timezone": forecast.get("timezone"),
        "requested_period": period,
        "days": selected,
    }
    if req.include_current and req.start_offset == 0:
        payload["current"] = _build_current_payload(current)
    return payload


def _period_labels(
    req: "WeatherLookupRequest", selected: list[dict[str, Any]]
) -> dict[str, Any]:
    if req.start_offset == req.end_offset == 0:
        desc_en, desc_vi = "today", "hôm nay"
    elif req.start_offset == req.end_offset == 1:
        desc_en, desc_vi = "tomorrow", "ngày mai"
    elif req.start_offset == req.end_offset == 2:
        desc_en, desc_vi = "the day after tomorrow", "ngày kia"
    elif req.end_offset - req.start_offset >= 1:
        count = req.end_offset - req.start_offset + 1
        desc_en = f"days {req.start_offset}–{req.end_offset} ({count}-day outlook)"
        if req.start_offset == 0:
            desc_vi = f"{count} ngày tới (từ hôm nay)"
        else:
            desc_vi = f"{count} ngày (từ ngày +{req.start_offset})"
    else:
        desc_en = f"day +{req.start_offset}"
        desc_vi = f"ngày +{req.start_offset}"

    dates = [d.get("date") for d in selected if d.get("date")]
    return {
        "time_key": req.time_key,
        "start_offset_days": req.start_offset,
        "end_offset_days": req.end_offset,
        "description_en": desc_en,
        "description_vi": desc_vi,
        "dates": dates,
    }


def build_weather_snapshot(
    geo: dict[str, Any], forecast: dict[str, Any], *, speech_lang: str
) -> dict[str, Any]:
    current = forecast["current"]
    daily = forecast["daily"]
    temp = _round_temp(current.get("temperature_2m"))
    condition = wmo_label(current.get("weather_code", 0), speech_lang)

    if speech_lang == "en":
        current_abstract = f"{temp}°C, {condition}" if temp is not None else condition
    elif speech_lang == "zh":
        current_abstract = f"{temp}度，{condition}" if temp is not None else condition
    else:
        current_abstract = f"{temp} độ, {condition}" if temp is not None else condition

    temps_list: list[tuple[str, str, int | None, int | None]] = []
    for idx, date in enumerate(daily.get("time") or []):
        code = (daily.get("weather_code") or [0])[idx]
        high = _round_temp((daily.get("temperature_2m_max") or [None])[idx])
        low = _round_temp((daily.get("temperature_2m_min") or [None])[idx])
        temps_list.append((date, wmo_label(code, speech_lang), high, low))

    current_basic: dict[str, str] = {}
    if temp is not None:
        current_basic["temperature"] = f"{temp}°C"

    return {
        "city_name": geo.get("name") or "",
        "current_abstract": current_abstract,
        "current_basic": current_basic,
        "temps_list": temps_list,
    }


async def resolve_weather_location(
    conn: "ConnectionHandler", location: str | None
) -> str:
    from core.utils.cache.manager import cache_manager, CacheType

    weather_config = _weather_config(conn)
    default_location = weather_config.get("default_location", "Ho Chi Minh City")
    client_ip = conn.client_ip

    if location and str(location).strip():
        return str(location).strip()

    if client_ip:
        cached_ip_info = cache_manager.get(CacheType.IP_INFO, client_ip)
        if cached_ip_info:
            city = cached_ip_info.get("city")
            if city:
                return city
        ip_info = get_ip_info(client_ip, logger)
        if ip_info:
            cache_manager.set(CacheType.IP_INFO, client_ip, ip_info)
            city = ip_info.get("city")
            if city:
                return city
    return default_location


def format_weather_speech(
    *,
    city_name: str,
    current_abstract: str,
    temps_list: list,
    locale: str = "vi",
    requested_location: str | None = None,
) -> str:
    display_city = (requested_location or city_name or "").strip() or city_name
    today = temps_list[0] if temps_list else None
    if locale == "en":
        if today:
            _date, weather, high, low = today
            if high is not None and low is not None:
                return (
                    f"{display_city}: now {current_abstract}. "
                    f"Today {weather}, {low} to {high} degrees."
                )
            return f"{display_city}: now {current_abstract}. Today {weather}."
        return f"{display_city}: {current_abstract}."

    if today:
        _date, weather, high, low = today
        if high is not None and low is not None:
            return (
                f"{display_city}: hiện {current_abstract}. "
                f"Hôm nay {weather}, {low} đến {high} độ."
            )
        return f"{display_city}: hiện {current_abstract}. Hôm nay {weather}."
    return f"{display_city}: hiện {current_abstract}."


def format_weather_report(
    *,
    city_name: str,
    current_abstract: str,
    current_basic: dict[str, str],
    temps_list: list,
    speech_lang: str,
) -> str:
    if speech_lang == "en":
        weather_report = f"Location: {city_name}\n\nCurrent: {current_abstract}\n"
        if current_basic:
            weather_report += "Details:\n"
            for key, value in current_basic.items():
                weather_report += f"  · {key}: {value}\n"
        weather_report += "\n7-day forecast:\n"
        for date, weather, high, low in temps_list:
            weather_report += f"{date}: {weather}, {low}~{high}°C\n"
        return weather_report

    if speech_lang == "zh":
        weather_report = f"您查询的位置是：{city_name}\n\n当前天气: {current_abstract}\n"
        if current_basic:
            weather_report += "详细参数：\n"
            for key, value in current_basic.items():
                weather_report += f"  · {key}: {value}\n"
        weather_report += "\n未来7天预报：\n"
        for date, weather, high, low in temps_list:
            weather_report += f"{date}: {weather}，气温 {low}~{high}°C\n"
        weather_report += "\n（如需某一天的具体天气，请告诉我日期）"
        return weather_report

    weather_report = f"Địa điểm: {city_name}\n\nHiện tại: {current_abstract}\n"
    if current_basic:
        weather_report += "Chi tiết:\n"
        for key, value in current_basic.items():
            weather_report += f"  · {key}: {value}\n"
    weather_report += "\nDự báo 7 ngày:\n"
    for date, weather, high, low in temps_list:
        weather_report += f"{date}: {weather}, {low}~{high}°C\n"
    return weather_report


async def fetch_weather_data(
    conn: "ConnectionHandler",
    location: str | None,
    *,
    locale: str = "vi",
    lang: str | None = None,
    time_request: "WeatherLookupRequest | None" = None,
) -> dict[str, Any] | None:
    from core.utils.weather_tag_codec import WeatherLookupRequest, default_weather_request

    req = time_request or default_weather_request((location or "").strip())
    if req.location and not location:
        location = req.location
    weather_config = _weather_config(conn)
    geocoding_url = weather_config.get("geocoding_url", OPEN_METEO_GEO_URL)
    forecast_url = weather_config.get("forecast_url", OPEN_METEO_FORECAST_URL)
    prefer_country = weather_config.get("prefer_country", "VN") or None

    requested = (location or "").strip() or None
    resolved_name = await resolve_weather_location(conn, requested)
    speech_lang = _speech_lang(locale=locale, lang=lang)

    geo = await resolve_geocode(
        resolved_name,
        speech_lang=speech_lang,
        prefer_country=prefer_country,
        geocoding_url=geocoding_url,
    )
    if not geo:
        logger.bind(tag=TAG).error(f"Open-Meteo geocode miss: {resolved_name!r}")
        return None

    forecast = await fetch_open_meteo_forecast(
        geo["latitude"],
        geo["longitude"],
        timezone=geo.get("timezone"),
        forecast_url=forecast_url,
    )
    if not forecast:
        return None

    snapshot = build_weather_snapshot(geo, forecast, speech_lang=speech_lang)
    snapshot["raw"] = build_weather_raw_payload(geo, forecast, time_request=req)
    snapshot["time_request"] = req
    snapshot["requested_location"] = requested
    snapshot["resolved_name"] = resolved_name
    return snapshot


def _clean_llm_weather_reply(text: str) -> str:
    from core.utils.robot_move_codec import split_robot_move_tags
    from core.utils.weather_tag_codec import strip_wx_tags

    cleaned, _ = split_robot_move_tags(text or "", trim_edges=True)
    return strip_wx_tags(cleaned, trim_edges=True).strip()


def _weather_naturalize_system_prompt(*, locale: str, character_name: str) -> str:
    language = {"en": "English", "vi": "Vietnamese"}.get(locale, "Vietnamese")
    return (
        f"You are {character_name}, a warm, lively voice assistant. "
        f"Turn Open-Meteo weather JSON into a natural {language} spoken forecast for text-to-speech.\n"
        "Rules:\n"
        "- Read requested_period — answer ONLY for that period (today / tomorrow / multi-day).\n"
        "- If tomorrow or a future day, do NOT use current unless JSON includes \"current\".\n"
        "- Single day: 3–5 short sentences. Multi-day: 1–2 sentences per day (max ~6 sentences).\n"
        "- Pick the most useful extra details when present: feels_like, humidity, rain_chance_pct, "
        "rain_mm, wind, uv_index, sunrise/sunset.\n"
        "- Mention rain probability if rain_chance_pct >= 40. Mention feels_like if it differs a lot from temp.\n"
        "- UV: warn gently if uv_index >= 8 (very high). Keep tone friendly, not alarmist.\n"
        "- Use ONLY facts from JSON. Do not invent numbers.\n"
        "- No markdown, bullet lists, emojis, or control tags (wx:, mv:, vol:, mem:).\n"
        "Reply with the spoken text only."
    )


async def naturalize_weather_speech(
    conn: "ConnectionHandler",
    location: str | None = None,
    *,
    locale: str = "vi",
    time_request: "WeatherLookupRequest | None" = None,
) -> str | None:
    """Fetch Open-Meteo data and ask the LLM for a natural spoken weather reply."""
    from core.characters.character_registry import get_active_character, get_display_name
    from core.utils.weather_tag_codec import WeatherLookupRequest, default_weather_request

    req = time_request or default_weather_request((location or "").strip())
    requested = (location or req.location or "").strip() or None
    snapshot = await fetch_weather_data(
        conn, requested, locale=locale, time_request=req
    )
    if not snapshot:
        return None

    raw = snapshot.get("raw")
    llm = getattr(conn, "llm", None)
    if llm and raw:
        character_id = get_active_character(conn)
        character_name = get_display_name(character_id) if character_id else "Kira"
        system_prompt = _weather_naturalize_system_prompt(
            locale=locale, character_name=character_name
        )
        user_prompt = json.dumps(raw, ensure_ascii=False, indent=2)
        try:
            loop = asyncio.get_running_loop()
            reply = await loop.run_in_executor(
                None,
                lambda: llm.response_no_stream(
                    system_prompt,
                    user_prompt,
                    max_tokens=300,
                    temperature=0.35,
                ),
            )
            cleaned = _clean_llm_weather_reply(reply)
            if cleaned:
                return cleaned
            logger.bind(tag=TAG).warning(
                f"LLM weather reply empty after clean; fallback. raw={reply!r}"
            )
        except Exception as exc:
            logger.bind(tag=TAG).error(f"LLM weather naturalize failed: {exc}")

    return format_weather_speech(
        city_name=snapshot["city_name"],
        current_abstract=snapshot["current_abstract"],
        temps_list=snapshot["temps_list"],
        locale=locale,
        requested_location=snapshot.get("requested_location"),
    )


async def fetch_weather_speech(
    conn: "ConnectionHandler",
    location: str | None = None,
    *,
    locale: str = "vi",
    time_request: "WeatherLookupRequest | None" = None,
) -> str | None:
    """Fetch weather for wx: tag dispatch. Returns TTS-ready natural language text."""
    from core.utils.cache.manager import cache_manager, CacheType
    from core.utils.weather_tag_codec import WeatherLookupRequest, default_weather_request

    req = time_request or default_weather_request((location or "").strip())
    resolved = await resolve_weather_location(conn, (location or req.location or None))
    weather_cache_key = f"openmeteo_natural_{resolved}_{req.time_key}_{locale}"
    cached = cache_manager.get(CacheType.WEATHER, weather_cache_key)
    if cached:
        return cached

    speech = await naturalize_weather_speech(
        conn, location or req.location or None, locale=locale, time_request=req
    )
    if speech:
        cache_manager.set(CacheType.WEATHER, weather_cache_key, speech)
    return speech


def format_weather_report_for_llm(raw: dict[str, Any]) -> str:
    return (
        "Open-Meteo weather data (answer the user naturally using these facts only):\n"
        + json.dumps(raw, ensure_ascii=False, indent=2)
    )


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def get_weather(conn: "ConnectionHandler", location: str = None, lang: str = "zh_CN"):
    from core.utils.cache.manager import cache_manager, CacheType

    resolved = await resolve_weather_location(conn, location)
    speech_lang = _speech_lang(lang=lang)
    weather_cache_key = f"openmeteo_full_{resolved}_{lang}"
    cached_weather_report = cache_manager.get(CacheType.WEATHER, weather_cache_key)
    if cached_weather_report:
        return ActionResponse(Action.REQLLM, cached_weather_report, None)

    locale = "en" if speech_lang == "en" else "vi"
    snapshot = await fetch_weather_data(conn, location, locale=locale, lang=lang)
    if not snapshot:
        if speech_lang == "zh":
            msg = f"未找到相关的城市: {resolved}，请确认地点是否正确"
        elif speech_lang == "en":
            msg = f"Could not find weather for: {resolved}"
        else:
            msg = f"Không tìm thấy thời tiết cho: {resolved}"
        return ActionResponse(Action.REQLLM, msg, None)

    weather_report = format_weather_report_for_llm(snapshot["raw"])
    cache_manager.set(CacheType.WEATHER, weather_cache_key, weather_report)
    return ActionResponse(Action.REQLLM, weather_report, None)
