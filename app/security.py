"""输入归一化与安全校验：只允许 YouTube 链接 / 11 位视频 ID / 搜索关键词。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .config import Settings

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ILLEGAL_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')
_MAX_QUERY_LEN = 200


class InvalidTarget(ValueError):
    """请求目标不合法（非 YouTube 域名、参数注入、空输入等）。"""


@dataclass(frozen=True)
class Target:
    """交给 yt-dlp 的规范化输入。"""

    kind: str                 # "url" | "video_id" | "search"
    value: str                # yt-dlp 实际使用的输入
    display: str              # 原始用户输入（用于日志/回显）
    video_id: str | None      # 能从 URL 直接解析出来时带上，用于提前命中缓存
    search_limit: int = 1


def _extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")

    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if VIDEO_ID_RE.match(candidate) else None

    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0] in {"shorts", "embed", "live", "v"} and len(parts) >= 2:
        candidate = parts[1]
        if VIDEO_ID_RE.match(candidate):
            return candidate

    qs = parse_qs(parsed.query)
    candidate = (qs.get("v") or [None])[0]
    return candidate if candidate and VIDEO_ID_RE.match(candidate) else None


def _normalize_url(raw: str, settings: Settings) -> str:
    value = raw.strip()
    # 用户可能省略协议
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value.lstrip("/")

    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise InvalidTarget("仅支持 https 链接")
    if parsed.username or parsed.password:
        raise InvalidTarget("链接中不允许携带用户信息")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidTarget("无法解析链接域名")
    if host not in settings.allowed_hosts:
        raise InvalidTarget(
            f"域名 {host!r} 不在允许列表中（默认仅允许 YouTube，可通过 MUSIC_EXTRA_ALLOWED_HOSTS 扩展）"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidTarget("链接端口不合法") from exc
    if port is not None and port != 443:
        raise InvalidTarget("仅允许标准 443 端口")
    return value


def _normalize_query(raw: str) -> str:
    q = unicodedata.normalize("NFKC", raw).strip()
    q = "".join(ch for ch in q if ch.isprintable())
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        raise InvalidTarget("搜索关键词不能为空")
    if len(q) > _MAX_QUERY_LEN:
        q = q[:_MAX_QUERY_LEN]
    # 防止以 '-' 开头被 yt-dlp 当成命令行参数解析
    q = q.lstrip("- ").strip()
    if not q:
        raise InvalidTarget("搜索关键词不合法")
    # 防止把 yt-dlp 的特殊前缀注入到搜索词里
    if re.match(r"^(ytsearch|ytsearch\d*|all|playlist):", q, re.I):
        q = " " + q
    return q


def build_target(
    *,
    url: str | None = None,
    q: str | None = None,
    video_id: str | None = None,
    search_limit: int = 1,
    settings: Settings,
) -> Target:
    """三选一：url / q / video_id。优先级 url > video_id > q。"""
    limit = max(1, min(search_limit, settings.search_max_limit))

    if url and url.strip():
        value = _normalize_url(url, settings)
        return Target(
            kind="url",
            value=value,
            display=url.strip(),
            video_id=_extract_video_id(value),
            search_limit=limit,
        )

    if video_id and video_id.strip():
        vid = video_id.strip()
        if not VIDEO_ID_RE.match(vid):
            raise InvalidTarget("视频 ID 必须是 11 位 [A-Za-z0-9_-] 字符")
        value = f"https://www.youtube.com/watch?v={vid}"
        return Target(kind="video_id", value=value, display=vid, video_id=vid, search_limit=limit)

    if q and q.strip():
        clean = _normalize_query(q)
        return Target(
            kind="search",
            value=f"ytsearch{limit}:{clean}",
            display=clean,
            video_id=None,
            search_limit=limit,
        )

    raise InvalidTarget("必须提供 url、video_id 或 q 之一")


def safe_filename(name: str, fallback: str = "audio") -> str:
    """生成跨平台的 Content-Disposition 文件名（不含路径分隔符与控制字符）。"""
    cleaned = _ILLEGAL_FILENAME_RE.sub("_", unicodedata.normalize("NFKC", name or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]
