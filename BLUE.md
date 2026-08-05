# Blue V1 stack

Backend for the [esp32-blue](https://github.com/) / `xiaozhi-esp32` firmware (Blue V1 robot).

| Repo | Role |
|------|------|
| `esp32-blue` / `xiaozhi-esp32` | ESP32-S3 firmware — **`blue-v2`** (pin-optimized PCB) or **`blue-v1`** (legacy) |
| `esp32-server-blue` | Xiaozhi Python server + Kira character + robot motion |

## Quick start (Mac)

```bash
cd esp32-server-blue/main/xiaozhi-server

# Python 3.10 (pyenv recommended)
pyenv local 3.10.19
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Config (local, gitignored)
mkdir -p data tmp
cp data/.config.yaml.example data/.config.yaml   # or: ./run.sh auto-creates on first run
# Edit data/.config.yaml — Gemini API key + OpenAI ASR key + LAN websocket URL

# ASR model (optional if using OpenaiASR only)
# Download model.pt → models/SenseVoiceSmall/model.pt
# https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt
```

Run from repo root:

```bash
./run.sh
```

WebSocket endpoint (default): `ws://<your-lan-ip>:8000/xiaozhi/v1/`

Point the ESP32 OTA / WiFi config at that URL. Pin maps: [Blue V2 wiring](../esp32-blue/main/boards/blue-v2/WIRING.md) (new PCB) · [Blue V1](../esp32-blue/main/boards/blue-v1/WIRING.md) (legacy).

## Blue-specific features

- **Kira character** — `core/characters/` (personality, memory, wake-word switch)
- **Gemini Flash brain** — `GeminiLLM` / `gemini-flash-latest` (`generativelanguage.googleapis.com` v1beta)
- **Robot motion tags** — LLM output includes `mv:*` codes; server dispatches via MCP to firmware motor tools (`core/utils/robot_move_codec.py`)
- **Vietnamese voice** — Edge TTS `vi-VN-HoaiMyNeural`, OpenAI ASR with Vietnamese prompt

- **Vietnamese / English runtime** — auto-switch ASR + TTS voice per user message (`language_runtime` in config)

## LLM (Gemini Flash)

| Component | Config |
|-----------|--------|
| Provider | `selected_module.LLM: GeminiLLM` |
| Model | `gemini-flash-latest` |
| API | [Google AI Studio key](https://aistudio.google.com/apikey) in `data/.config.yaml` |

```yaml
LLM:
  GeminiLLM:
    api_key: YOUR_GEMINI_API_KEY
```

If Gemini is blocked from your network, add proxy under `GeminiLLM` (`http_proxy` / `https_proxy` in `config.yaml`).

## Language runtime (vi / en)

Default stack is optimized for **Vietnamese**. When the user speaks/writes **English**, the server switches **per WebSocket connection** (sticky until switched back):

| Component | Vietnamese (`vi`) | English (`en`) |
|-----------|-------------------|----------------|
| ASR `language` | `vi` | `en` |
| ASR prompt | Vietnamese diacritics, anti-Thai | English-only |
| TTS voice | `vi-VN-HoaiMyNeural` | `en-US-JennyNeural` |
| TTS normalize | Vietnamese spacing fixes | off |

Detection: diacritics / Vietnamese keywords → `vi`; mostly ASCII → `en`. Explicit: *"speak English"*, *"nói tiếng anh"*.

```yaml
language_runtime:
  default_locale: vi
  locales:
    vi:
      asr_language: vi
      tts_voice: vi-VN-HoaiMyNeural
    en:
      asr_language: en
      tts_voice: en-US-JennyNeural
```

Log: `[locale] vi → en (user_text)` on switch.

## Robot motion (`mv:*`)

Kira appends compact tags at the **end** of spoken replies. Tags are stripped before TTS and executed as MCP motor calls.

| Tag | Action |
|-----|--------|
| `mv:t` | Turn left |
| `mv:p` | Turn right |
| `mv:f` | Forward |
| `mv:b` | Backward |
| `mv:s` | Stop |

**Multi-step example:** user says *"đi tới, quẹo phải và dừng lại"* → LLM reply ends with `mv:f mv:p mv:s`.

**Duration:** optional seconds suffix — `mv:t:10` = turn left **10 s**. Default **5 s**, max **30 s** (config). Server calls `self.motor.move` with `duration_ms` when the device supports it.

### Sequence limits (config)

| Key | Default | Meaning |
|-----|---------|---------|
| `robot_move_default_duration_seconds` | `5` | Motor run time when tag has no `:N` |
| `robot_move_max_duration_seconds` | `30` | Clamp for `:N` suffix |
| `robot_move_max_sequence` | `3` | Max motor steps per reply |
| `robot_move_step_delay_seconds` | `5` | Gap between steps if step has no duration (e.g. stop) |

Set in `main/xiaozhi-server/data/.config.yaml` (or root `config.yaml`).

### Per-device queue

Motor queue is **not global**. Each WebSocket connection gets its own `ConnectionHandler`:

- `_robot_move_sequence_queue`, cooldown timer, and MCP client are **per connection**
- Device A and device B run sequences **in parallel** without interfering
- MCP commands go only to the websocket that received that LLM reply

Typical setup: **1 ESP32 = 1 WebSocket = 1 queue**. If the same `device-id` opens two connections (sim + hardware), each has an independent queue — commands follow the active voice session.

On disconnect / Ctrl+C, `_shutdown_robot_moves()` cancels pending timers and clears that connection's queue.

### Flow

```
User speech → ASR → LLM (Kira) → reply + mv:* tags
  → strip tags from TTS text
  → enqueue moves (max 3) on this connection
  → MCP self.motor.* / self.chassis.* to that device
  → 5 s cooldown between steps
```

Implementation: `core/connection.py`, `core/utils/robot_move_codec.py`, `core/characters/kira.py`.

## Local TTS (Piper / Speaches — low latency)

Run Piper voices **on your Mac** via [Speaches](https://speaches.ai/) Docker (OpenAI-compatible API). No Edge TTS cloud hop → typically **~0.3–1 s** synthesis from Vietnam.

### 1. Start TTS container + download models

From repo root:

```bash
chmod +x run-tts.sh
./run-tts.sh setup    # pull image, start on :8881, download vi + en Piper models
./run-tts.sh test     # writes /tmp/blue-tts-vi.wav and /tmp/blue-tts-en.wav
```

| Model | HuggingFace ID |
|-------|----------------|
| Vietnamese | `speaches-ai/piper-vi_VN-25hours_single-low` |
| English | `speaches-ai/piper-en_US-lessac-medium` |

Override with env: `TTS_VI_MODEL`, `TTS_EN_MODEL`, `TTS_HOST_PORT` (default `8881`).

### 2. Point xiaozhi-server at local TTS

In `main/xiaozhi-server/data/.config.yaml`:

```yaml
selected_module:
  TTS: CustomTTS

language_runtime:
  default_locale: vi
  locales:
    vi:
      tts_voice: speaches-ai/piper-vi_VN-25hours_single-low
      tts_speeches_voice: 25hours_single
    en:
      tts_voice: speaches-ai/piper-en_US-lessac-medium
      tts_speeches_voice: lessac

TTS:
  CustomTTS:
    type: custom
    method: POST
    url: "http://127.0.0.1:8881/v1/audio/speech"
    default_voice: default
    format: wav
    output_dir: tmp/
    params:
      input: "{prompt_text}"
      model: "{model}"
      voice: "{voice}"
      response_format: "wav"
      speed: 1.0
```

`language_runtime` sets `{model}` per locale; `{voice}` is usually `default` for single-speaker Piper models.

### 3. Restart server

```bash
./run.sh
```

### Optional: VieNeu (higher quality, GPU)

For NVIDIA GPU hosts, VieNeu-TTS v2 Docker (`pnnbao/vieneu-tts`) gives better Vietnamese quality but needs CUDA. Not required for Mac CPU — use Piper above. See [VieNeu-TTS](https://github.com/pnnbao-ump/VieNeu-TTS).

## Digital human (browser)

Optional Live2D client in `main/digital-human/` — same WebSocket server, 16 kHz Opus.

Use the **same venv** as xiaozhi-server (do not create a separate `.venv` inside `digital-human`):

```bash
cd esp32-server-blue/main/xiaozhi-server
source .venv/bin/activate
pip install -r ../digital-human/wakeword_runtime/requirements.txt

cd ../digital-human
python start.py
```

Open http://127.0.0.1:8006/index.html

Wakeword models live in `main/digital-human/wakeword_runtime/models/` (copy locally — do not symlink to the old `xiaozhi-esp32-server` repo). Default wake words: `kira`, `hey kira`, `lili`, `hey lili`.

Motor tools are **simulated in the browser** (`"simulated": true` in MCP responses). Refresh the page and reconnect WebSocket after server updates.

If the browser still shows old wake words, clear site data for `127.0.0.1:8006` or run in DevTools: `localStorage.clear()`

## Firmware dev (QEMU)

Run Blue V1 in ESP-IDF QEMU without hardware — see firmware docs:

- [esp32-blue/main/boards/blue-v1/QEMU.md](../esp32-blue/main/boards/blue-v1/QEMU.md)

Quick run (after `python scripts/build.py blue-v1`):

```bash
pkill -9 qemu-system-xtensa 2>/dev/null
pkill -f "idf.py qemu" 2>/dev/null
export IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf6.0_py3.12_env
source ~/esp/esp-idf/export.sh
cd ~/work/xiaozhi-esp32
idf.py qemu
```

## Upstream

Based on [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server). Docker and manager-api docs in [README.md](./README.md).
