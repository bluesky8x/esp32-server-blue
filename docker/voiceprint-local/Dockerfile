# Self-hosted local speaker-verification service (3D-Speaker via ModelScope).
# CPU build — the pretrained model is downloaded ONCE from ModelScope on first
# run, then all inference is offline/local.
FROM python:3.10-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MODELSCOPE_CACHE=/app/.cache/modelscope

WORKDIR /app

# torch (CPU) — large but keeps the image self-contained without a GPU.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN mkdir -p /app/data

EXPOSE 8005

HEALTHCHECK --interval=60s --timeout=15s --start-period=600s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8005/voiceprint/health?key=%s'%__import__('os').environ.get('VOICEPRINT_KEY','abcd'), timeout=10).status==200 else 1)"

CMD ["python", "server.py"]
