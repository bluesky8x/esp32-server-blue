# VieNeu-TTS v3 Turbo (CPU/ONNX) — OpenAI-compatible API for esp32-server-blue
#
# Source: https://github.com/pnnbao97/VieNeu-TTS
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/root/.cache/huggingface \
    VIENEU_HOST=0.0.0.0 \
    VIENEU_PORT=8882

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /opt/vieneu-tts

ARG VIENEU_REPO=https://github.com/pnnbao97/VieNeu-TTS.git
ARG VIENEU_REF=main
RUN git clone --depth 1 --branch "${VIENEU_REF}" "${VIENEU_REPO}" .

# Torch-free ONNX stack (v3 Turbo CPU int8)
RUN uv sync

COPY openai_server.py /opt/vieneu-tts/openai_server.py

EXPOSE 8882

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:8882/health" || exit 1

CMD ["uv", "run", "python", "openai_server.py"]
