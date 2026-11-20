"""Music MP3 API —— FastAPI 接口层。

调用链：请求 -> 输入校验(build_target) -> 缓存查询 -> yt-dlp 下载 -> ffmpeg 转 MP3 -> 写缓存 -> FileResponse
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .cache import CacheManager
from .config import ALLOWED_BITRATES, BASE_DIR, Settings, get_settings
from .downloader import (
    AudioResult,
    DependencyMissing,
    DownloadError,
    Downloader,
    FileTooLarge,
    MediaTooLong,
    probe,
    search_videos,
)
from .jobs import JobManager
from .lyrics import get_lyrics
from .security import InvalidTarget, Target, build_target, safe_filename

try:  # 可选依赖，用于读取 .env
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("music_api")

settings: Settings = get_settings()
cache = CacheManager(settings)
downloader = Downloader(settings, cache)


def _worker(target: Target, bitrate: int, progress_cb: Callable[[float, str], None]) -> AudioResult:
    return downloader.get_audio(target, bitrate, progress_cb)


jobs = JobManager(settings, _worker)

MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Music MP3 API 启动 | backend=%s | ffmpeg=%s | cache=%s",
        downloader.backend,
        downloader.has_ffmpeg,
        cache.dir,
    )
    yield
    jobs.shutdown()
    logger.info("Music MP3 API 已停止")


app = FastAPI(
    title="Music MP3 API",
    version=__version__,
    description="基于 yt-dlp + ffmpeg 的音乐音频接口：搜索 / 解析 / 下载 / 提取 MP3 / 本地缓存。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Cache",
        "X-Video-Id",
        "X-Audio-Bitrate",
        "X-Audio-Codec",
        "X-Audio-Size",
        "Content-Disposition",
        "Accept-Ranges",
    ],
)


# 只暴露 downloads（用户手动下载的音频），不暴露 data/ 等敏感目录
_DOWNLOADS_DIR = BASE_DIR / "downloads"
if _DOWNLOADS_DIR.is_dir():
    app.mount("/downloads", StaticFiles(directory=str(_DOWNLOADS_DIR)), name="downloads")


@app.middleware("http")
async def gate_downloads(request: Request, call_next):
    """公网部署时，静态下载目录同样要求 API Key（请求头或 ?api_key=）。

    /api/* 已由 require_api_key 逐个保护；这里补上 StaticFiles 挂载点，
    避免演示音频在启用鉴权后仍可被任意下载。
    """
    if settings.api_key and request.url.path.startswith("/downloads/"):
        supplied = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if supplied != settings.api_key:
            return JSONResponse(status_code=401, content={"detail": "无效或缺失的 API Key"})
    return await call_next(request)


# ---------------------------------------------------------------- 通用工具
async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(default=None, alias="api_key"),
) -> None:
    """设置了 MUSIC_API_KEY 时才生效；支持请求头或 query 参数（便于 <audio> 标签）。"""
    expected = settings.api_key
    if not expected:
        return
    if (x_api_key or api_key) != expected:
        raise HTTPException(status_code=401, detail="无效或缺失的 API Key")


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidTarget):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (MediaTooLong, FileTooLarge)):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, DependencyMissing):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, DownloadError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    logger.exception("未处理的服务异常")
    return HTTPException(status_code=500, detail=f"服务器内部错误：{exc}")


def safe_call(fn: Callable, *args, **kwargs):
    """把核心层异常统一映射为 HTTP 状态码。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise http_error(exc) from exc


def valid_bitrate(value: int | None) -> int:
    if value is None:
        return settings.mp3_bitrate
    if value not in ALLOWED_BITRATES:
        raise HTTPException(
            status_code=422,
            detail=f"bitrate 必须是 {ALLOWED_BITRATES} 之一",
        )
    return value


def make_target(
    url: str | None, q: str | None, video_id: str | None, search_limit: int = 1
) -> Target:
    return safe_call(
        build_target,
        url=url,
        q=q,
        video_id=video_id,
        search_limit=search_limit,
        settings=settings,
    )


def audio_response(result: AudioResult, *, force_download: bool = False) -> FileResponse:
    path = result.file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="缓存文件已丢失，请重新请求")
    ext = result.entry.ext
    media_type = MEDIA_TYPES.get(ext) or mimetypes.types_map.get(ext) or "audio/mpeg"
    title = result.meta.get("title") or result.meta.get("id") or "audio"
    filename = safe_filename(f"{title}{ext}", fallback=f"audio{ext}")
    headers = {
        "X-Cache": "HIT" if result.cache_hit else "MISS",
        "X-Video-Id": result.meta.get("id") or result.entry.video_id,
        "X-Audio-Bitrate": str(result.entry.bitrate),
        "X-Audio-Codec": result.codec,
        "X-Audio-Size": str(result.entry.size),
        "Cache-Control": "public, max-age=86400",
        "Accept-Ranges": "bytes",
    }
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if force_download else "inline",
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("请求 %s 出现未捕获异常", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "error": str(exc)},
    )


# ---------------------------------------------------------------- 页面与元信息
@app.get("/", include_in_schema=False)
def index():
    index_file = Path(__file__).resolve().parent.parent / "static" / "index.html"
    if settings.demo_ui and index_file.is_file():
        # 演示页每次请求都读盘，禁用浏览器缓存，避免改版后用户仍停留在旧页面
        return HTMLResponse(
            index_file.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )
    return {
        "service": "Music MP3 API",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health", tags=["meta"], summary="服务健康与依赖状态")
def health():
    stats = cache.stats()
    return {
        "status": "ok",
        "service": "Music MP3 API",
        "version": __version__,
        "download": downloader.health(),
        "cache": {
            "count": stats["count"],
            "total_mb": stats["total_mb"],
            "limit_mb": stats["limit_mb"],
            "ttl_seconds": stats["ttl_seconds"],
            "directory": stats["directory"],
        },
        "config": {
            "api_key_required": bool(settings.api_key),
            "default_bitrate": settings.mp3_bitrate,
            "allowed_bitrates": list(ALLOWED_BITRATES),
            "max_duration_sec": settings.max_duration_sec,
            "search_max_limit": settings.search_max_limit,
            "lyrics_ttl_days": settings.lyrics_ttl_days,
            "lyrics_external": settings.lyrics_external,
            "demo_ui": settings.demo_ui,
        },
        "jobs": {"tracked": len(jobs.list(limit=1000))},
    }


# ---------------------------------------------------------------- 搜索 / 解析
@app.get("/api/search", tags=["youtube"], summary="搜索 YouTube 视频（不下载）")
def api_search(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(8, ge=1, description="返回条数"),
    _: None = Depends(require_api_key),
):
    target = make_target(None, q, None, search_limit=min(limit, settings.search_max_limit))
    results = safe_call(search_videos, target, settings)
    return {"query": target.display, "count": len(results), "results": results}


@app.get("/api/resolve", tags=["youtube"], summary="解析单个视频元数据（不下载）")
def api_resolve(
    url: str | None = Query(default=None, description="YouTube 链接"),
    q: str | None = Query(default=None, description="搜索关键词，取第一条"),
    video_id: str | None = Query(default=None, description="11 位视频 ID"),
    bitrate: int | None = Query(default=None, description="用于判断缓存是否命中"),
    _: None = Depends(require_api_key),
):
    br = valid_bitrate(bitrate)
    target = make_target(url, q, video_id)
    info = safe_call(probe, target, settings)
    vid = info.get("id")
    hit = bool(vid and cache.get(vid, br, downloader.codec))
    return {
        "video": info,
        "cache": {"bitrate": br, "codec": downloader.codec, "hit": hit},
        "audio_url": f"/api/music?video_id={vid}&bitrate={br}" if vid else None,
    }


@app.get("/api/lyrics", tags=["youtube"], summary="获取视频同步歌词（字幕 / 自动字幕）")
def api_lyrics(
    url: str | None = Query(default=None, description="YouTube 链接"),
    video_id: str | None = Query(default=None, description="11 位视频 ID"),
    lang: str = Query(
        default="auto",
        max_length=20,
        description="歌词语言：auto / zh-Hans / zh-Hant / en / ja / ko",
    ),
    _: None = Depends(require_api_key),
):
    """返回带 start/end/text 的歌词行，供前端播放器逐行高亮。"""
    target = make_target(url, None, video_id)
    return safe_call(get_lyrics, target, settings, lang)


# ---------------------------------------------------------------- 主接口
@app.get("/api/music", tags=["audio"], summary="获取音乐音频（首次下载并缓存，之后直接命中）")
def api_music(
    url: str | None = Query(default=None, description="YouTube 链接"),
    q: str | None = Query(default=None, description="搜索关键词，取第一条"),
    video_id: str | None = Query(default=None, description="11 位视频 ID"),
    bitrate: int | None = Query(default=None, description="MP3 码率 kbps"),
    download: bool = Query(default=False, description="true 时以附件形式下载"),
    _: None = Depends(require_api_key),
):
    br = valid_bitrate(bitrate)
    target = make_target(url, q, video_id)
    result = safe_call(downloader.get_audio, target, br)
    return audio_response(result, force_download=download)


# ---------------------------------------------------------------- 异步任务
class JobRequest(BaseModel):
    url: str | None = Field(default=None, description="YouTube 链接")
    q: str | None = Field(default=None, description="搜索关键词")
    video_id: str | None = Field(default=None, description="11 位视频 ID")
    bitrate: int | None = Field(default=None, description="MP3 码率 kbps")


@app.post("/api/jobs", status_code=202, tags=["jobs"], summary="创建后台下载任务")
def api_create_job(body: JobRequest, _: None = Depends(require_api_key)):
    br = valid_bitrate(body.bitrate)
    target = make_target(body.url, body.q, body.video_id)
    job = jobs.submit(target, br)
    return job.as_dict()


@app.get("/api/jobs", tags=["jobs"], summary="任务列表")
def api_list_jobs(
    limit: int = Query(50, ge=1, le=200),
    _: None = Depends(require_api_key),
):
    return {"count": len(jobs.list(limit=limit)), "jobs": [j.as_dict() for j in jobs.list(limit=limit)]}


@app.get("/api/jobs/{job_id}", tags=["jobs"], summary="查询任务状态与进度")
def api_get_job(job_id: str, _: None = Depends(require_api_key)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.as_dict()


@app.get("/api/jobs/{job_id}/download", tags=["jobs"], summary="下载任务产出的音频")
def api_download_job(job_id: str, _: None = Depends(require_api_key)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if job.status != "done" or job.artifact is None:
        raise HTTPException(
            status_code=409,
            detail=f"任务尚未完成（status={job.status}）" + (f"：{job.error}" if job.error else ""),
        )
    return audio_response(job.artifact, force_download=True)


# ---------------------------------------------------------------- 缓存管理
@app.get("/api/cache", tags=["cache"], summary="查看缓存统计与条目")
def api_cache_stats(_: None = Depends(require_api_key)):
    return cache.stats()


@app.get("/api/cache/lookup", tags=["cache"], summary="查询某个视频是否已缓存（纯本地，不联网）")
def api_cache_lookup(
    video_id: str = Query(..., description="11 位视频 ID"),
    bitrate: int | None = Query(default=None),
    _: None = Depends(require_api_key),
):
    br = valid_bitrate(bitrate)
    entry = cache.get(video_id, br, downloader.codec)
    return {
        "hit": entry is not None,
        "video_id": video_id,
        "bitrate": br,
        "codec": downloader.codec,
        "entry": entry.summary() if entry else None,
    }


@app.delete("/api/cache", tags=["cache"], summary="清理缓存（不传参数则清空）")
def api_cache_delete(
    video_id: str | None = Query(default=None, description="只删除该视频的缓存"),
    key: str | None = Query(default=None, description="只删除指定缓存键"),
    _: None = Depends(require_api_key),
):
    if key or video_id:
        removed = cache.delete(video_id=video_id, key=key)
    else:
        removed = cache.clear()
    return {"removed": removed, "cache": cache.stats()}


# ---------------------------------------------------------------- 直接运行
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
