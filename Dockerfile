FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg + ffprobe (the actual render engine) and fonts for the ASS
# captions (DejaVu Sans Bold 76, v3 model).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY .env.example .env.example

# Whisper model cache + job storage. Keep them on a volume so a
# redeploy does not re-download the model or lose finished Shorts.
VOLUME ["/app/jobs", "/root/.cache/huggingface"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
