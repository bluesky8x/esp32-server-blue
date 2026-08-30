# Kokoro-82M TTS (English, female voices) via kokoro-onnx — CPU-friendly ONNX.
# Serves an OpenAI-compatible /v1/audio/speech endpoint so xiaozhi-server's
# CustomTTS can route English replies here (VieNeu handles Vietnamese).
#
# Build:  docker build -t blue-kokoro-tts:latest docker/kokoro
# Run:    docker run -d -p 8883:8883 --name blue-kokoro-tts blue-kokoro-tts:latest

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        espeak-ng \
        curl \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Model + voices (v1.0 fp16 — spectral correlation 0.999 vs fp32, ~half size).
# Env-overridable so users can pick fp32/int8 without rebuilding the image.
ARG KOKORO_MODEL_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.fp16.onnx
ARG KOKORO_VOICES_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin

RUN mkdir -p /models \
    && wget -q -O /models/kokoro.onnx "${KOKORO_MODEL_URL}" \
    && wget -q -O /models/voices.bin "${KOKORO_VOICES_URL}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV KOKORO_MODEL=/models/kokoro.onnx \
    KOKORO_VOICES=/models/voices.bin \
    KOKORO_DEFAULT_VOICE=af_heart \
    KOKORO_SAMPLE_RATE=24000

EXPOSE 8883

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8883"]
