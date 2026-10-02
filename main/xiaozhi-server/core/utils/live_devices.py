"""Sổ đăng ký các thiết bị ĐANG kết nối — dùng cho bảng điều khiển web (HTTP API).

Vì sao cần: WebSocket server tạo một `ConnectionHandler` cho mỗi máy rồi chỉ `await` nó, không
giữ danh sách nào. Bảng điều khiển (`GET /robot/`) cần biết "đang có máy nào" và gửi lệnh tới
đúng máy ⇒ mỗi handler tự đăng ký khi kết nối và tự xoá khi ngắt.

Đường gửi lệnh dùng ĐÚNG bộ tag của LLM (`dispatch_control_tags_from_text`) nên KHÔNG sinh thêm
một bộ lệnh hardcode thứ hai: bảng điều khiển gửi `mv:f:steps=5`, `pst:lt`, `srv:dance`, `tof:cal`
… y như một câu trả lời của LLM. Thêm/xoá tag ở codec nào thì bảng điều khiển dùng được ngay.
"""

from __future__ import annotations

import threading
import time
from typing import Any

TAG = "LiveDevices"

_LOCK = threading.Lock()
_DEVICES: dict[str, Any] = {}
_CONNECTED_AT: dict[str, float] = {}


def _device_key(handler: Any) -> str:
    device_id = getattr(handler, "device_id", None)
    if device_id:
        return str(device_id)
    return f"unknown-{id(handler)}"


def register(handler: Any) -> None:
    """Gọi khi thiết bị vừa kết nối (sau khi đã biết `device_id`)."""
    key = _device_key(handler)
    with _LOCK:
        _DEVICES[key] = handler
        _CONNECTED_AT[key] = time.time()
    logger = getattr(handler, "logger", None)
    if logger is not None:
        logger.bind(tag=TAG).info(f"thiết bị đăng ký bảng điều khiển: {key}")


def unregister(handler: Any) -> None:
    """Gọi khi thiết bị ngắt kết nối."""
    key = _device_key(handler)
    with _LOCK:
        existed = _DEVICES.pop(key, None) is not None
        _CONNECTED_AT.pop(key, None)
    if existed:
        logger = getattr(handler, "logger", None)
        if logger is not None:
            logger.bind(tag=TAG).info(f"thiết bị rời bảng điều khiển: {key}")


def snapshot() -> list[dict]:
    """Danh sách thiết bị đang kết nối (cho `GET /robot/devices`)."""
    with _LOCK:
        items = list(_DEVICES.items())
    now = time.time()
    devices: list[dict] = []
    for key, handler in items:
        state = getattr(handler, "client_state", None) or getattr(handler, "state", None)
        devices.append(
            {
                "device_id": key,
                "board": getattr(handler, "device_board", None),
                "state": str(state) if state is not None else None,
                "ip": getattr(handler, "client_ip", None),
                "session_id": getattr(handler, "session_id", None),
                "connected_seconds": int(now - _CONNECTED_AT.get(key, now)),
            }
        )
    return devices


def count() -> int:
    with _LOCK:
        return len(_DEVICES)


def pick(selector: str | None) -> Any | None:
    """Chọn handler theo `selector` (device-id, khớp một phần). None ⇒ máy duy nhất / máy đầu."""
    with _LOCK:
        items = list(_DEVICES.items())
    if not items:
        return None
    if selector:
        wanted = str(selector).strip().lower()
        for key, handler in items:
            if key.lower() == wanted:
                return handler
        for key, handler in items:
            if wanted in key.lower():
                return handler
        return None
    return items[0][1]
