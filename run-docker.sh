#!/usr/bin/env bash
# Run Blue xiaozhi-server on Ubuntu/Linux via Docker Compose.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

CONFIG="main/xiaozhi-server/data/.config.yaml"
EXAMPLE="main/xiaozhi-server/data/.config.yaml.example"
ENV_FILE="$ROOT/docker/.env"

compose() {
  local args=(docker compose -f "$ROOT/docker-compose.yml")
  if [[ -f "$ENV_FILE" ]]; then
    args+=(--env-file "$ENV_FILE")
  fi
  "${args[@]}" "$@"
}

ensure_config() {
  mkdir -p main/xiaozhi-server/data main/xiaozhi-server/tmp
  if [[ ! -f "$CONFIG" ]]; then
    if [[ -f "$EXAMPLE" ]]; then
      cp "$EXAMPLE" "$CONFIG"
      echo "Created $CONFIG from example."
    else
      echo "Missing $CONFIG — create it before starting the server."
      exit 1
    fi
  fi
  echo "Config: $CONFIG"
  echo "Set server.websocket to ws://<ubuntu-lan-ip>:8000/xiaozhi/v1/"
}

lan_ip_hint() {
  if command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{print $1}' || true
  fi
}

cmd="${1:-help}"

case "$cmd" in
  up)
    ensure_config
    compose up -d --build
    ip="$(lan_ip_hint)"
    echo ""
    echo "Server started."
    [[ -n "$ip" ]] && echo "OTA test:  curl http://${ip}:8003/xiaozhi/ota/"
    echo "Logs:      $0 logs"
    ;;
  down)
    compose down
    ;;
  restart)
    compose restart xiaozhi-server
    ;;
  logs)
    compose logs -f --tail=100 xiaozhi-server
    ;;
  ps)
    compose ps -a
    ;;
  build)
    compose build xiaozhi-server
    ;;
  shell)
    compose exec xiaozhi-server bash
    ;;
  help|*)
    cat <<EOF
Usage: $0 {up|down|restart|logs|ps|build|shell}

  up       Build and start xiaozhi-server (ports 8000, 8003)
  down     Stop server container

Local TTS runs separately:
  ./run-vieneu-tts.sh up   # VieNeu on \${VIENEU_TTS_PORT:-8882}
  ./run-kokoro-tts.sh up   # Kokoro (English) on \${KOKORO_TTS_PORT:-8883}
  Point data/.config.yaml CustomTTS url at 127.0.0.1 or host.docker.internal
  restart  Restart xiaozhi-server
  logs     Follow server logs
  ps       Show container status
  build    Rebuild server image only
  shell    Shell into running server container

Before first run, edit main/xiaozhi-server/data/.config.yaml:
  server.websocket: ws://<ubuntu-lan-ip>:8000/xiaozhi/v1/
  LLM/ASR API keys

Optional env file: cp docker/.env.example docker/.env
EOF
    ;;
esac
