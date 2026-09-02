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

Optional local TTS (separate Docker stack — configure url in `data/.config.yaml`):

```bash
# VieNeu v3 Turbo (better Vietnamese, CPU/ONNX):
./run-vieneu-tts.sh up
./run-vieneu-tts.sh test

# Kokoro-82M (best English on CPU):
./run-kokoro-tts.sh up
```

See [Local TTS](#local-tts-separate-docker-stack) for `data/.config.yaml` snippets.

Point ESP32 OTA / WiFi config at `http://<ubuntu-lan-ip>:8003/xiaozhi/ota/`.

First image build downloads PyTorch and can take several minutes.

## macOS — install dependencies (local build)

One-time setup on Mac before `./run.sh`. Blue defaults use **cloud APIs** (Gemini LLM, OpenAI ASR, Edge TTS) — no local GPU or FunASR model required.

> **MacBook Pro 2019 (Intel):** use the dedicated guide [docs/macos-intel-build.md](./docs/macos-intel-build.md) — Homebrew paths, pyenv/OpenSSL, USB-C flashing, RAM, and build times for x86_64.

Apple Silicon and other Macs can follow the steps below (same commands; Intel doc covers x86_64 edge cases).

### What you need

| Item | Required | Notes |
|------|----------|--------|
| macOS 12+ | Yes | Ventura / Sonoma / Sequoia |
| Xcode Command Line Tools | Yes | C compiler for some pip wheels |
| [Homebrew](https://brew.sh/) | Recommended | Installs ffmpeg, opus, pyenv, git |
| Python **3.10.19** | Yes | Via **pyenv** (see `.python-version`) |
| ffmpeg | Yes | Checked at startup (`app.py`) |
| libopus | Yes | Used by `opuslib_next` for ESP32 audio |
| Docker Desktop | Optional | Local VieNeu/Kokoro TTS via `./run-vieneu-tts.sh` / `./run-kokoro-tts.sh` |
| FunASR model | Optional | Only if `selected_module.ASR: FunASR` |
| **ESP-IDF 6.0.2** | For firmware | Build/flash [esp32-blue](../esp32-blue/) (separate Python env) |

Disk: allow **~3 GB** for the server venv (PyTorch + deps) and **~5 GB** for ESP-IDF toolchains. RAM: **2 GB+** with all-API config; **4 GB+** if using local FunASR; **8 GB+** recommended for firmware builds.

**Two Python environments:** the server uses **pyenv 3.10.19**; ESP-IDF installs its own **3.12** under `~/.espressif/`. Do not run `./run.sh` in a shell where `export.sh` is sourced unless you know what you are doing.

### 1. Xcode Command Line Tools

```bash
xcode-select --install
```

### 2. Homebrew + system libraries

Install Homebrew if missing: [https://brew.sh](https://brew.sh)

```bash
brew update
brew install git pyenv ffmpeg opus cmake ninja dfu-util ccache
```

`cmake`, `ninja`, and `dfu-util` are for **ESP-IDF** firmware builds. `ccache` speeds up rebuilds (optional).

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

### 8. Optional — Docker (local TTS)

For low-latency TTS instead of Edge TTS:

```bash
# Install Docker Desktop for Mac, then from repo root:
./run-vieneu-tts.sh up    # Vietnamese (VieNeu) on :8882
./run-kokoro-tts.sh up    # English (Kokoro) on :8883
```

See [Local TTS](#local-tts-separate-docker-stack) for `data/.config.yaml` changes.

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

### 10. ESP-IDF (firmware — esp32-blue)

Required to **build and flash** the Blue robot firmware. Skip if you only run the Python server.

Official guide: [ESP-IDF macOS setup](https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32/get-started/macos-setup.html)

#### 10.1 Install ESP-IDF 6.0.2

```bash
mkdir -p ~/esp
cd ~/esp
git clone -b v6.0.2 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3
```

First run downloads compilers and tools into `~/.espressif/` (**10–30 minutes**).

Add to `~/.zshrc`:

```bash
# Load ESP-IDF — pins the install-time Python 3.12 venv (avoids py3.14 mismatch)
get_idf() {
  export IDF_PYTHON_ENV_PATH="$HOME/.espressif/python_env/idf6.0_py3.12_env"
  . "$HOME/esp/esp-idf/export.sh"
}
```

Then run `get_idf` before any `idf.py` / firmware build command.

> **If `source export.sh` fails with** `idf6.0_py3.14_env not found`: your shell’s default `python3` is **3.14**, but ESP-IDF 6.0.2 was installed with **3.12**. Use `get_idf` above, or run once:
> ```bash
> export IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf6.0_py3.12_env
> source ~/esp/esp-idf/export.sh
> ```
> Do **not** use Python 3.14 with ESP-IDF 6.0.2. Either pin `IDF_PYTHON_ENV_PATH` or run `./install.sh` with pyenv 3.12: `pyenv shell 3.12.11 && ./install.sh esp32s3`

Verify:

```bash
source ~/esp/esp-idf/export.sh
idf.py --version          # ESP-IDF v6.0.2
python --version          # 3.12.x from ~/.espressif/python_env/...
```

#### 10.2 Build Blue firmware

```bash
cd esp32-blue
source ~/esp/esp-idf/export.sh

python scripts/build.py blue-v2    # new PCB (recommended)
# python scripts/build.py blue-v1  # legacy PCB
```

First build fetches managed components and can take **15–30 minutes**.

#### 10.3 Flash to hardware

Connect the ESP32-S3 over USB, find the port, then flash:

```bash
ls /dev/cu.usbmodem* /dev/cu.usbserial* 2>/dev/null
source ~/esp/esp-idf/export.sh
cd esp32-blue
idf.py -p /dev/cu.usbmodem1101 flash monitor    # replace with your port
```

In the serial monitor, set **OTA URL** (WiFi config portal → Advanced) to your server:

`http://<mac-lan-ip>:8003/xiaozhi/ota/`

Board docs: [Blue V2](../esp32-blue/main/boards/blue-v2/README.md) · [Blue V1](../esp32-blue/main/boards/blue-v1/README.md) · [Wiring](../esp32-blue/main/boards/blue-v2/WIRING.md)

#### 10.4 QEMU (firmware without hardware)

Requires ESP-IDF **6.x**. See [esp32-blue/main/boards/blue-v2/QEMU.md](../esp32-blue/main/boards/blue-v2/QEMU.md).

```bash
cd esp32-blue
export IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf6.0_py3.12_env
source ~/esp/esp-idf/export.sh
python scripts/build.py blue-v2
idf.py qemu
```

Adjust `IDF_PYTHON_ENV_PATH` if your env name differs: `ls ~/.espressif/python_env/`

### Verify installation

**Server:**

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

**Firmware (after step 10):**

```bash
source ~/esp/esp-idf/export.sh
cd esp32-blue
idf.py --version
python scripts/build.py blue-v2    # should finish without errors
```

### Troubleshooting (macOS)

| Symptom | Fix |
|---------|-----|
| `ffmpeg` not found / startup error | `brew install ffmpeg`, restart venv |
| `Missing .venv` | Repeat step 4 |
| Wrong Python version | `pyenv local 3.10.19` in `main/xiaozhi-server`, recreate venv |
| LAN/ESP32 can't reach server | Firewall step 6; confirm `server.websocket` uses LAN IP |
| `OSError: [Errno 22] Invalid argument` (aiohttp) | Already patched in `app.py` for macOS — pull latest |
| Gemini / OpenAI errors | Check API keys and network; add proxy in config if blocked |
| `idf.py: command not found` | Run `source ~/esp/esp-idf/export.sh` (or `get_idf`) |
| `./install.sh` fails on Mac | Install step 2 brew deps; ensure Xcode CLT installed |
| USB port not found | Try another cable/port; `ls /dev/cu.usb*`; install [CP210x driver](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers) if using UART bridge |
| Firmware build OOM | Close other apps; first build needs ~8 GB RAM |
| QEMU fails | Use IDF 6.0.2; set `IDF_PYTHON_ENV_PATH` — see step 10.4 |

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
| TTS backend | VieNeu v3 (local, 8882) | **Kokoro-82M** (local, 8883) |
| TTS voice | `Thục Đoan` | `af_heart` (female US) |
| TTS normalize | Vietnamese spacing fixes | off |

**TTS routing is LLM-tag driven.** The LLM marks its reply language with a leading
tag, the server parses it, switches locale, and routes to the matching local backend:

```
[locale=en] Hello! How are you?   →  Kokoro (8883), af_heart
[locale=vi] Dạ, mình khỏe nha     →  VieNeu (8882), Thục Đoan
```

- Tag is **stripped before TTS** — the robot never speaks `[locale=...]`.
- The tag also drives ASR locale for the next turn (sticky per connection).
- Vietnamese replies need **no tag** (locale stays `vi` by default).

```yaml
language_runtime:
  default_locale: vi
  locales:
    vi:
      tts_voice: vieneu-v3-turbo
      tts_speeches_voice: Thục Đoan
    en:
      tts_voice: kokoro-v1.0
      tts_speeches_voice: af_heart
TTS:
  CustomTTS:
    url: "http://host.docker.internal:8882/v1/audio/speech"   # vi default
    locales:
      en:
        url: "http://host.docker.internal:8883/v1/audio/speech"  # Kokoro
        model: kokoro-v1.0
        voice: af_heart
```

Log: `[locale] LLM tag -> en (…label…)` on switch.

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

## Local TTS (separate Docker stack)

TTS runs in its own Compose project (`docker/tts/docker-compose.yml`), independent of xiaozhi-server. Start TTS first, then point the server at the host port via `CustomTTS` in `data/.config.yaml`.

| Engine | Script | Port | Use case |
|--------|--------|------|----------|
| VieNeu v3 Turbo | `./run-vieneu-tts.sh up` | 8882 | Best Vietnamese quality (CPU/ONNX) |
| Kokoro-82M | `./run-kokoro-tts.sh up` | 8883 | Best English quality on CPU (female US voices) |

Optional env: `cp docker/tts/.env.example docker/tts/.env`

**Server on host** (`./run.sh`): use `http://127.0.0.1:<port>/v1/audio/speech`

**Server in Docker** (`./run-docker.sh up`): use `http://host.docker.internal:<port>/v1/audio/speech`

### VieNeu-TTS (higher quality Vietnamese)

[VieNeu-TTS v3 Turbo](https://github.com/pnnbao97/VieNeu-TTS) runs **on CPU via ONNX** (no GPU required). Excellent Vietnamese prosody; first start downloads models (~few GB).

```bash
chmod +x run-vieneu-tts.sh
./run-vieneu-tts.sh up      # build + start on :8882 (may take several minutes first time)
./run-vieneu-tts.sh test    # → /tmp/blue-vieneu-vi.wav
./run-vieneu-tts.sh voices  # list preset voices (Ngọc Lan, Ngọc Linh, Trúc Ly, Mỹ Duyên, …)
```

In `main/xiaozhi-server/data/.config.yaml`:

```yaml
selected_module:
  TTS: CustomTTS

language_runtime:
  default_locale: vi
  locales:
    vi:
      tts_voice: vieneu-v3-turbo
      tts_speeches_voice: Ngọc Lan

TTS:
  CustomTTS:
    type: custom
    method: POST
    url: "http://127.0.0.1:8882/v1/audio/speech"
    default_voice: Ngọc Lan
    format: wav
    output_dir: tmp/
    params:
      input: "{prompt_text}"
      model: "{model}"
      voice: "{voice}"
      response_format: "wav"
      speed: 1.0
```

Override port/voice via `docker/tts/.env`: `VIENEU_TTS_PORT`, `VIENEU_DEFAULT_VOICE`.

For NVIDIA GPU hosts, upstream also supports LMDeploy v2 server (`pnnbao/vieneu-tts:serve`) — see [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS).

### Kokoro-82M (best English on CPU)

[Kokoro-82M](https://github.com/hexgrad/kokoro) is an 82M-param open-weight TTS
with excellent **English** quality that runs on CPU. It has no Vietnamese model,
so it is used only for `en` replies (VieNeu keeps `vi`). Runs via
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (ONNX, ~548 MB image).

```bash
chmod +x run-kokoro-tts.sh
./run-kokoro-tts.sh up       # build + start on :8883 (downloads model first time)
./run-kokoro-tts.sh test     # → /tmp/blue-kokoro-en.wav
./run-kokoro-tts.sh voices   # list female voices (af_heart, af_bella, af_nicole, …)
```

Female US voices: `af_heart` (default), `af_alloy`, `af_bella`, `af_jessica`,
`af_nicole`, `af_sarah`, `af_sky`, … Override with `KOKORO_DEFAULT_VOICE` env
(or the `en.voice` block in `data/.config.yaml`).

In `main/xiaozhi-server/data/.config.yaml` (both Vi and En local):

```yaml
selected_module:
  TTS: CustomTTS

language_runtime:
  default_locale: vi
  locales:
    vi:
      tts_voice: vieneu-v3-turbo
      tts_speeches_voice: Thục Đoan
    en:
      tts_voice: kokoro-v1.0
      tts_speeches_voice: af_heart

TTS:
  CustomTTS:
    type: custom
    method: POST
    url: "http://127.0.0.1:8882/v1/audio/speech"      # vi → VieNeu
    default_voice: Thục Doan
    format: wav
    output_dir: tmp/
    params:
      input: "{prompt_text}"
      model: "{model}"
      voice: "{voice}"
      response_format: "wav"
      speed: 1.0
    locales:
      en:
        url: "http://127.0.0.1:8883/v1/audio/speech"  # en → Kokoro
        model: kokoro-v1.0
        voice: af_heart
        format: wav
        params:
          input: "{prompt_text}"
          model: "kokoro-v1.0"
          voice: "{voice}"
          response_format: "wav"
          speed: 1.0
```

The server routes `vi` → `url` (VieNeu) and `en` → `locales.en.url` (Kokoro).
The LLM marks reply language with a leading `[locale=en]` tag (see
[Language runtime](#language-runtime-vi--en)).

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

Run Blue firmware in ESP-IDF QEMU without hardware — install [ESP-IDF (step 10)](#101-install-esp-idf-602) first.

- [esp32-blue/main/boards/blue-v2/QEMU.md](../esp32-blue/main/boards/blue-v2/QEMU.md)
- [esp32-blue/main/boards/blue-v1/QEMU.md](../esp32-blue/main/boards/blue-v1/QEMU.md)

```bash
cd esp32-blue
export IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf6.0_py3.12_env
source ~/esp/esp-idf/export.sh
python scripts/build.py blue-v2
idf.py qemu
```

Pair with `./run.sh` on [esp32-server-blue](./BLUE.md) (WebSocket on your LAN).

## Upstream

Based on [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server). Docker and manager-api docs in [README.md](./README.md).
