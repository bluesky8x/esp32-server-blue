#!/usr/bin/env bash
# Run xiaozhi-server on Mac (after setup once)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/main/xiaozhi-server"

if [[ ! -f data/.config.yaml ]]; then
  echo "Missing data/.config.yaml — copy from data/.config.yaml.example"
  exit 1
fi

selected_asr="$(grep -E '^\s+ASR:' data/.config.yaml | head -1 | sed -E 's/.*ASR:[[:space:]]*//')"
if [[ "${selected_asr}" == "FunASR" ]] && [[ ! -f models/SenseVoiceSmall/model.pt ]]; then
  echo "ASR model missing: models/SenseVoiceSmall/model.pt"
  echo "Download: https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt"
  echo "(Or set selected_module.ASR to OpenaiASR in data/.config.yaml to skip local ASR.)"
  exit 1
fi

# Prefer pyenv Python 3.10.x (see .python-version)
if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init -)"
fi

if [[ -d .venv ]]; then
  source .venv/bin/activate
else
  echo "Missing .venv — run: pyenv local 3.10.19 && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

exec python app.py
