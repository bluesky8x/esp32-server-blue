#!/usr/bin/env bash
# Local Kokoro-82M TTS (English female voices) for esp32-server-blue — CPU/ONNX.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$ROOT/docker/tts/docker-compose.yml"
ENV_FILE="$ROOT/docker/tts/.env"

COMPOSE=(docker compose -f "$COMPOSE_FILE" --profile kokoro)
[[ -f "$ENV_FILE" ]] && COMPOSE+=(--env-file "$ENV_FILE")

PORT="${KOKORO_TTS_PORT:-8883}"
BASE="http://127.0.0.1:${PORT}"
VOICE="${KOKORO_DEFAULT_VOICE:-af_heart}"

cmd="${1:-help}"

wait_healthy() {
  echo "Waiting for Kokoro-TTS health at $BASE (first start downloads models)..."
  for _ in $(seq 1 90); do
    if curl -sf "$BASE/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  echo "Kokoro-TTS not healthy — check logs: $0 logs"
  return 1
}

case "$cmd" in
  up)
    "${COMPOSE[@]}" up -d --build kokoro-tts
    echo "Kokoro-TTS API: $BASE/v1/audio/speech"
    echo "Voices:         $BASE/voices"
    echo "Default voice:  $VOICE"
    echo "Next: $0 test"
    ;;
  down)
    "${COMPOSE[@]}" stop kokoro-tts
    ;;
  build)
    "${COMPOSE[@]}" build kokoro-tts
    ;;
  logs)
    "${COMPOSE[@]}" logs -f kokoro-tts
    ;;
  test)
    "${COMPOSE[@]}" up -d --build kokoro-tts
    wait_healthy
    curl -s -X POST "$BASE/v1/audio/speech" \
      -H "Content-Type: application/json" \
      -d "{\"input\":\"Hello! How are you today?\",\"voice\":\"$VOICE\",\"response_format\":\"wav\"}" \
      -o /tmp/blue-kokoro-en.wav
    echo "Wrote /tmp/blue-kokoro-en.wav"
    file /tmp/blue-kokoro-en.wav
    ;;
  voices)
    "${COMPOSE[@]}" up -d kokoro-tts
    wait_healthy
    curl -s "$BASE/voices" | python3 -m json.tool 2>/dev/null || curl -s "$BASE/voices"
    ;;
  *)
    echo "Usage: $0 {up|down|build|logs|test|voices}"
    exit 1
    ;;
esac
