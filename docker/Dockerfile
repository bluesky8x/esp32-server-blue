# Blue xiaozhi-server — production image (Ubuntu / Linux hosts)
FROM python:3.10-slim-bookworm

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libopus0 \
        locales \
    && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/xiaozhi-esp32-server

COPY main/xiaozhi-server/requirements.txt .

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && if [ -n "$PIP_INDEX_URL" ]; then \
         pip config set global.index-url "$PIP_INDEX_URL"; \
         if [ -n "$PIP_TRUSTED_HOST" ]; then \
           pip config set global.trusted-host "$PIP_TRUSTED_HOST"; \
         fi; \
       fi \
    && pip install --no-cache-dir -r requirements.txt --default-timeout=120 --retries 5

COPY main/xiaozhi-server .
COPY docker/seed_silero_vad.py /tmp/seed_silero_vad.py

# models/ excluded by .dockerignore — seed Silero VAD from pip silero_vad.
RUN mkdir -p data tmp models/SenseVoiceSmall \
    && python /tmp/seed_silero_vad.py \
    && rm /tmp/seed_silero_vad.py

EXPOSE 8000 8003

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8003/xiaozhi/ota/ >/dev/null || exit 1

CMD ["python", "app.py"]
