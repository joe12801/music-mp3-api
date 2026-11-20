# syntax=docker/dockerfile:1
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Music MP3 API" \
      org.opencontainers.image.description="Self-hosted yt-dlp + ffmpeg MP3 API with local cache, synced lyrics and a demo player" \
      org.opencontainers.image.source="https://github.com/joe12801/music-mp3-api" \
      org.opencontainers.image.documentation="https://github.com/joe12801/music-mp3-api/blob/main/docs/DOCKER.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_HOST=0.0.0.0 \
    MUSIC_PORT=8000 \
    MUSIC_CACHE_DIR=/app/data/cache

WORKDIR /app

# ffmpeg（libmp3lame）用于转 MP3；Node.js 是 yt-dlp-ejs 求解 YouTube JS challenge 的必需运行时。
# 两者都内置，宿主机不需要安装任何依赖。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates \
    && node -e "const v=parseInt(process.versions.node.split('.')[0]); if(v<18){console.error('Node.js >=18 required, got '+process.versions.node);process.exit(1)} console.log('node '+process.versions.node)" \
    && ffmpeg -version | head -1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY run.py ./

# 以非 root 用户运行；数字 UID 无需系统里存在该用户。
# 缓存 / 歌词目录可写，配合 VOLUME 或 bind mount 持久化。
RUN mkdir -p /app/data/cache /app/data/lyrics /app/downloads \
    && chown -R 10001:10001 /app
USER 10001:10001

VOLUME ["/app/data/cache", "/app/data/lyrics"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.getenv('MUSIC_PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-u", "run.py"]
