"""yt-dlp 下载 + ffmpeg 音频提取 + 缓存编排。

支持两种后端：
- python：直接调用 yt_dlp Python API（首选，进度可控）；
- cli：模块不可用时回退到 `yt-dlp` 命令行（兼容只装了 CLI 的环境）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cache import AUDIO_EXTS, CacheEntry, CacheManager
from .config import Settings
from .security import InvalidTarget, Target, _normalize_url

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SKIP_SUFFIXES = {".part", ".ytdl", ".temp", ".tmp", ".info.json", ".description"}
ProgressCB = Callable[[float, str], None] | None


class DownloadError(RuntimeError):
    """下载/解析失败的统一异常。"""


class MediaTooLong(DownloadError):
    """视频时长超过限制。"""


class FileTooLarge(DownloadError):
    """音频文件超过限制。"""


class DependencyMissing(DownloadError):
    """缺少 yt-dlp 依赖。"""


# ---------------------------------------------------------------- 依赖探测
def find_ffmpeg(settings: Settings) -> str | None:
    """返回 ffmpeg 可执行文件所在目录（yt-dlp 需要目录或可执行文件路径）。"""
    configured = settings.ffmpeg_path
    if configured:
        p = Path(configured).expanduser()
        if p.is_file():
            return str(p.parent)
        if p.is_dir():
            return str(p.resolve())
        found = shutil.which(configured)
        if found:
            return str(Path(found).parent)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


def find_cli() -> str | None:
    return shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")


def resolve_backend(settings: Settings) -> str:
    forced = settings.ytdlp_backend
    if forced == "python":
        try:
            import yt_dlp  # noqa: F401
        except ImportError as exc:
            raise DependencyMissing("未安装 yt_dlp 模块，请执行 pip install yt-dlp") from exc
        return "python"
    if forced == "cli":
        if not find_cli():
            raise DependencyMissing("未找到 yt-dlp 命令行，请安装或改用 MUSIC_YTDLP_BACKEND=python")
        return "cli"
    try:
        import yt_dlp  # noqa: F401
        return "python"
    except ImportError:
        if find_cli():
            return "cli"
        raise DependencyMissing("既没有 yt_dlp 模块也没有 yt-dlp 命令，请执行 pip install -r requirements.txt")


def ytdlp_version(prefer_cli: bool = False) -> str:
    """返回实际使用的 yt-dlp 版本；CLI 后端优先读命令行版本。"""
    if prefer_cli:
        exe = find_cli()
        if exe:
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.strip()
            except Exception:
                pass
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return "not installed"


# ---------------------------------------------------------------- yt-dlp 选项
def _extractor_args_cli(args: dict) -> list[str]:
    """把嵌套 dict 转成一组 --extractor-args 参数（每个 extractor 一组）。"""
    if not args:
        return []
    result: list[str] = []
    for ie, kv in args.items():
        if not isinstance(kv, dict):
            continue
        parts = []
        for key, value in kv.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                value = ",".join(str(v) for v in value)
            parts.append(f"{key}={value}")
        if parts:
            result += ["--extractor-args", f"{ie}:{';'.join(parts)}"]
    return result


def _browser_spec_cli(spec: tuple | None) -> str | None:
    """把 4 元组还原成 yt-dlp CLI 的 --cookies-from-browser 取值。"""
    if not spec:
        return None
    return ":".join(str(x) for x in spec if x)


_JS_RUNTIME_CACHE: dict | None | str = "unset"


def _js_runtime_spec() -> dict | None:
    """返回 yt-dlp Python API 的 js_runtimes 配置。

    YouTube 2025 年起对 web 客户端强制 JS challenge（n / signature），
    需要 Node/Deno + yt-dlp-ejs 才能拿到真实音视频格式；否则只剩图片格式。
    """
    global _JS_RUNTIME_CACHE
    if _JS_RUNTIME_CACHE != "unset":
        return _JS_RUNTIME_CACHE  # type: ignore[return-value]
    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        _JS_RUNTIME_CACHE = None
        return None
    if shutil.which("deno"):
        _JS_RUNTIME_CACHE = {"deno": {}}
    elif shutil.which("node"):
        _JS_RUNTIME_CACHE = {"node": {}}
    else:
        _JS_RUNTIME_CACHE = None
    return _JS_RUNTIME_CACHE  # type: ignore[return-value]


_CLI_JS_SUPPORT: bool | None = None


def _cli_supports_js_runtimes() -> bool:
    """旧版 CLI（如 2026.03.17）没有 --js-runtimes，传了会直接报错。"""
    global _CLI_JS_SUPPORT
    if _CLI_JS_SUPPORT is not None:
        return _CLI_JS_SUPPORT
    exe = find_cli()
    if not exe:
        _CLI_JS_SUPPORT = False
        return False
    try:
        r = subprocess.run(
            [exe, "--help"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        _CLI_JS_SUPPORT = "--js-runtimes" in (r.stdout or "")
    except Exception:
        _CLI_JS_SUPPORT = False
    return _CLI_JS_SUPPORT


_COOKIE_SNAPSHOT_PREFIX = "music_api_cookies_"
_COOKIE_SNAPSHOT_MAX_AGE = 3600


def _cookie_snapshot(source: Path) -> Path:
    """把主 cookie 文件复制成临时副本，供 yt-dlp 使用。

    yt-dlp 的 YoutubeDL.close() 只要设置了 cookiefile 就会调用
    cookiejar.save() 回写该文件，而回写结果会丢失 Cookie-Editor 导出的
    HttpOnly 登录态（SID / HSID / SSID / SAPISID / LOGIN_INFO /
    __Secure-1PSID ...），随后所有请求都会被
    "Sign in to confirm you're not a bot" 拦截。
    因此主凭证文件只作为只读来源，实际读写都落在临时副本上。
    """
    tmp_dir = Path(tempfile.gettempdir()) / "music-mp3-api-cookies"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in tmp_dir.glob(_COOKIE_SNAPSHOT_PREFIX + "*.txt"):
        try:
            if now - old.stat().st_mtime > _COOKIE_SNAPSHOT_MAX_AGE:
                old.unlink()
        except OSError:
            pass
    fd, name = tempfile.mkstemp(
        prefix=_COOKIE_SNAPSHOT_PREFIX, suffix=".txt", dir=tmp_dir
    )
    try:
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            shutil.copyfileobj(src, dst)
    except Exception:
        try:
            Path(name).unlink()
        except OSError:
            pass
        raise
    return Path(name)


def _base_opts(settings: Settings) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": settings.ytdlp_timeout,
        "retries": settings.ytdlp_retries,
        "fragment_retries": settings.ytdlp_retries,
        "extractor_retries": settings.ytdlp_retries,
        "http_headers": {"User-Agent": USER_AGENT},
        "geo_bypass": True,
    }
    if settings.proxy:
        opts["proxy"] = settings.proxy
    else:
        # 显式禁用环境代理：不少代理出口会被 YouTube 标记，返回
        # "The page needs to be reloaded" / 空格式列表。需要代理时用 MUSIC_PROXY 显式指定。
        opts["proxy"] = ""
    js_rt = _js_runtime_spec()
    if js_rt:
        opts["js_runtimes"] = js_rt
    if settings.cookies_file:
        p = Path(settings.cookies_file).expanduser()
        if p.is_file():
            # 用临时副本，防止 yt-dlp 回写时冲掉 HttpOnly 登录态
            opts["cookiefile"] = str(_cookie_snapshot(p.resolve()))
        else:
            logger.warning("配置的 cookies 文件不存在：%s", p)
    # 两种 cookie 来源可同时配置，yt-dlp 会自动合并
    if settings.cookies_from_browser:
        opts["cookiesfrombrowser"] = settings.cookies_from_browser
    if settings.ytdlp_extractor_args:
        opts["extractor_args"] = settings.ytdlp_extractor_args
    ffmpeg_dir = find_ffmpeg(settings)
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    return opts


def _cli_common_args(settings: Settings, opts: dict) -> list[str]:
    args = [
        "--ignore-config",
        "--no-warnings",
        "--no-playlist",
        "--socket-timeout", str(settings.ytdlp_timeout),
        "--retries", str(settings.ytdlp_retries),
        "--user-agent", USER_AGENT,
    ]
    if settings.proxy:
        args += ["--proxy", settings.proxy]
    if opts.get("cookiefile"):
        args += ["--cookies", opts["cookiefile"]]
    if opts.get("cookiesfrombrowser"):
        spec = _browser_spec_cli(opts["cookiesfrombrowser"])
        if spec:
            args += ["--cookies-from-browser", spec]
    if opts.get("ffmpeg_location"):
        args += ["--ffmpeg-location", opts["ffmpeg_location"]]
    if opts.get("ignoreerrors"):
        args.append("--ignore-errors")
    if opts.get("extract_flat"):
        args.append("--flat-playlist")
    if opts.get("playlistend"):
        args += ["--playlist-end", str(opts["playlistend"])]
    if opts.get("js_runtimes") and _cli_supports_js_runtimes():
        runtime = "deno" if "deno" in opts["js_runtimes"] else "node"
        args += ["--js-runtimes", runtime]
    args += _extractor_args_cli(opts.get("extractor_args") or {})
    return args


def _emit(cb: ProgressCB, pct: float, message: str) -> None:
    if cb is None:
        return
    try:
        cb(max(0.0, min(100.0, pct)), message)
    except Exception:
        pass


# ---------------------------------------------------------------- 元数据归一化
def normalize_info(info: dict) -> dict:
    if not isinstance(info, dict):
        raise DownloadError("yt-dlp 未返回有效信息")

    vid = info.get("id")
    webpage_url = info.get("webpage_url") or info.get("url")
    if not webpage_url and vid:
        webpage_url = f"https://www.youtube.com/watch?v={vid}"

    thumbnails = info.get("thumbnails") or []
    thumbnail = info.get("thumbnail")
    if not thumbnail and thumbnails:
        thumbnail = (thumbnails[-1] or {}).get("url")

    duration = info.get("duration")
    if duration is not None:
        try:
            duration = int(float(duration))
        except (TypeError, ValueError):
            duration = None

    description = info.get("description") or ""
    return {
        "id": vid,
        "title": info.get("title") or info.get("alt_title") or (vid or "unknown"),
        "channel": info.get("uploader") or info.get("channel") or info.get("artist") or None,
        "duration": duration,
        "thumbnail": thumbnail,
        "webpage_url": webpage_url,
        "description": description[:300],
        "view_count": info.get("view_count"),
        "upload_date": info.get("upload_date"),
    }


def _first_entry(info: dict) -> dict:
    if info.get("_type") in {"playlist", "multi_video", "url", None} and info.get("entries"):
        entries = [e for e in info["entries"] if isinstance(e, dict)]
        if entries:
            return entries[0]
    return info


# ---------------------------------------------------------------- 抽取实现
def _py_extract(value: str, opts: dict) -> dict:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(value, download=False) or {}
    except Exception as exc:  # yt_dlp.utils.DownloadError / ExtractorError / 网络异常
        raise DownloadError(f"yt-dlp 解析失败：{exc}") from exc


def _cli_extract(value: str, opts: dict, timeout: int) -> dict:
    exe = find_cli()
    if not exe:
        raise DependencyMissing("未找到 yt-dlp 命令行")
    cmd = [exe, *_cli_common_args_for_extract(opts), "--dump-single-json", value]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout * 4,
        )
    except subprocess.TimeoutExpired as exc:
        raise DownloadError("yt-dlp 解析超时") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise DownloadError("yt-dlp 解析失败：" + " / ".join(detail))
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise DownloadError("yt-dlp 返回的 JSON 无法解析") from exc


def _cli_common_args_for_extract(opts: dict) -> list[str]:
    # _cli_extract 独立使用（不依赖 settings），从 opts 反向生成参数
    args = ["--ignore-config", "--no-warnings", "--no-playlist"]
    if opts.get("ignoreerrors"):
        args.append("--ignore-errors")
    if opts.get("extract_flat"):
        args.append("--flat-playlist")
    if opts.get("playlistend"):
        args += ["--playlist-end", str(opts["playlistend"])]
    if opts.get("proxy"):
        args += ["--proxy", opts["proxy"]]
    if opts.get("cookiefile"):
        args += ["--cookies", opts["cookiefile"]]
    if opts.get("cookiesfrombrowser"):
        spec = _browser_spec_cli(opts["cookiesfrombrowser"])
        if spec:
            args += ["--cookies-from-browser", spec]
    if opts.get("ffmpeg_location"):
        args += ["--ffmpeg-location", opts["ffmpeg_location"]]
    if opts.get("socket_timeout"):
        args += ["--socket-timeout", str(opts["socket_timeout"])]
    if opts.get("retries") is not None:
        args += ["--retries", str(opts["retries"])]
    if opts.get("js_runtimes") and _cli_supports_js_runtimes():
        runtime = "deno" if "deno" in opts["js_runtimes"] else "node"
        args += ["--js-runtimes", runtime]
    args += _extractor_args_cli(opts.get("extractor_args") or {})
    return args


def extract_info(target: Target, settings: Settings, *, flat: bool = False, ignore_errors: bool = False) -> dict:
    backend = resolve_backend(settings)
    opts = _base_opts(settings)
    if flat:
        opts.update({"extract_flat": "in_playlist", "playlistend": target.search_limit, "ignoreerrors": True})
    elif ignore_errors:
        opts["ignoreerrors"] = True
    elif target.kind == "search":
        opts["playlistend"] = target.search_limit

    if backend == "python":
        info = _py_extract(target.value, opts)
    else:
        info = _cli_extract(target.value, opts, settings.ytdlp_timeout)
    return info


def search_videos(target: Target, settings: Settings) -> list[dict]:
    info = extract_info(target, settings, flat=True)
    entries = info.get("entries") or ([info] if info.get("id") else [])
    return [normalize_info(e) for e in entries if isinstance(e, dict)][: target.search_limit]


def probe(target: Target, settings: Settings) -> dict:
    info = extract_info(target, settings)
    info = _first_entry(info)
    result = normalize_info(info)
    if not result.get("id"):
        raise DownloadError("未能解析出视频 ID，请检查链接或关键词")
    # 确保下载阶段拿到的是合法的 YouTube 页面地址
    url = result.get("webpage_url")
    if url:
        try:
            result["webpage_url"] = _normalize_url(url, settings)
        except InvalidTarget:
            if result.get("id"):
                result["webpage_url"] = f"https://www.youtube.com/watch?v={result['id']}"
            else:
                raise
    return result


# ---------------------------------------------------------------- 结果对象
@dataclass
class AudioResult:
    entry: CacheEntry
    meta: dict
    cache_hit: bool
    codec: str
    file_path: Path          # 缓存文件的绝对路径，供 FileResponse 使用

    def as_dict(self) -> dict:
        return {
            "cache_hit": self.cache_hit,
            "codec": self.codec,
            "bitrate": self.entry.bitrate,
            "size": self.entry.size,
            "ext": self.entry.ext,
            "key": self.entry.key,
            **{k: self.meta.get(k) for k in ("id", "title", "channel", "duration", "webpage_url", "thumbnail")},
        }


# ---------------------------------------------------------------- 下载编排
class Downloader:
    def __init__(self, settings: Settings, cache: CacheManager):
        self.settings = settings
        self.cache = cache
        self._sem = threading.BoundedSemaphore(settings.max_concurrent_downloads)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._ffmpeg_dir = find_ffmpeg(settings)
        self.backend = resolve_backend(settings)

    @property
    def codec(self) -> str:
        return "mp3" if self._ffmpeg_dir else "native"

    @property
    def has_ffmpeg(self) -> bool:
        return bool(self._ffmpeg_dir)

    def _lock_for(self, name: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(name, threading.Lock())

    # ---- 主入口 ----
    def get_audio(self, target: Target, bitrate: int | None = None, progress_cb: ProgressCB = None) -> AudioResult:
        bitrate = bitrate or self.settings.mp3_bitrate
        codec = self.codec

        # 1) 带 video_id 的直链可完全离线命中，不触发任何网络请求
        if target.video_id:
            hit = self.cache.get(target.video_id, bitrate, codec)
            if hit:
                _emit(progress_cb, 100, "缓存命中")
                return AudioResult(hit, dict(hit.meta), True, codec, self.cache.path_of(hit))

        # 2) 同一输入只做一次元数据解析
        with self._lock_for("probe:" + target.value):
            _emit(progress_cb, 5, "解析视频信息")
            info = probe(target, self.settings)

        vid = info["id"]
        key = CacheManager.key_for(vid, bitrate, codec)

        # 3) 按「解析后的 video_id」去重，搜索词与直链指向同一视频时不会重复下载
        with self._lock_for("dl:" + key):
            hit = self.cache.get(vid, bitrate, codec)
            if hit:
                _emit(progress_cb, 100, "缓存命中")
                return AudioResult(hit, dict(hit.meta), True, codec, self.cache.path_of(hit))

            duration = info.get("duration")
            max_dur = self.settings.max_duration_sec
            if max_dur and duration and duration > max_dur:
                raise MediaTooLong(
                    f"视频时长 {duration}s 超过上限 {max_dur}s，可通过 MUSIC_MAX_DURATION_SEC 调整"
                )

            with self._sem:  # 全局并发限制
                _emit(progress_cb, 10, "开始下载音频")
                with tempfile.TemporaryDirectory(prefix="music-api-") as tmp:
                    src = self._download_to_tmp(info, bitrate, codec, Path(tmp), progress_cb)
                    size = src.stat().st_size
                    limit = self.settings.max_file_bytes
                    if limit and size > limit:
                        raise FileTooLarge(
                            f"音频 {size / 1024 / 1024:.1f}MB 超过上限 {limit / 1024 / 1024:.0f}MB"
                        )
                    entry = self.cache.put(vid, bitrate, codec, src, meta=info)
            _emit(progress_cb, 100, "完成")
            return AudioResult(entry, info, False, codec, self.cache.path_of(entry))

    # ---- 实际下载 ----
    def _ffmpeg_exe(self) -> str | None:
        if self._ffmpeg_dir:
            exe = Path(self._ffmpeg_dir) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if exe.is_file():
                return str(exe)
        return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

    def _download_to_tmp(
        self, info: dict, bitrate: int, codec: str, tmpdir: Path, progress_cb: ProgressCB
    ) -> Path:
        vid = info["id"]
        url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        outtmpl = str(tmpdir / f"{vid}.%(ext)s")

        if self.backend == "python":
            self._py_download(url, outtmpl, progress_cb)
        else:
            self._cli_download(url, outtmpl, progress_cb)

        raw = self._find_produced(tmpdir, vid)
        if codec != "mp3":
            return raw
        _emit(progress_cb, 90, "ffmpeg 提取音频并转码 MP3")
        return self._transcode(raw, bitrate, tmpdir / f"{vid}.converted.mp3")

    def _find_produced(self, tmpdir: Path, vid: str) -> Path:
        produced = [
            p for p in tmpdir.iterdir()
            if p.is_file()
            and p.name.startswith(f"{vid}.")
            and not any(p.name.endswith(s) for s in _SKIP_SUFFIXES)
            and p.suffix.lower() in (AUDIO_EXTS | {".mp4", ".mkv", ".mov", ".3gp"})
        ]
        if not produced:
            raise DownloadError("下载完成但未找到音频文件（可能源不可下载或受区域限制）")
        audio = [p for p in produced if p.suffix.lower() in AUDIO_EXTS]
        return max(audio or produced, key=lambda p: p.stat().st_size)

    def _transcode(self, src: Path, bitrate: int, dest: Path) -> Path:
        """显式调用 ffmpeg 提取音轨并统一转成指定码率的 MP3。

        不依赖 yt-dlp 的 FFmpegExtractAudio：后者在源文件已是 MP3 时会直接
        stream copy，导致请求的码率不生效。这里自己转，行为可预期。
        """
        ffmpeg = self._ffmpeg_exe()
        if not ffmpeg:
            raise DependencyMissing("找不到 ffmpeg 可执行文件，无法转码为 MP3")
        base = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(src), "-vn", "-map", "0:a:0",
        ]
        last_error: list[str] = []
        for encoder in ("libmp3lame", "mp3"):
            cmd = [*base, "-c:a", encoder, "-b:a", f"{bitrate}k", str(dest)]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=max(120, self.settings.ytdlp_timeout * 5),
                )
            except subprocess.TimeoutExpired as exc:
                raise DownloadError("ffmpeg 转码超时") from exc
            if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
                return dest
            last_error = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        raise DownloadError("ffmpeg 转码失败：" + " / ".join(last_error))

    def _py_download(self, url: str, outtmpl: str, progress_cb: ProgressCB) -> None:
        import yt_dlp

        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = 10 + (done / total * 75 if total else 0)
                _emit(progress_cb, min(pct, 85), f"下载中 {done / 1024 / 1024:.1f} MB")
            elif d.get("status") == "finished":
                _emit(progress_cb, 88, "下载完成，准备转码")

        opts = _base_opts(self.settings)
        opts.update(
            {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "overwrites": True,
                "noprogress": True,
                "progress_hooks": [hook],
            }
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            raise DownloadError(f"下载失败：{exc}") from exc

    def _cli_download(self, url: str, outtmpl: str, progress_cb: ProgressCB) -> None:
        exe = find_cli()
        if not exe:
            raise DependencyMissing("未找到 yt-dlp 命令行")
        opts = _base_opts(self.settings)
        cmd = [
            exe, *_cli_common_args(self.settings, opts), "--no-progress",
            "-f", "bestaudio/best", "-o", outtmpl, url,
        ]
        _emit(progress_cb, 20, "下载最佳音轨")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.settings.ytdlp_timeout * 10,
            )
        except subprocess.TimeoutExpired as exc:
            raise DownloadError("下载超时") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            raise DownloadError("下载失败：" + " / ".join(detail))

    def health(self) -> dict:
        return {
            "backend": self.backend,
            "ytdlp_version": ytdlp_version(prefer_cli=self.backend == "cli"),
            "ffmpeg_available": self.has_ffmpeg,
            "ffmpeg_location": self._ffmpeg_dir,
            "codec": self.codec,
            "bitrate": self.settings.mp3_bitrate,
            "max_duration_sec": self.settings.max_duration_sec,
            "max_concurrent_downloads": self.settings.max_concurrent_downloads,
            "cookies": {
                "file": self.settings.cookies_file or None,
                "file_exists": bool(
                    self.settings.cookies_file and Path(self.settings.cookies_file).is_file()
                ),
                "from_browser": _browser_spec_cli(self.settings.cookies_from_browser),
            },
        }
