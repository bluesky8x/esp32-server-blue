#!/usr/bin/env bash
# Self-hosted local speaker recognition (3D-Speaker) — build & run.
# Fully local/offline after the model is downloaded once from ModelScope.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

ENV_FILE="$ROOT/docker/.env"

compose() {
  local args=(docker compose -f "$ROOT/docker-compose.yml")
  if [[ -f "$ENV_FILE" ]]; then
    args+=(--env-file "$ENV_FILE")
  fi
  "${args[@]}" "$@"
}

cmd="${1:-help}"

case "$cmd" in
  up)
    compose up -d --build voiceprint-local
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    key="${VOICEPRINT_KEY:-abcd}"
    echo ""
    echo "voiceprint-local started."
    echo "Health: http://${ip:-<lan-ip>}:${VOICEPRINT_PORT:-8005}/voiceprint/health?key=${key}"
    echo "In data/.config.yaml set:"
    echo "  voiceprint:"
    echo "    url: http://${ip:-<lan-ip>}:${VOICEPRINT_PORT:-8005}/voiceprint/health?key=${key}"
    echo "    similarity_threshold: 0.55"
    echo "Logs:  $0 logs"
    ;;
  down)
    compose down voiceprint-local
    ;;
  restart)
    compose restart voiceprint-local
    ;;
  logs)
    compose logs -f --tail=100 voiceprint-local
    ;;
  ps)
    compose ps voiceprint-local
    ;;
  rebuild)
    compose build --no-cache voiceprint-local
    ;;
  shell)
    compose exec voiceprint-local bash
    ;;
  help|*)
    cat <<EOF
Usage: $0 {up|down|restart|logs|ps|rebuild|shell}

  up       Build and start the local voiceprint service (3D-Speaker, port 8005)
  down     Stop it
  logs     Follow logs
  ps       Show status
  rebuild  Rebuild from scratch (re-downloads deps)
EOF
    ;;
esac
