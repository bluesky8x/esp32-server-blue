#!/usr/bin/env bash
# Local Piper TTS (Speaches) for esp32-server-blue — low-latency vi/en voices.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
TTS_DIR="$ROOT/main/xiaozhi-server/docker/tts"
COMPOSE="docker compose -f $TTS_DIR/docker-compose.yml"
PORT="${TTS_HOST_PORT:-8881}"
BASE="http://127.0.0.1:${PORT}"

# Piper models (HuggingFace IDs via speaches registry)
VI_MODEL="${TTS_VI_MODEL:-speaches-ai/piper-vi_VN-vais1000-medium}"
EN_MODEL="${TTS_EN_MODEL:-speaches-ai/piper-en_US-lessac-high}"
VI_VOICE="${TTS_VI_VOICE:-vais1000}"
EN_VOICE="${TTS_EN_VOICE:-lessac}"

cmd="${1:-help}"

download_models() {
  echo "Waiting for Speaches health at $BASE ..."
  for _ in $(seq 1 60); do
    if curl -sf "$BASE/health" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  if ! curl -sf "$BASE/health" >/dev/null 2>&1; then
    echo "Speaches not healthy — run: $0 up"
    exit 1
  fi

  echo "Downloading Vietnamese Piper model: $VI_MODEL"
  $COMPOSE exec -T speaches uv tool run speaches-cli model download "$VI_MODEL"

  echo "Downloading English Piper model: $EN_MODEL"
  $COMPOSE exec -T speaches uv tool run speaches-cli model download "$EN_MODEL"

  echo "Installed TTS models:"
  $COMPOSE exec -T speaches uv tool run speaches-cli model ls --task text-to-speech || true
}

case "$cmd" in
  up)
    $COMPOSE up -d
    echo "Speaches TTS: $BASE/v1/audio/speech"
    echo "Next: $0 setup   # download vi/en Piper models"
    ;;
  down)
    $COMPOSE down
    ;;
  setup)
    $COMPOSE up -d
    download_models
    echo ""
    echo "Done. Test with: $0 test"
    ;;
  logs)
    $COMPOSE logs -f --tail=100
    ;;
  test)
    echo "VI test → /tmp/blue-tts-vi.wav"
    curl -sf "$BASE/v1/audio/speech" \
      -H "Content-Type: application/json" \
      -d "{\"input\":\"Xin chào, mình là Kira.\",\"model\":\"$VI_MODEL\",\"voice\":\"$VI_VOICE\",\"response_format\":\"wav\",\"speed\":0.92}" \
      -o /tmp/blue-tts-vi.wav
    echo "EN test → /tmp/blue-tts-en.wav"
    curl -sf "$BASE/v1/audio/speech" \
      -H "Content-Type: application/json" \
      -d "{\"input\":\"Hello, I am Kira.\",\"model\":\"$EN_MODEL\",\"voice\":\"$EN_VOICE\",\"response_format\":\"wav\",\"speed\":0.92}" \
      -o /tmp/blue-tts-en.wav
    ls -lh /tmp/blue-tts-vi.wav /tmp/blue-tts-en.wav
    echo "OK"
    ;;
  help|*)
    cat <<EOF
Usage: $0 {up|down|setup|test|logs}

  up     Start Speaches (Piper) on port $PORT
  setup  Start + download Vietnamese + English Piper models
  test   Synthesize sample vi/en WAV files
  down   Stop container
  logs   Follow container logs

Configure xiaozhi-server data/.config.yaml:
  selected_module.TTS: CustomTTS
  See data/.config.yaml.example section "Local Piper TTS (Speaches)"
EOF
    ;;
esac
