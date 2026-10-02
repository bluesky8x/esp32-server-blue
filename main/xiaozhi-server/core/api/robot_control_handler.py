"""Bảng điều khiển robot qua HTTP (LAN) — `GET /robot/` + API JSON.

Cách hoạt động: trang web chỉ gửi **chuỗi tag** đúng như LLM vẫn gửi (`mv:f:steps=5`, `pst:lt`,
`srv:dance`, `tof:cal`, `srv:rot=90:80:0`…). Server tìm thiết bị đang kết nối rồi đưa chuỗi đó
vào ĐÚNG đường dispatch tag của LLM (`dispatch_control_tags_from_text`) ⇒ không sinh bộ lệnh
hardcode thứ hai, và mọi tag mới thêm sau này tự động có trên bảng điều khiển.

| Route | Việc |
|---|---|
| `GET  /robot/` | trang điều khiển (HTML, không cần build) |
| `GET  /robot/devices` | JSON: thiết bị đang kết nối (device_id, board, state, ip…) |
| `POST /robot/cmd` | `{"device": "<id|một phần id>", "text": "mv:f:steps=5"}` → gửi tag |
| `POST /robot/raw` | `{"device": …, "tool": "self.tof.get_distance", "arguments": {}}` → gọi tool thiết bị và TRẢ KẾT QUẢ (để đọc số đo/trạng thái) |

An toàn: chỉ dành cho LAN (không auth), đúng như yêu cầu. Không mở cổng mới — dùng chung cổng
`http_port` (mặc định 8003) với OTA.
"""

from __future__ import annotations

import json
import time
import uuid

from aiohttp import web

from config.logger import setup_logging
from core.utils import live_devices

TAG = "RobotPanel"

_JSON_HEADERS = {"Access-Control-Allow-Origin": "*"}


def _body(payload: dict, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, headers=_JSON_HEADERS)


def _available_tools(handler) -> set[str]:
    """Danh sách tool thiết bị đã đăng ký (tên đã chuẩn hoá) — dùng cho `/robot/raw` + `/robot/tools`."""
    try:
        names = handler._robot_move_available_tools()
    except Exception:
        names = set()
    return set(names or ())


class RobotControlHandler:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()

    # ------------------------------------------------------------------ routes
    async def handle_page(self, request: web.Request) -> web.Response:
        return web.Response(text=_PANEL_HTML, content_type="text/html", headers=_JSON_HEADERS)

    async def handle_devices(self, request: web.Request) -> web.Response:
        return _body({"ok": True, "devices": live_devices.snapshot()})

    async def handle_options(self, request: web.Request) -> web.Response:
        return web.Response(
            status=204,
            headers={
                **_JSON_HEADERS,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    async def handle_cmd(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return _body({"ok": False, "error": "body phải là JSON"}, 400)

        text = str(data.get("text") or "").strip()
        if not text:
            return _body({"ok": False, "error": "thiếu 'text' (chuỗi tag)"}, 400)

        selector = data.get("device")
        handler = live_devices.pick(selector)
        if handler is None:
            return _body(
                {
                    "ok": False,
                    "error": "không có thiết bị nào đang kết nối"
                    if not selector
                    else f"không thấy thiết bị khớp '{selector}'",
                    "devices": live_devices.snapshot(),
                },
                404,
            )

        from core.utils.assistant_reply_tags import dispatch_control_tags_from_text

        device_id = getattr(handler, "device_id", None)
        try:
            # sentence_id PHẢI DUY NHẤT cho mỗi lần bấm. Server dedupe lệnh theo
            # `(sentence_id, mã, số bước…)` trong `_executed_robot_moves` / `_executed_postures` /
            # `_executed_servo_cmds`, và chỉ reset khi có LƯỢT CHAT mới của người dùng — panel không
            # đi qua lượt chat nên dùng id cố định thì bấm lại cùng một nút sẽ bị coi là "trùng" và
            # bị bỏ qua (lỗi gặp thật 29-09: đi 3 bước lần 1 chạy, lần 2 không đi). Id duy nhất ⇒
            # mọi lần bấm đều là lượt mới, đồng thời vẫn chống trùng trong CÙNG một request.
            sentence_id = f"web-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
            dispatch_control_tags_from_text(
                handler,
                text,
                label="web_panel",
                sentence_id=sentence_id,
                defer_post_tts=False,
            )
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"[panel] gửi tag lỗi: {exc}")
            return _body({"ok": False, "error": f"gửi tag lỗi: {exc}"}, 500)

        self.logger.bind(tag=TAG).info(f"[panel] {device_id} ← «{text}» ({sentence_id})")
        return _body({"ok": True, "device_id": device_id, "text": text})

    async def handle_tools(self, request: web.Request) -> web.Response:
        handler = live_devices.pick(request.query.get("device"))
        if handler is None:
            return _body({"ok": False, "error": "không có thiết bị đang kết nối"}, 404)
        return _body({"ok": True, "tools": sorted(_available_tools(handler))})

    async def handle_raw(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return _body({"ok": False, "error": "body phải là JSON"}, 400)

        tool = str(data.get("tool") or "").strip()
        if not tool:
            return _body({"ok": False, "error": "thiếu 'tool'"}, 400)

        handler = live_devices.pick(data.get("device"))
        if handler is None:
            return _body({"ok": False, "error": "không có thiết bị đang kết nối"}, 404)

        func_handler = getattr(handler, "func_handler", None)
        if func_handler is None:
            return _body({"ok": False, "error": "thiết bị chưa sẵn sàng (chưa nạp tool)"}, 409)

        # Tên tool gửi xuống thiết bị phải ở dạng ĐÃ CHUẨN HOÁ (`self_tof_get_distance`), vì đó là
        # danh sách mà server đăng ký với LLM. Nhận cả dạng có dấu chấm cho tiện.
        from core.utils.util import sanitize_tool_name

        tools = _available_tools(handler)
        resolved = None
        for candidate in (tool, sanitize_tool_name(tool)):
            if candidate in tools:
                resolved = candidate
                break
        if resolved is None:
            resolved = sanitize_tool_name(tool)
            if tools and resolved not in tools:
                suggestion = sorted(n for n in tools if tool.replace(".", "_") in n)[:8]
                return _body(
                    {
                        "ok": False,
                        "error": f"thiết bị không có tool '{tool}'",
                        "tool": resolved,
                        "goi_y": suggestion,
                        "goi_y_tat_ca": sorted(tools)[:80],
                    },
                    404,
                )
        tool = resolved

        arguments = data.get("arguments") or {}
        if isinstance(arguments, str):
            args_json = arguments
        else:
            args_json = json.dumps(arguments, ensure_ascii=False)

        try:
            result = await func_handler.handle_llm_function_call(
                handler, {"name": tool, "arguments": args_json}
            )
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"[panel] gọi tool lỗi: {tool}: {exc}")
            return _body({"ok": False, "error": f"gọi tool lỗi: {exc}"}, 500)

        payload = getattr(result, "result", None) or getattr(result, "response", None)
        return _body({"ok": True, "tool": tool, "result": payload})


_PANEL_HTML = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Điều khiển robot</title>
<style>
 :root { color-scheme: dark; }
 body { font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 16px;
        background: #12151a; color: #e6e8eb; }
 h1 { font-size: 18px; margin: 0 0 12px; }
 h2 { font-size: 13px; margin: 18px 0 8px; color: #8fb3ff; text-transform: uppercase;
      letter-spacing: .06em; }
 .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
 button { background: #1e2530; color: #e6e8eb; border: 1px solid #2f3a49; border-radius: 10px;
          padding: 10px 14px; font-size: 14px; cursor: pointer; }
 button:hover { background: #273141; }
 button.hot { border-color: #3d5bff; }
 button.warn { border-color: #b3572b; }
 input, select { background: #1a2029; color: #e6e8eb; border: 1px solid #2f3a49;
                 border-radius: 8px; padding: 8px 10px; font-size: 14px; }
 input[type=number] { width: 72px; }
 #bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
 #log { margin-top: 16px; background: #0d1015; border: 1px solid #222a35; border-radius: 10px;
        padding: 10px; height: 190px; overflow: auto; font-family: ui-monospace, monospace;
        font-size: 12px; white-space: pre-wrap; }
 .muted { color: #8b96a5; font-size: 12px; }
</style></head>
<body>
<h1>Điều khiển robot</h1>
<div id="bar">
  <select id="device"><option value="">(đang tải…)</option></select>
  <button onclick="loadDevices()">↻ Thiết bị</button>
  <span class="muted" id="devInfo"></span>
</div>

<h2>Trạng thái</h2>
<div class="row">
  <button onclick="send('pst:std')">Đứng</button>
  <button onclick="send('pst:sit')">Ngồi</button>
  <button class="warn" onclick="send('mv:s')">Dừng</button>
  <button onclick="send('srv:status')">Trạng thái servo</button>
  <button onclick="raw('self.tof.get_distance')">Đọc ToF</button>
</div>

<h2>Di chuyển</h2>
<div class="row">
  <input type="number" id="steps" value="3" min="1" max="8" title="số bước đi">
  <button onclick="send('mv:f:steps='+val('steps'))">Đi tới N bước</button>
  <button onclick="send('mv:b:steps='+val('steps'))">Lùi N bước</button>
</div>
<div class="row" style="margin-top:8px">
  <input type="number" id="tsteps" value="1" min="1" max="8" title="số bước quay (1 bước ≈ 87°)">
  <button onclick="send('mv:t:steps='+val('tsteps'))">⟲ Quay trái N bước</button>
  <button onclick="send('mv:p:steps='+val('tsteps'))">⟳ Quay phải N bước</button>
  <button onclick="send('mv:t:steps=1')">⟲ 1 bước</button>
  <button onclick="send('mv:p:steps=1')">⟳ 1 bước</button>
</div>
<span class="muted">Quay tại chỗ theo BƯỚC (1 bước ≈ 87°: 4 bước ≈ trọn vòng — hip quét 55°↔125°). Dùng bước
thay vì giây để khớp gait quay mới; trong lúc quay guard ToF tạm nhịn.</span>

<h2>Biểu diễn</h2>
<div class="row">
  <button class="hot" onclick="send('srv:wave')">Vẫy chân chào</button>
  <button class="hot" onclick="send('srv:dance')">Nhảy</button>
  <button class="hot" onclick="send('mv:d')">Nhảy theo nhạc</button>
  <button class="warn" onclick="send('mv:s')">Dừng nhảy</button>
</div>

<h2>Tư thế</h2>
<div class="row">
  <input type="number" id="deg" value="12" min="5" max="20" title="độ nghiêng">
  <button onclick="send('pst:lt:'+val('deg'))">Nghiêng trái</button>
  <button onclick="send('pst:rt:'+val('deg'))">Nghiêng phải</button>
  <button onclick="send('pst:fw:'+val('deg'))">Chúi tới</button>
  <button onclick="send('pst:bw:'+val('deg'))">Ngửa ra sau</button>
  <button onclick="send('pst:std')">Về tư thế đứng</button>
</div>

<h2>Cảm biến khoảng cách (ToF)</h2>
<div class="row">
  <button onclick="send('tof:cal')">Hiệu chuẩn</button>
  <button class="warn" onclick="send('tof:clr')">Xoá hiệu chuẩn</button>
  <button onclick="send('tof:guard=1')">Bật guard</button>
  <button class="warn" onclick="send('tof:guard=0')">Tắt guard</button>
  <button onclick="raw('self.tof.get_distance')">Đọc khoảng cách</button>
</div>

<h2>Màn hình</h2>
<div class="row">
  <button onclick="send('srv:rot=0')">0°</button>
  <button onclick="send('srv:rot=90')">90°</button>
  <button onclick="send('srv:rot=180')">180°</button>
  <button onclick="send('srv:rot=270')">270°</button>
  <input type="number" id="gx" value="80" title="bù GRAM X">
  <input type="number" id="gy" value="0" title="bù GRAM Y">
  <button onclick="send('srv:rot=90:'+val('gx')+':'+val('gy'))">Áp 90° + bù</button>
</div>

<h2>Tốc độ &amp; servo</h2>
<div class="row">
  <input type="number" id="speed" value="30" min="1" max="200">
  <button onclick="send('srv:speed=+'+val('speed'))">Nhanh hơn N%</button>
  <button onclick="send('srv:speed=-'+val('speed'))">Chậm hơn N%</button>
  <button onclick="send('srv:speed=100')">Về 100%</button>
  <button onclick="send('srv:speed')">Đọc tốc độ</button>
  <button onclick="send('srv:relax')">Thả lỏng</button>
  <button onclick="send('srv:enable')">Cấp lực</button>
</div>

<h2>Test biên độ 1 servo (bring-up)</h2>
<div class="row">
  <select id="sjoint" title="joint 0..7">
    <option value="0">0 · FL hip</option><option value="1">1 · FL knee</option>
    <option value="2">2 · FR hip</option><option value="3">3 · FR knee</option>
    <option value="4">4 · RL hip</option><option value="5">5 · RL knee</option>
    <option value="6">6 · RR hip</option><option value="7">7 · RR knee</option>
  </select>
  <input type="number" id="samp" value="90" min="5" max="180" title="biên độ TỔNG — 90 = quét 45°↔135° (±45° quanh 90°)">
  <button onclick="sweepAmp(0)">Quét biên độ này</button>
  <button onclick="sweepAmp(-5)">−5°</button>
  <button onclick="sweepAmp(5)">+5°</button>
  <button onclick="sweepPct(-20)">−20%</button>
  <button onclick="sweepPct(20)">+20%</button>
  <button onclick="sweepFull()">Hết hành trình 0→180°</button>
  <input type="number" id="sms" value="600" min="200" max="15000" title="ms mỗi lượt quét">
  <input type="number" id="slaps" value="2" min="1" max="4" title="số lượt (mỗi lượt đi-về)">
</div>
<div class="row" style="margin-top:8px">
  <select id="sleg" title="chân 0..3">
    <option value="0">chân 0 · trước-trái</option><option value="1">chân 1 · trước-phải</option>
    <option value="2">chân 2 · sau-trái</option><option value="3">chân 3 · sau-phải</option>
  </select>
  <input type="number" id="ship" value="90" min="10" max="170" title="hip_deg — biên độ quét ngang (90 = quét ±45°)">
  <input type="number" id="sknee" value="40" min="0" max="90" title="knee_deg — độ nhấc bàn chân">
  <input type="number" id="sldur" value="2000" min="1000" max="15000" title="duration_ms">
  <button onclick="legSweep()">Quét 1 chân (gait hip/knee)</button>
  <button onclick="call('self.servo.get_positions',{})">Đọc góc 8 servo</button>
</div>
<span class="muted">`self.servo.sweep` quét ĐÚNG 1 servo 0..7 quanh 90°, `end_neutral=1` nên xong tự về
90°. `self.gait.leg_sweep` nhấc ĐÚNG 1 chân theo biên độ hip/knee, 3 chân kia đứng yên. Test xong
nên bấm <b>Đứng</b> ở mục Trạng thái để gait về tư thế chuẩn.<br>
<b>Quy đổi biên độ hip:</b> ô biên độ = <b>tổng</b> độ quét ⇒ <b>70 = hip đi 55° ↔ 125°</b> (±35°
quanh 90°) — chính là hành trình lệnh quay đang dùng; 49.5 = ±24.75° (hành trình đi bộ). Với khớp
knee nên giữ ≤ 40° để bàn chân không quật vào thân.</span>

<h2>Tag tự do / tool thô</h2>
<div class="row">
  <input id="free" size="30" placeholder="vd: mv:f:steps=5  ·  srv:rot=270">
  <button onclick="send(document.getElementById('free').value)">Gửi tag</button>
</div>
<div class="row" style="margin-top:8px">
  <input id="tool" size="26" placeholder="self_tof_get_distance">
  <select id="toolList" onchange="document.getElementById('tool').value=this.value">
    <option value="">(danh sách tool)</option>
  </select>
  <button onclick="loadTools()">↻ Tools</button>
  <button onclick="raw(document.getElementById('tool').value)">Gọi tool</button>
</div>

<div id="log"></div>

<script>
function val(id) { return document.getElementById(id).value || 0; }
function log(msg, cls) {
  const el = document.getElementById('log');
  const t = new Date().toLocaleTimeString();
  el.textContent = '[' + t + '] ' + msg + '\\n' + el.textContent;
}
async function loadDevices() {
  try {
    const r = await fetch('/robot/devices');
    const j = await r.json();
    const sel = document.getElementById('device');
    const keep = sel.value;
    sel.innerHTML = '';
    (j.devices || []).forEach(d => {
      const o = document.createElement('option');
      o.value = d.device_id;
      o.textContent = d.device_id + (d.board ? ' · ' + d.board : '');
      sel.appendChild(o);
    });
    if (!j.devices || !j.devices.length) {
      const o = document.createElement('option');
      o.value = ''; o.textContent = '(chưa có máy nào kết nối)'; sel.appendChild(o);
      document.getElementById('devInfo').textContent = '';
    } else {
      if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
      document.getElementById('devInfo').textContent = j.devices.length + ' máy';
    }
  } catch (e) { log('LỖI tải thiết bị: ' + e); }
}
async function post(url, payload) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify(payload)});
  return await r.json();
}
async function send(text) {
  if (!text) return;
  const device = document.getElementById('device').value || null;
  try {
    const j = await post('/robot/cmd', {device, text});
    log((j.ok ? 'OK  ' : 'LỖI ') + text + (j.ok ? '' : (' — ' + j.error)));
  } catch (e) { log('LỖI ' + text + ' — ' + e); }
}
async function raw(tool) {
  if (!tool) return;
  const device = document.getElementById('device').value || null;
  try {
    const j = await post('/robot/raw', {device, tool, arguments: {}});
    log('TOOL ' + tool + ' → ' + (j.ok ? JSON.stringify(j.result) : ('LỖI ' + j.error)));
  } catch (e) { log('LỖI ' + tool + ' — ' + e); }
}
async function call(tool, args) {
  const device = document.getElementById('device').value || null;
  try {
    const j = await post('/robot/raw', {device, tool, arguments: args || {}});
    log('TOOL ' + tool + ' ' + JSON.stringify(args || {}) + ' → ' +
        (j.ok ? JSON.stringify(j.result) : ('LỖI ' + j.error)));
  } catch (e) { log('LỖI ' + tool + ' — ' + e); }
}
function num(id) { return parseInt(document.getElementById(id).value, 10) || 0; }
function amp(id, delta) {
  const el = document.getElementById(id);
  const v = Math.max(5, Math.min(180, (parseInt(el.value, 10) || 60) + delta));
  el.value = v;
  return v;
}
function sweepAmp(delta) {
  const a = amp('samp', delta);
  const half = a / 2;
  call('self.servo.sweep', {joint: num('sjoint'), from_deg: Math.round(90 - half),
                            to_deg: Math.round(90 + half), duration_ms: num('sms'),
                            laps: num('slaps'), end_neutral: 1});
}
function sweepPct(pct) {
  const el = document.getElementById('samp');
  const cur = parseInt(el.value, 10) || 60;
  const next = Math.max(5, Math.min(180, Math.round(cur * (1 + pct / 100))));
  el.value = next;
  const half = next / 2;
  call('self.servo.sweep', {joint: num('sjoint'), from_deg: Math.round(90 - half),
                            to_deg: Math.round(90 + half), duration_ms: num('sms'),
                            laps: num('slaps'), end_neutral: 1});
}
function sweepFull() {
  call('self.servo.sweep', {joint: num('sjoint'), from_deg: 0, to_deg: 180,
                            duration_ms: num('sms'), laps: num('slaps'), end_neutral: 1});
}
function legSweep() {
  call('self.gait.leg_sweep', {leg: num('sleg'), hip_deg: num('ship'), knee_deg: num('sknee'),
                               duration_ms: num('sldur')});
}
async function loadTools() {
  const device = document.getElementById('device').value || '';
  try {
    const r = await fetch('/robot/tools?device=' + encodeURIComponent(device));
    const j = await r.json();
    const sel = document.getElementById('toolList');
    sel.innerHTML = '<option value="">(danh sách tool)</option>';
    (j.tools || []).forEach(t => {
      const o = document.createElement('option');
      o.value = t; o.textContent = t; sel.appendChild(o);
    });
    log('TOOLS: ' + (j.ok ? (j.tools || []).length + ' tool' : ('LỖI ' + j.error)));
  } catch (e) { log('LỖI tải tool: ' + e); }
}
loadDevices();
setInterval(loadDevices, 10000);
</script>
</body></html>
"""
