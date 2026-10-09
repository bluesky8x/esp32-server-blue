#!/usr/bin/env bash
# Local VieNeu-TTS (pnnbao97/VieNeu-TTS) for esp32-server-blue — v3 Turbo CPU/ONNX.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$ROOT/docker/tts/docker-compose.yml"
ENV_FILE="$ROOT/docker/tts/.env"

COMPOSE=(docker compose -f "$COMPOSE_FILE" --profile vieneu)
if [[ -f "$ENV_FILE" ]]; then
  COMPOSE+=(--env-file "$ENV_FILE")
fi

SERVICE="vieneu-tts"
IMAGE="blue-vieneu-tts:latest"

PORT="${VIENEU_TTS_PORT:-8882}"
BASE="http://127.0.0.1:${PORT}"
DEFAULT_VOICE="${VIENEU_DEFAULT_VOICE:-Ngọc Lan}"

cmd="${1:-help}"

image_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

# Start container. `up` KHÔNG build lại mỗi lần: chỉ build khi chưa có image
# (`docker compose up` không tự build trừ khi thiếu image). Muốn ép build lại
# dùng: $0 build  (giữ cache) hoặc $0 rebuild (xoá image, clone lại repo).
start_service() {
  if image_exists; then
    echo "Reusing image $IMAGE (no rebuild). Changed code/config? Run: $0 build"
    "${COMPOSE[@]}" up -d "$SERVICE"
  else
    echo "Image $IMAGE not found — first build, this takes a few minutes..."
    "${COMPOSE[@]}" up -d --build "$SERVICE"
  fi
}

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
    start_service
    echo "VieNeu-TTS API: $BASE/v1/audio/speech"
    echo "Voices:       $BASE/voices"
    echo "Next: $0 test"
    ;;
  down)
    "${COMPOSE[@]}" stop "$SERVICE"
    ;;
  build)
    "${COMPOSE[@]}" build "$SERVICE"
    ;;
  rebuild)
    echo "Full rebuild (no cache) — removes old image, re-clones VieNeu repo..."
    "${COMPOSE[@]}" stop "$SERVICE" 2>/dev/null || true
    docker rm -f blue-vieneu-tts 2>/dev/null || true
    docker rmi "$IMAGE" 2>/dev/null || true
    "${COMPOSE[@]}" build --no-cache "$SERVICE"
    "${COMPOSE[@]}" up -d "$SERVICE"
    echo "Wait for health, then: $0 info && $0 voices"
    ;;
  info)
    wait_healthy
    curl -sf "$BASE/info" | python3 -m json.tool
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
Usage: $0 {up|down|build|rebuild|test|voices|info|logs}

  up       Start VieNeu-TTS on port $PORT (builds ONLY the first time — reuses
           the existing image afterwards; use `build`/`rebuild` to force)
  rebuild  Force clean rebuild (use when voices/repo still look old)
  info     Show repo/ref baked into running container
  test     Synthesize sample Vietnamese WAV
  voices   List preset voices
  down     Stop container
  logs     Follow container logs
  build    Rebuild image only (may reuse Docker cache)

Configure xiaozhi-server data/.config.yaml:
  selected_module.TTS: CustomTTS
  TTS.CustomTTS.url: "http://127.0.0.1:$PORT/v1/audio/speech"
  # server in Docker: http://host.docker.internal:$PORT/v1/audio/speech
  language_runtime.locales.vi.tts_speeches_voice: "$DEFAULT_VOICE"

Env overrides (docker/tts/.env):
  VIENEU_TTS_PORT=$PORT
  VIENEU_DEFAULT_VOICE=$DEFAULT_VOICE
  VIENEU_REPO=https://github.com/pnnbao97/VieNeu-TTS.git
  VIENEU_REF=main
  # If .env still points at xuanhieu fork, rebuild will keep old voices!
EOF
    ;;
esac
