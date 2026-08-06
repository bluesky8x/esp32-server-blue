# Blue V1 stack

Backend for the [esp32-blue](https://github.com/) / `xiaozhi-esp32` firmware (Blue V1 robot).

| Repo | Role |
|------|------|
| `esp32-blue` / `xiaozhi-esp32` | ESP32-S3 firmware — **`blue-v2`** (pin-optimized PCB) or **`blue-v1`** (legacy) |
| `esp32-server-blue` | Xiaozhi Python server + Kira character + robot motion |

## Quick start (Ubuntu — Docker Compose)

Requires Docker Engine + Compose v2 on Ubuntu (or any Linux host).

```bash
cd esp32-server-blue

# Optional env (timezone, ports)
cp docker/.env.example docker/.env

# Config (API keys + LAN websocket URL for ESP32)
mkdir -p main/xiaozhi-server/data main/xiaozhi-server/tmp
cp main/xiaozhi-server/data/.config.yaml.example main/xiaozhi-server/data/.config.yaml
# Edit data/.config.yaml:
#   server.websocket: ws://<ubuntu-lan-ip>:8000/xiaozhi/v1/
#   Gemini + OpenAI API keys

chmod +x run-docker.sh
./run-docker.sh up          # server on :8000 (WS) and :8003 (OTA/HTTP)
./run-docker.sh logs
```

Verify OTA from another machine on the LAN:

```bash
curl http://<ubuntu-lan-ip>:8003/xiaozhi/ota/
```

Optional local Piper TTS (lower latency than Edge TTS):

```bash
./run-docker.sh up-tts
./run-tts.sh setup
# In data/.config.yaml: selected_module.TTS: CustomTTS
# url: http://speaches:8000/v1/audio/speech  (Docker network name)
```

Point ESP32 OTA / WiFi config at `http://<ubuntu-lan-ip>:8003/xiaozhi/ota/`.

First image build downloads PyTorch and can take several minutes.

## macOS — install dependencies (local build)

One-time setup on Mac (Apple Silicon or Intel) before `./run.sh`. Blue defaults use **cloud APIs** (Gemini LLM, OpenAI ASR, Edge TTS) — no local GPU or FunASR model required.

### What you need

| Item | Required | Notes |
|------|----------|--------|
| macOS 12+ | Yes | Ventura / Sonoma / Sequoia |
| Xcode Command Line Tools | Yes | C compiler for some pip wheels |
| [Homebrew](https://brew.sh/) | Recommended | Installs ffmpeg, opus, pyenv, git |
| Python **3.10.19** | Yes | Via **pyenv** (see `.python-version`) |
| ffmpeg | Yes | Checked at startup (`app.py`) |
| libopus | Yes | Used by `opuslib_next` for ESP32 audio |
| Docker Desktop | Optional | Local Piper TTS via `./run-tts.sh` |
| FunASR model | Optional | Only if `selected_module.ASR: FunASR` |

Disk: allow **~3 GB** for the venv (PyTorch + deps). RAM: **2 GB+** with all-API config; **4 GB+** if using local FunASR.

### 1. Xcode Command Line Tools

```bash
xcode-select --install
```

### 2. Homebrew + system libraries

Install Homebrew if missing: [https://brew.sh](https://brew.sh)

```bash
brew update
brew install git pyenv ffmpeg opus
```

Verify:

```bash
ffmpeg -version    # must print "ffmpeg version ..."
```

### 3. pyenv + Python 3.10.19

Add pyenv to your shell (zsh — append to `~/.zshrc` if not already there):

```bash
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
```

Reload the shell (`source ~/.zshrc`), then:

```bash
pyenv install 3.10.19    # first time only; may take a few minutes
cd esp32-server-blue/main/xiaozhi-server
pyenv local 3.10.19      # reads .python-version
python --version         # Python 3.10.19
```

### 4. Python venv + pip dependencies

```bash
cd esp32-server-blue/main/xiaozhi-server
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

First `pip install` downloads PyTorch and can take **10–20 minutes** on a slow connection.

### 5. Config + API keys

```bash
mkdir -p data tmp
cp data/.config.yaml.example data/.config.yaml
```

Edit `data/.config.yaml`:

| Key | Example |
|-----|---------|
| `server.websocket` | `ws://192.168.x.x:8000/xiaozhi/v1/` |
| `server.vision_explain` | `http://192.168.x.x:8003/mcp/vision/explain` |
| `LLM.GeminiLLM.api_key` | [Google AI Studio](https://aistudio.google.com/apikey) |
| `ASR.OpenaiASR.api_key` | OpenAI API key |

LAN IP on Mac:

```bash
ipconfig getifaddr en0    # Wi‑Fi; use en1 if en0 is empty
```

ESP32 OTA URL (WiFi portal → Advanced): `http://<lan-ip>:8003/xiaozhi/ota/`

### 6. macOS Firewall (ESP32 / LAN access)

If `curl http://127.0.0.1:8003/xiaozhi/ota/` works but `http://<lan-ip>:8003/...` fails, the firewall is blocking **pyenv Python**:

**System Settings → Network → Firewall → Options** → find **python3.10** (`~/.pyenv/versions/3.10.19/bin/python3.10`) → **Allow incoming connections**.

Or:

```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp \
  "$HOME/.pyenv/versions/3.10.19/bin/python3.10"
```

### 7. Optional — local ASR (FunASR)

Skip if using default **OpenaiASR**.

```bash
mkdir -p models/SenseVoiceSmall
curl -L -o models/SenseVoiceSmall/model.pt \
  "https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt"
```

Set `selected_module.ASR: FunASR` in `data/.config.yaml`.

### 8. Optional — Docker (local Piper TTS)

For low-latency TTS instead of Edge TTS:

```bash
# Install Docker Desktop for Mac, then from repo root:
chmod +x run-tts.sh
./run-tts.sh setup
```

See [Local TTS (Piper / Speaches)](#local-tts-piper--speaches--low-latency) for `data/.config.yaml` changes.

### 9. Optional — digital human (browser client)

Uses the **same venv** as xiaozhi-server:

```bash
cd esp32-server-blue/main/xiaozhi-server
source .venv/bin/activate
pip install -r ../digital-human/wakeword_runtime/requirements.txt

cd ../digital-human
python start.py
# Open http://127.0.0.1:8006/index.html
```

### Verify installation

```bash
cd esp32-server-blue
./run.sh
```

In another terminal:

```bash
curl -s http://127.0.0.1:8003/xiaozhi/ota/ | head
curl -s "http://$(ipconfig getifaddr en0):8003/xiaozhi/ota/" | head   # LAN
```

Logs: `main/xiaozhi-server/tmp/server.log`

### Troubleshooting (macOS)

| Symptom | Fix |
|---------|-----|
| `ffmpeg` not found / startup error | `brew install ffmpeg`, restart venv |
| `Missing .venv` | Repeat step 4 |
| Wrong Python version | `pyenv local 3.10.19` in `main/xiaozhi-server`, recreate venv |
| LAN/ESP32 can't reach server | Firewall step 6; confirm `server.websocket` uses LAN IP |
| `OSError: [Errno 22] Invalid argument` (aiohttp) | Already patched in `app.py` for macOS — pull latest |
| Gemini / OpenAI errors | Check API keys and network; add proxy in config if blocked |

---

## Quick start (Mac)

After [install dependencies](#macos--install-dependencies-local-build) above:

```bash
cd esp32-server-blue
./run.sh
```

WebSocket endpoint: `ws://<your-lan-ip>:8000/xiaozhi/v1/`

Point the ESP32 OTA / WiFi config at `http://<your-lan-ip>:8003/xiaozhi/ota/`. Pin maps: [Blue V2 wiring](../esp32-blue/main/boards/blue-v2/WIRING.md) (new PCB) · [Blue V1](../esp32-blue/main/boards/blue-v1/WIRING.md) (legacy).

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
