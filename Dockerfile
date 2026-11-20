FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_HOST=0.0.0.0 \
    MUSIC_PORT=8000 \
    MUSIC_CACHE_DIR=/app/data/cache

WORKDIR /app

# ffmpeg（含 libmp3lame）用于把音频流转码为 MP3
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY run.py ./
RUN mkdir -p /app/data/cache

VOLUME ["/app/data/cache"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
