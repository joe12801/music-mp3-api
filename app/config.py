"""集中配置：全部通过 MUSIC_* 环境变量注入，均带安全默认值。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """轻量 .env 加载：只补齐缺失的 MUSIC_* 变量，环境变量本身优先级更高。

    不引入 python-dotenv 依赖；.env 已在 .gitignore 中，绝不入库。
    """
    path = BASE_DIR / ".env"
    if not path.is_file():
        return
    try:
        raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key.startswith("MUSIC_") or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv()


def _sanitize_runtime_environment() -> None:
    """清除会破坏 yt-dlp JS challenge solver 的 NODE_OPTIONS 注入。

    部分沙箱环境给 Node 注入 ``--experimental-permission``（连同
    ``--require=<shim>``），会让 yt-dlp-ejs 拉起的 node 进程连自己的脚本都
    读不了（ERR_ACCESS_DENIED），导致 n/signature challenge 全部失败、
    YouTube 只返回 storyboard 图片格式。服务本身不依赖该注入；清除它只影响
    yt-dlp 启动的 node 子进程。设 MUSIC_KEEP_NODE_OPTIONS=1 可保留。
    """
    keep = os.environ.get("MUSIC_KEEP_NODE_OPTIONS", "").strip().lower()
    if keep in {"1", "true", "yes", "on"}:
        return
    raw = os.environ.get("NODE_OPTIONS", "")
    # yt-dlp 会用 `--experimental-permission` 运行 node；若环境里还带
    # `--require=<shim>`，shim 读取自身文件就会被拒，进而拖垮整个 solver。
    if raw and ("--require" in raw or "--experimental-permission" in raw):
        os.environ.pop("NODE_OPTIONS", None)
        logger.debug("已清除 NODE_OPTIONS 注入，供 yt-dlp EJS 使用")


_sanitize_runtime_environment()
DEFAULT_CACHE_DIR = BASE_DIR / "data" / "cache"

ALLOWED_BITRATES: tuple[int, ...] = (64, 96, 128, 192, 256, 320)

# 默认仅放行 YouTube 相关域名，避免把服务变成任意 URL 的下载代理（SSRF / 滥用）
YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "www.music.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _csv(name: str) -> tuple[str, ...]:
    raw = _str(name)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _json_dict(name: str) -> dict:
    """读取 JSON 对象形式的高级 yt-dlp extractor_args。"""
    raw = _str(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except ValueError as exc:
        logger.warning("%s 不是合法 JSON，已忽略：%s", name, exc)
        return {}


def _browser_spec(name: str) -> tuple[str, str | None, str | None, str | None] | None:
    """解析 cookies-from-browser 规格：browser[:profile[:keyring[:container]]]。

    返回 yt-dlp Python API 需要的 4 元组；未配置时返回 None。
    """
    raw = _str(name)
    if not raw:
        return None
    parts = raw.split(":")
    get = lambda i: parts[i].strip() if len(parts) > i and parts[i].strip() else None
    return (get(0).lower(), get(1), get(2), get(3))


@dataclass(frozen=True)
class Settings:
    # 服务
    host: str
    port: int
    log_level: str
    api_key: str
    cors_origins: tuple[str, ...]
    demo_ui: bool

    # 缓存
    cache_dir: Path
    max_cache_mb: float
    cache_ttl_days: float
    lyrics_ttl_days: float
    lyrics_external: bool
    max_file_mb: float

    # 音频
    mp3_bitrate: int
    max_duration_sec: int

    # 下载
    max_concurrent_downloads: int
    ytdlp_timeout: int
    ytdlp_retries: int
    search_max_limit: int
    ytdlp_backend: str
    ffmpeg_path: str
    proxy: str
    cookies_file: str
    cookies_from_browser: tuple[str, str | None, str | None, str | None] | None
    ytdlp_extractor_args: dict
    extra_allowed_hosts: tuple[str, ...]

    # 异步任务
    job_workers: int
    job_ttl_sec: int

    @property
    def max_cache_bytes(self) -> int:
        return int(self.max_cache_mb * 1024 * 1024)

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)

    @property
    def ttl_seconds(self) -> float:
        return self.cache_ttl_days * 86400

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return YOUTUBE_HOSTS | frozenset(h.lower().lstrip(".") for h in self.extra_allowed_hosts)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取一次并缓存；测试中修改环境变量后调用 get_settings.cache_clear() 即可。"""
    cache_dir = Path(_str("MUSIC_CACHE_DIR", str(DEFAULT_CACHE_DIR))).expanduser()
    if not cache_dir.is_absolute():
        cache_dir = (BASE_DIR / cache_dir).resolve()

    bitrate = _int("MUSIC_MP3_BITRATE", 192)
    if bitrate not in ALLOWED_BITRATES:
        bitrate = 192

    backend = _str("MUSIC_YTDLP_BACKEND", "auto").lower()
    if backend not in {"auto", "python", "cli"}:
        backend = "auto"

    origins = _csv("MUSIC_CORS_ORIGINS") or ("*",)

    cookies_file = _str("MUSIC_COOKIES_FILE")
    if cookies_file:
        p = Path(cookies_file).expanduser()
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
        cookies_file = str(p)

    return Settings(
        host=_str("MUSIC_HOST", "127.0.0.1"),
        port=_int("MUSIC_PORT", 8000),
        log_level=_str("MUSIC_LOG_LEVEL", "info"),
        api_key=_str("MUSIC_API_KEY"),
        cors_origins=origins,
        demo_ui=_bool("MUSIC_DEMO_UI", True),
        cache_dir=cache_dir,
        max_cache_mb=max(0.0, _float("MUSIC_MAX_CACHE_MB", 2048)),
        cache_ttl_days=max(0.0, _float("MUSIC_CACHE_TTL_DAYS", 30)),
        # 字幕歌词单独缓存：默认 7 天，避免视频后补字幕后长期停留在旧版本
        lyrics_ttl_days=max(0.0, _float("MUSIC_LYRICS_TTL_DAYS", 7)),
        # YouTube 无字幕时是否回退到 LRCLIB 等第三方歌词库
        lyrics_external=_bool("MUSIC_LYRICS_EXTERNAL", True),
        max_file_mb=max(0.0, _float("MUSIC_MAX_FILE_MB", 60)),
        mp3_bitrate=bitrate,
        max_duration_sec=max(0, _int("MUSIC_MAX_DURATION_SEC", 900)),
        max_concurrent_downloads=max(1, _int("MUSIC_MAX_CONCURRENT_DOWNLOADS", 2)),
        ytdlp_timeout=max(5, _int("MUSIC_YTDLP_TIMEOUT", 60)),
        ytdlp_retries=max(0, _int("MUSIC_YTDLP_RETRIES", 3)),
        search_max_limit=max(1, _int("MUSIC_SEARCH_MAX_LIMIT", 20)),
        ytdlp_backend=backend,
        ffmpeg_path=_str("MUSIC_FFMPEG_PATH"),
        proxy=_str("MUSIC_PROXY"),
        cookies_file=cookies_file,
        cookies_from_browser=_browser_spec("MUSIC_COOKIES_FROM_BROWSER"),
        ytdlp_extractor_args=_json_dict("MUSIC_YTDLP_EXTRACTOR_ARGS"),
        extra_allowed_hosts=_csv("MUSIC_EXTRA_ALLOWED_HOSTS"),
        job_workers=max(1, _int("MUSIC_JOB_WORKERS", 4)),
        job_ttl_sec=max(60, _int("MUSIC_JOB_TTL_SEC", 3600)),
    )
