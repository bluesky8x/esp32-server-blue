#!/usr/bin/env bash
# Run xiaozhi-server on Mac (after setup once)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/main/xiaozhi-server"

if [[ ! -f data/.config.yaml ]]; then
  if [[ -f data/.config.yaml.example ]]; then
    cp data/.config.yaml.example data/.config.yaml
    echo "Created data/.config.yaml from example — edit API keys and server.websocket"
  else
    echo "Missing data/.config.yaml — copy from data/.config.yaml.example"
    exit 1
  fi
fi

selected_asr="$(python3 -c "
import yaml
with open('config.yaml') as f: base = yaml.safe_load(f) or {}
with open('data/.config.yaml') as f: over = yaml.safe_load(f) or {}
def merge(a,b):
    if not isinstance(a,dict) or not isinstance(b,dict): return b if b is not None else a
    out=dict(a)
    for k,v in b.items():
        out[k]=merge(out.get(k),v) if k in out and isinstance(out[k],dict) and isinstance(v,dict) else v
    return out
cfg=merge(base,over)
print((cfg.get('selected_module') or {}).get('ASR',''))
" 2>/dev/null || true)"
if [[ -z "${selected_asr}" ]]; then
  selected_asr="$(grep -E '^\s+ASR:' data/.config.yaml | head -1 | sed -E 's/.*ASR:[[:space:]]*//' || true)"
fi
if [[ -z "${selected_asr}" ]]; then
  selected_asr="$(grep -E '^\s+ASR:' config.yaml | head -1 | sed -E 's/.*ASR:[[:space:]]*//' || true)"
fi
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
