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

IMAGE="esp32-server-blue:latest"

# Code paths baked into the image by docker/Dockerfile (`COPY main/xiaozhi-server .`).
# Everything else (data/, tmp/, music/, models/) is already a runtime volume.
SYNC_PATHS=(
  app.py
  core
  config
  plugins_func
  config.yaml
  config_from_api.yaml
  mcp_server_settings.json
  agent-base-prompt.txt
)

image_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

container_id() {
  compose ps -q xiaozhi-server 2>/dev/null | head -n1
}

# Copy the current source into the running container — no rebuild, no new image layers.
sync_code() {
  local cid
  cid="$(container_id)"
  if [[ -z "$cid" ]]; then
    echo "Container is not running. Start it first: $0 up"
    exit 1
  fi

  local src="$ROOT/main/xiaozhi-server"
  local present=()
  for p in "${SYNC_PATHS[@]}"; do
    [[ -e "$src/$p" ]] && present+=("$p")
  done

  echo "Syncing ${#present[@]} path(s) -> /opt/xiaozhi-esp32-server (code only, no rebuild)"
  tar -cf - -C "$src" \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
    "${present[@]}" \
    | docker exec -i "$cid" tar -xf - -C /opt/xiaozhi-esp32-server

  echo "Restarting xiaozhi-server to reload the code..."
  compose restart xiaozhi-server
  echo "Done. Logs: $0 logs"
}

cmd="${1:-help}"

case "$cmd" in
  up)
    ensure_config
    if image_exists; then
      # Reuse the existing image: rebuilding on every start leaves stale <none> images.
      echo "Reusing image $IMAGE (no rebuild)."
      echo "Code changed? Run: $0 sync"
      compose up -d
    else
      echo "Image $IMAGE not found — first build, this takes a few minutes..."
      compose up -d --build
    fi
    ip="$(lan_ip_hint)"
    echo ""
    echo "Server started."
    [[ -n "$ip" ]] && echo "OTA test:  curl http://${ip}:8003/xiaozhi/ota/"
    echo "Logs:      $0 logs"
    ;;
  sync)
    ensure_config
    sync_code
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
  rebuild)
    compose build xiaozhi-server
    compose up -d
    ;;
  prune)
    docker image prune -f
    docker builder prune -f
    docker system df
    ;;
  shell)
    compose exec xiaozhi-server bash
    ;;
  help|*)
    cat <<EOF
Usage: $0 {up|sync|down|restart|logs|ps|build|rebuild|prune|shell}

  up       Start xiaozhi-server, reusing the existing image (no rebuild)
  sync     Copy the current source into the running container + restart
           (no rebuild, no new image layers — use this for code changes)
  down     Stop server container

Local TTS runs separately:
  ./run-vieneu-tts.sh up   # VieNeu on \${VIENEU_TTS_PORT:-8882}
  ./run-kokoro-tts.sh up   # Kokoro (English) on \${KOKORO_TTS_PORT:-8883}
  Point data/.config.yaml CustomTTS url at 127.0.0.1 or host.docker.internal
  restart  Restart xiaozhi-server
  logs     Follow server logs
  ps       Show container status
  build    Rebuild the image (only when requirements.txt / Dockerfile change)
  rebuild  Rebuild the image and restart
  prune    Drop dangling images + build cache (frees disk space)
  shell    Shell into running server container

Before first run, edit main/xiaozhi-server/data/.config.yaml:
  server.websocket: ws://<ubuntu-lan-ip>:8000/xiaozhi/v1/
  LLM/ASR API keys

Optional env file: cp docker/.env.example docker/.env
EOF
    ;;
esac
