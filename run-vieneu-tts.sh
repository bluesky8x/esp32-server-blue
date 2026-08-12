#!/usr/bin/env bash
# Local VieNeu-TTS (pnnbao97/VieNeu-TTS) for esp32-server-blue — v3 Turbo CPU/ONNX.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$ROOT/docker/tts/docker-compose.yml"
ENV_FILE="$ROOT/docker/tts/.env"

COMPOSE=(docker compose -f "$COMPOSE_FILE" --profile vieneu)
[[ -f "$ENV_FILE" ]] && COMPOSE+=(--env-file "$ENV_FILE")

PORT="${VIENEU_TTS_PORT:-8882}"
BASE="http://127.0.0.1:${PORT}"
DEFAULT_VOICE="${VIENEU_DEFAULT_VOICE:-Ngọc Lan}"

cmd="${1:-help}"

wait_healthy() {
  echo "Waiting for VieNeu-TTS health at $BASE (first start may download models)..."
  for _ in $(seq 1 120); do
    if curl -sf "$BASE/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  echo "VieNeu-TTS not healthy — check logs: $0 logs"
  return 1
}

case "$cmd" in
  up)
    "${COMPOSE[@]}" up -d --build vieneu-tts
    echo "VieNeu-TTS API: $BASE/v1/audio/speech"
    echo "Voices:       $BASE/voices"
    echo "Next: $0 test"
    ;;
  down)
    "${COMPOSE[@]}" stop vieneu-tts
    ;;
  build)
    "${COMPOSE[@]}" build vieneu-tts
    ;;
  logs)
    "${COMPOSE[@]}" logs -f --tail=100 vieneu-tts
    ;;
  voices)
    wait_healthy
    curl -sf "$BASE/voices" | python3 -m json.tool
    ;;
  test)
    wait_healthy
    echo "VI test → /tmp/blue-vieneu-vi.wav (voice: $DEFAULT_VOICE)"
    curl -sf "$BASE/v1/audio/speech" \
      -H "Content-Type: application/json" \
      -d "{\"input\":\"Xin chào, mình là Kira.\",\"model\":\"vieneu-v3-turbo\",\"voice\":\"$DEFAULT_VOICE\",\"response_format\":\"wav\"}" \
      -o /tmp/blue-vieneu-vi.wav
    ls -lh /tmp/blue-vieneu-vi.wav
    echo "OK"
    ;;
  help|*)
    cat <<EOF
Usage: $0 {up|down|build|test|voices|logs}

  up      Build and start VieNeu-TTS on port $PORT
  test    Synthesize sample Vietnamese WAV
  voices  List preset voices
  down    Stop container
  logs    Follow container logs
  build   Rebuild image only

Configure xiaozhi-server data/.config.yaml:
  selected_module.TTS: CustomTTS
  TTS.CustomTTS.url: "http://127.0.0.1:$PORT/v1/audio/speech"
  # server in Docker: http://host.docker.internal:$PORT/v1/audio/speech
  language_runtime.locales.vi.tts_speeches_voice: "$DEFAULT_VOICE"

Env overrides (docker/tts/.env):
  VIENEU_TTS_PORT=$PORT
  VIENEU_DEFAULT_VOICE=$DEFAULT_VOICE
EOF
    ;;
esac
