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
  samples)
    "${COMPOSE[@]}" up -d kokoro-tts
    wait_healthy
    OUT_DIR="${KOKORO_SAMPLES_DIR:-/tmp/kokoro-samples}"
    mkdir -p "$OUT_DIR"
    SAMPLE_TEXT="${KOKORO_SAMPLE_TEXT:-Hello! This is the Kokoro voice, saying a quick sample for you.}"
    echo "Generating samples for all female (af_*) voices → $OUT_DIR ..."
    # Fetch the voice list and filter female US voices (af_*).
    python3 - "$BASE" "$OUT_DIR" "$SAMPLE_TEXT" <<'PY'
import io, json, os, subprocess, sys, urllib.request

base, out_dir, sample = sys.argv[1], sys.argv[2], sys.argv[3]
voices = json.load(urllib.request.urlopen(f"{base}/voices"))["voices"]
female = sorted(v for v in voices if v.startswith("af_"))
if not female:
    female = voices  # fallback: all
print("Female voices:", ", ".join(female))
for i, voice in enumerate(female, 1):
    body = json.dumps({"input": f"{voice.replace('_', ' ')}, {sample}", "voice": voice, "response_format": "wav"}).encode()
    req = urllib.request.Request(f"{base}/v1/audio/speech", data=body, headers={"Content-Type": "application/json"})
    data = urllib.request.urlopen(req).read()
    path = os.path.join(out_dir, f"{i:02d}-{voice}.wav")
    open(path, "wb").write(data)
    print(f"  {path} ({len(data)/1024:.0f} KB)")
# Concatenate into one demo file with ffmpeg if available.
files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".wav"))
if files and subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0:
    concat = os.path.join(out_dir, "all-female-voices.wav")
    list_file = os.path.join(out_dir, ".concat.txt")
    with open(list_file, "w") as f:
        for p in files:
            f.write(f"file '{os.path.basename(p)}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, concat],
                   capture_output=True, cwd=out_dir)
    os.remove(list_file)
    print(f"Combined: {concat}")
PY
    echo "Done. Individual files + combined demo in $OUT_DIR"
    ;;
  *)
    echo "Usage: $0 {up|down|build|logs|test|voices|samples}"
    exit 1
    ;;
esac
