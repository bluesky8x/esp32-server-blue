# Build Blue stack on MacBook Pro 2019 (Intel)

End-to-end guide for **local development** on an **Intel Mac** (x86_64):

| Repo | What you build |
|------|----------------|
| [esp32-server-blue](../) | Python Xiaozhi server (Kira / Lili, Gemini, robot motion) |
| [esp32-blue](../../esp32-blue/) | ESP32-S3 firmware (`blue-v2` or `blue-v1`) |

Tested target: **MacBook Pro 2019** (13" or 16", Intel Core i5/i7/i9).

General macOS notes also live in [BLUE.md § macOS install](../BLUE.md#macos--install-dependencies-local-build). This doc adds **Intel-specific** steps and expectations.

---

## Before you start

### Supported hardware / OS

| | Minimum | Recommended |
|---|---------|-------------|
| Mac | MacBook Pro **2019** (Intel) | 16 GB RAM model |
| Architecture | **x86_64** (`uname -m`) | not Apple Silicon |
| macOS | **12 Monterey** | 13 Ventura or 14 Sonoma |
| Free disk | **25 GB** | 40 GB+ (IDF toolchains + PyTorch + builds) |
| RAM | **8 GB** (server only) | **16 GB** (server + firmware builds) |

Confirm Intel:

```bash
uname -m          # must print: x86_64
sysctl -n machdep.cpu.brand_string
# e.g. Intel Core i7-9750H ...
```

### What gets installed where

| Tool | Location | Used for |
|------|----------|----------|
| Homebrew | `/usr/local` on Intel | ffmpeg, pyenv, cmake, … |
| Server Python **3.10.19** | pyenv + `main/xiaozhi-server/.venv` | `./run.sh` |
| ESP-IDF Python **3.12** | `~/.espressif/python_env/` | `idf.py`, firmware build |
| ESP-IDF | `~/esp/esp-idf` | Blue firmware |

Use **separate terminal tabs** for server vs firmware, or `deactivate` the server venv before sourcing `export.sh`.

---

## 1. Xcode Command Line Tools

```bash
xcode-select --install
xcode-select -p    # should print a path under /Library/Developer/
```

---

## 2. Homebrew (Intel)

Install from [https://brew.sh](https://brew.sh) if needed. On Intel Macs, Homebrew uses **`/usr/local`**.

After install, ensure shell finds it:

```bash
echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zshrc
eval "$(/usr/local/bin/brew shellenv)"
brew --version
```

Install dependencies:

```bash
brew update
brew install git pyenv ffmpeg opus cmake ninja dfu-util ccache \
  openssl@3 readline xz zlib bzip2
```

`openssl@3` and friends help **pyenv compile Python 3.10.19** on Intel without errors.

Verify:

```bash
ffmpeg -version
which brew    # /usr/local/bin/brew
```

---

## 3. Server — Python 3.10.19 (pyenv)

Add pyenv to `~/.zshrc`:

```bash
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
```

Reload: `source ~/.zshrc`

### Install Python (Intel hint)

If `pyenv install 3.10.19` fails linking OpenSSL, use:

```bash
export LDFLAGS="-L$(brew --prefix openssl@3)/lib"
export CPPFLAGS="-I$(brew --prefix openssl@3)/include"
export PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig"
pyenv install 3.10.19
```

Then:

```bash
cd ~/work/esp32-server-blue/main/xiaozhi-server   # adjust path
pyenv local 3.10.19
python --version    # Python 3.10.19
```

### Virtualenv + pip

```bash
cd ~/work/esp32-server-blue/main/xiaozhi-server
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

On Intel MacBook Pro 2019, first install often takes **15–30 minutes** (PyTorch CPU x86_64 wheel + deps). The machine may run warm — that is normal.

---

## 4. Server — config

```bash
mkdir -p data tmp
cp data/.config.yaml.example data/.config.yaml
```

Edit `data/.config.yaml`:

```yaml
server:
  websocket: ws://192.168.x.x:8000/xiaozhi/v1/
  vision_explain: http://192.168.x.x:8003/mcp/vision/explain
  timezone_offset: +7

LLM:
  GeminiLLM:
    api_key: YOUR_GEMINI_API_KEY

ASR:
  OpenaiASR:
    api_key: YOUR_OPENAI_API_KEY
```

LAN IP (Wi‑Fi):

```bash
ipconfig getifaddr en0
# if empty, try en1 or: ifconfig | grep "inet " | grep -v 127.0.0.1
```

---

## 5. Server — macOS Firewall

Intel Macs use the same application firewall. **pyenv Python** must accept LAN connections or the ESP32 cannot reach OTA/WebSocket.

**System Settings → Network → Firewall → Options** → **python3.10** → **Allow incoming connections**

Path: `~/.pyenv/versions/3.10.19/bin/python3.10`

```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp \
  "$HOME/.pyenv/versions/3.10.19/bin/python3.10"
```

Test:

```bash
cd ~/work/esp32-server-blue
./run.sh
# other terminal:
curl -s http://127.0.0.1:8003/xiaozhi/ota/ | head
curl -s "http://$(ipconfig getifaddr en0):8003/xiaozhi/ota/" | head
```

Both should return HTTP content (not empty / connection reset).

---

## 6. Firmware — ESP-IDF 6.0.2

Official reference: [ESP-IDF macOS setup (v6.0.2)](https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32/get-started/macos-setup.html)

```bash
mkdir -p ~/esp
cd ~/esp
git clone -b v6.0.2 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3
```

First run: **20–40 minutes** on MBP 2019 Intel (downloads x86_64 toolchains to `~/.espressif/`).

Add to `~/.zshrc`:

```bash
get_idf() {
  export IDF_PYTHON_ENV_PATH="$HOME/.espressif/python_env/idf6.0_py3.12_env"
  . "$HOME/esp/esp-idf/export.sh"
}
```

Verify:

```bash
get_idf
idf.py --version
python --version    # 3.12.x from ~/.espressif/
```

> **`idf6.0_py3.14_env not found`:** default `python3` is 3.14; your IDF venv is **3.12**. Use `get_idf` (sets `IDF_PYTHON_ENV_PATH`) — see [Troubleshooting § Python 3.14](#troubleshooting-intel-macbook-pro-2019).

---

## 7. Firmware — build Blue

```bash
cd ~/work/esp32-blue
source ~/esp/esp-idf/export.sh

python scripts/build.py blue-v2    # new PCB — recommended
# python scripts/build.py blue-v1  # legacy PCB
```

First firmware build: **20–45 minutes** on Intel (component download + compile). Close Chrome/heavy apps if you only have 8 GB RAM.

Rebuilds are much faster with `ccache` (installed via Homebrew).

---

## 8. Firmware — flash (USB)

MacBook Pro 2019 has **USB-C ports only**. Use:

- **USB-C ↔ USB-C** cable to ESP32-S3 native USB, or  
- **USB-C hub/dongle** with USB-A if your dev board uses a UART bridge (CP2102/CH340)

Find port:

```bash
ls /dev/cu.usbmodem* /dev/cu.usbserial* 2>/dev/null
```

Flash + serial monitor:

```bash
source ~/esp/esp-idf/export.sh
cd ~/work/esp32-blue
idf.py -p /dev/cu.usbmodem1101 flash monitor    # use your port name
```

On device (WiFi portal → **Advanced**), set OTA URL:

```text
http://<your-mac-lan-ip>:8003/xiaozhi/ota/
```

Board wiring: [Blue V2 WIRING.md](../../esp32-blue/main/boards/blue-v2/WIRING.md)

---

## 9. Run the full stack (daily workflow)

**Terminal A — server**

```bash
cd ~/work/esp32-server-blue
./run.sh
```

**Terminal B — firmware** (only when rebuilding/flashing)

```bash
source ~/esp/esp-idf/export.sh
cd ~/work/esp32-blue
idf.py -p /dev/cu.usbmodem1101 flash monitor
```

**Checklist**

| Step | Check |
|------|--------|
| Server up | `curl http://<lan-ip>:8003/xiaozhi/ota/` → OK |
| ESP32 WiFi | Connected to same LAN as Mac |
| OTA URL on device | Points to Mac LAN IP, port **8003** |
| Firewall | pyenv Python allowed |
| Wake / talk | Server log shows WebSocket connect |

---

## 10. Optional — Docker Piper TTS (Intel)

[Docker Desktop for Mac (Intel chip)](https://docs.docker.com/desktop/setup/install/mac-install/) works on x86_64.

```bash
cd ~/work/esp32-server-blue
chmod +x run-tts.sh
./run-tts.sh setup
```

In `data/.config.yaml`, set CustomTTS URL to `http://127.0.0.1:8881/v1/audio/speech` (host Docker, not compose network name).

See [BLUE.md § Local TTS](../BLUE.md#local-tts-piper--speaches--low-latency).

---

## 11. Optional — QEMU (no hardware)

Requires ESP-IDF 6.x. Full details: [esp32-blue/main/boards/blue-v2/QEMU.md](../../esp32-blue/main/boards/blue-v2/QEMU.md)

```bash
cd ~/work/esp32-blue
export IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf6.0_py3.12_env
source ~/esp/esp-idf/export.sh
python scripts/build.py blue-v2
idf.py qemu
```

If env name differs: `ls ~/.espressif/python_env/`

QEMU on Intel Mac is **slower than Apple Silicon** but fine for protocol testing against `./run.sh`.

---

## Troubleshooting (Intel MacBook Pro 2019)

| Problem | What to do |
|---------|------------|
| `pyenv install 3.10.19` fails (openssl/ssl) | Install `openssl@3 readline xz zlib`; use `LDFLAGS`/`CPPFLAGS` in §3 |
| `brew: command not found` | Run `eval "$(/usr/local/bin/brew shellenv)"` |
| Wrong arch packages | Confirm `uname -m` is `x86_64`; don't use Rosetta Terminal for builds |
| Server LAN fails, localhost OK | Firewall §5 — unblock pyenv `python3.10` |
| `idf.py: command not found` | Run `get_idf` (not bare `source export.sh`) |
| `idf6.0_py3.14_env not found` | Shell uses Python **3.14**; IDF 6.0.2 needs **3.12** — see below |
| USB port missing | Different cable/port; hold BOOT while plug-in; check `system_profiler SPUSBDataType` |
| Build killed / frozen | 8 GB RAM — quit browsers; build server and firmware **one at a time** |
| Very slow compiles | Expected on 2019 Intel; use `ccache`; plug in power adapter |
| ESP32 still hits `xiaozhi.me` | OTA URL on device must be your Mac IP `:8003`, not default cloud |
| `OSError: [Errno 22]` (aiohttp) | Fixed in current `app.py` for macOS — pull latest esp32-server-blue |

---

## Performance expectations (2019 Intel vs Apple Silicon)

| Task | MBP 2019 Intel (typical) | Apple Silicon (reference) |
|------|--------------------------|---------------------------|
| `pip install -r requirements.txt` | 15–30 min | 5–15 min |
| First `scripts/build.py blue-v2` | 20–45 min | 10–25 min |
| Incremental firmware rebuild | 2–8 min | 1–4 min |
| `./run.sh` runtime | Fine for dev | Similar |

Intel is fully supported; builds are just slower and RAM is tighter.

### Python 3.14 vs ESP-IDF 6.0.2

If `source ~/esp/esp-idf/export.sh` prints:

```text
ERROR: ESP-IDF Python virtual environment ".../idf6.0_py3.14_env" not found
```

your default `python3` is **3.14** (Homebrew/pyenv), but `./install.sh` created **`idf6.0_py3.12_env`**. ESP-IDF 6.0.2 does not support 3.14 yet.

**Fix (you already have the 3.12 venv):**

```bash
export IDF_PYTHON_ENV_PATH=~/.espressif/python_env/idf6.0_py3.12_env
source ~/esp/esp-idf/export.sh
```

Or use the `get_idf` function from §6. Confirm: `python --version` → **3.12.x**.

Before firmware work, **deactivate** the server venv so it does not shadow tools:

```bash
deactivate   # if (server) venv is active
get_idf
```

---

## Quick reference

```bash
# Server (from repo root)
cd ~/work/esp32-server-blue && ./run.sh

# Firmware build
source ~/esp/esp-idf/export.sh && cd ~/work/esp32-blue && python scripts/build.py blue-v2

# Flash
idf.py -p /dev/cu.usbmodem* flash monitor

# LAN IP
ipconfig getifaddr en0
```

Related docs:

- [BLUE.md](../BLUE.md) — Blue stack overview, Gemini, motor tags, language runtime  
- [esp32-blue/README.md](../../esp32-blue/README.md) — firmware board profiles  
- [Deployment.md](./Deployment.md) — upstream server deployment  
