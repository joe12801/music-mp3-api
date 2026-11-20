"""YouTube 字幕 / 自动字幕 -> 带时间轴的同步歌词。

设计目标：
- 不依赖前端跨域访问 YouTube，复用现有 yt-dlp 的 cookie / 代理 / JS runtime 配置；
- 优先人工字幕，其次自动字幕；中文用户默认优先简体中文、繁体中文，再回退英文等；
- 解析结果按 video_id + 语言缓存到 data/lyrics，避免每次播放重复拉取；
- 视频没有字幕时返回空列表而不是报错，播放器可优雅提示。
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable

from .config import BASE_DIR, Settings
from .downloader import (
    DependencyMissing,
    DownloadError,
    _base_opts,
    _cli_common_args_for_extract,
    _first_entry,
    extract_info,
    find_cli,
    resolve_backend,
    USER_AGENT,
)
from .security import Target

logger = logging.getLogger(__name__)

# 自动模式下的语言优先级：中文人工/自动字幕都优先于英文人工字幕。
LANG_PRIORITY: tuple[str, ...] = (
    "zh-Hans", "zh-CN", "zh-Hant", "zh-TW", "zh-HK", "zh",
    "en", "ja", "ko",
)
LANG_LABELS: dict[str, str] = {
    "auto": "自动选择",
    "zh-Hans": "简体中文",
    "zh-CN": "简体中文",
    "zh-Hant": "繁体中文",
    "zh-TW": "繁体中文",
    "zh-HK": "繁体中文",
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
}

_TS_RE = re.compile(
    r"(?P<start>\d{1,3}:\d{1,2}(?::\d{1,2})?[.,]\d{1,3}|\d{1,3}:\d{1,2}(?::\d{1,2})?)"
    r"\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{1,2}(?::\d{1,2})?[.,]\d{1,3}|\d{1,3}:\d{1,2}(?::\d{1,2})?)"
)
_TAG_RE = re.compile(r"<[^>]+>")
_POSITION_RE = re.compile(r"\{\\[^}]*\}")
_LRC_TS_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_OFFSET_RE = re.compile(r"^\[offset:([+-]?\d+)\]", re.I)
_LRC_META_RE = re.compile(
    r"^(?:作词|作曲|编曲|演唱|作詞| lyrics| music|composed|written|arranged|produced)\s*[:：]",
    re.I,
)
_PAREN_NOISE_RE = re.compile(
    r"[（(\[【][^）)\]】]{0,40}"
    r"(?:官方|official|video|music|mv|audio|lyrics?|高清|超清|国|国语|粤语|现场|live|remaster|remix|cover|karaoke|纯音乐|伴奏)"
    r"[^）)\]】]{0,40}[）)\]】]",
    re.I,
)
_CJK_TOKEN_RE = re.compile(r"[\u3400-\u9fff][\u3400-\u9fff0-9A-Za-z'&\-]{1,15}")
_LYRICS_DIR = BASE_DIR / "data" / "lyrics"
_LRCLIB_ENDPOINT = "https://lrclib.net/api/search"


def _safe_part(value: str, fallback: str = "x") -> str:
    part = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or ""))
    return part or fallback


def cache_path(video_id: str, lang: str) -> Path:
    return _LYRICS_DIR / f"{_safe_part(video_id)}.{_safe_part(lang, 'auto')}.json"


def parse_timestamp(raw: str) -> float:
    """解析 WebVTT 时间戳，兼容 00:01:02.345 / 01:02.345 / 逗号小数。"""
    cleaned = raw.strip().replace(",", ".")
    fields = cleaned.split(":")
    try:
        if len(fields) == 3:
            hours, minutes, seconds = fields
        elif len(fields) == 2:
            hours, minutes, seconds = "0", fields[0], fields[1]
        else:
            return 0.0
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0


def clean_cue_text(raw: str) -> str:
    """去掉 WebVTT 标签、定位标记与 HTML 实体。"""
    # 先剥离真实 VTT 标签，再反转义实体；否则 &lt;合唱&gt; 会被误当成标签删掉。
    text = _TAG_RE.sub("", raw or "")
    text = html.unescape(text)
    text = _POSITION_RE.sub("", text)
    text = text.replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_vtt(content: str) -> list[dict]:
    """把 VTT 文本解析成 [{start, end, text}, ...]，按开始时间排序。"""
    if not content:
        return []
    lines: list[dict] = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        block_lines = block.splitlines()
        cue_at = None
        match = None
        for idx, line in enumerate(block_lines):
            found = _TS_RE.search(line)
            if found:
                cue_at, match = idx, found
                break
        if cue_at is None or match is None:
            continue
        start = parse_timestamp(match.group("start"))
        end = parse_timestamp(match.group("end"))
        if end <= start:
            continue
        text = clean_cue_text(" ".join(block_lines[cue_at + 1:]))
        if not text:
            continue
        lines.append({"start": round(start, 3), "end": round(end, 3), "text": text[:600]})

    lines.sort(key=lambda x: (x["start"], x["end"]))
    # YouTube 滚动字幕会反复输出同一句的“越来越长”版本：
    #   [21.79-21.80] 我们对爱情并不陌生
    #   [21.80-25.95] 我们对爱情并不陌生 。 你我都清楚规则。
    #   [25.95-25.96] 。 你我都清楚规则。
    # 相邻且互为前缀/后缀的行合并，保留更长的文本。
    merged: list[dict] = []
    for line in lines:
        if merged:
            prev = merged[-1]
            a, b = prev["text"], line["text"]
            rolling = (
                a == b
                or (len(a) >= 2 and len(b) >= 2 and (a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a)))
            )
            if rolling and line["start"] <= prev["end"] + 0.25:
                if len(b) > len(a):
                    prev["text"] = b
                prev["end"] = max(prev["end"], line["end"])
                continue
        merged.append(line)
    return merged


# ---------------------------------------------------------------- 第三方歌词兜底（LRCLIB）
def parse_lrc(content: str) -> list[dict]:
    """解析 LRC 同步歌词；end 取下一句 start，最后一句默认 8 秒。"""
    if not content:
        return []
    cues: list[dict] = []
    offset = 0.0
    for raw in content.replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if not line:
            continue
        offset_match = _LRC_OFFSET_RE.match(line)
        if offset_match:
            try:
                offset = int(offset_match.group(1)) / 1000
            except ValueError:
                pass
            continue
        stamps = list(_LRC_TS_RE.finditer(line))
        if not stamps:
            continue
        text = html.unescape(line[stamps[-1].end():].strip())
        if not text or _LRC_META_RE.search(text):
            continue
        for stamp in stamps:
            minutes, seconds = int(stamp.group(1)), int(stamp.group(2))
            frac = stamp.group(3)
            if frac:
                seconds += float(frac) / (10 ** len(frac))
            start = max(0.0, minutes * 60 + seconds + offset)
            cues.append({"start": round(start, 3), "end": round(start + 8, 3), "text": text[:600]})

    cues.sort(key=lambda x: (x["start"], x["text"]))
    merged: list[dict] = []
    for cue in cues:
        if merged and merged[-1]["start"] == cue["start"] and merged[-1]["text"] == cue["text"]:
            continue
        merged.append(cue)
    for idx, cue in enumerate(merged):
        cue["end"] = merged[idx + 1]["start"] if idx + 1 < len(merged) else cue["start"] + 8
    return merged


def _clean_title(raw: str) -> str:
    text = html.unescape(str(raw or ""))
    text = _PAREN_NOISE_RE.sub(" ", text)
    text = re.sub(r"[\[【][^\]】]*[\]】]", " ", text)
    text = text.replace("｜", " - ").replace("|", " - ")
    return re.sub(r"\s+", " ", text).strip(" \t-–—_")


def _search_terms(info: dict) -> tuple[list[str], str | None, float | None]:
    title = _clean_title(info.get("title"))
    artist = info.get("artist")
    if not artist and isinstance(info.get("artists"), list) and info["artists"]:
        artist = info["artists"][0]
    artist = _clean_title(artist) if artist else None
    try:
        duration = float(info.get("duration")) if info.get("duration") else None
    except (TypeError, ValueError):
        duration = None

    terms: list[str] = []

    def add(value: str | None) -> None:
        cleaned = _clean_title(value or "")
        if 1 <= len(cleaned) <= 60 and cleaned not in terms:
            terms.append(cleaned)

    add(title)
    for part in re.split(r"\s+[-–—]\s+", title):
        add(part)
    for token in _CJK_TOKEN_RE.findall(title):
        add(token)
    if artist and title.lower().startswith(artist.lower()):
        add(title[len(artist):])
    return terms[:5], artist, duration


def _lrclib_search(track: str, artist: str | None, duration: float | None) -> list[dict]:
    params: dict[str, str] = {"track_name": track}
    if artist:
        params["artist_name"] = artist
    if duration and duration > 0:
        params["duration"] = str(max(1, round(duration)))
    url = _LRCLIB_ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return payload if isinstance(payload, list) else []


def _lrclib_score(item: dict, title: str, artist: str | None, duration: float | None) -> tuple:
    if not item.get("syncedLyrics"):
        return (1, 9999.0, 1, 1)
    try:
        item_duration = float(item.get("duration") or 0)
    except (TypeError, ValueError):
        item_duration = 0.0
    diff = abs(item_duration - duration) if duration and item_duration else 9999.0
    track = _clean_title(item.get("trackName"))
    name_hit = 0 if track and (track in title or title in track) else 1
    artist_name = str(item.get("artistName") or "")
    artist_hit = 0 if artist and artist.lower() in artist_name.lower() else 1
    return (0, diff, artist_hit, name_hit)


def fetch_external_lyrics(info: dict, settings: Settings) -> dict | None:
    """YouTube 无字幕时，用标题/歌手/时长向 LRCLIB 匹配同步歌词。"""
    if not getattr(settings, "lyrics_external", True):
        return None
    terms, artist, duration = _search_terms(info)
    if not terms:
        return None
    title = _clean_title(info.get("title"))
    best: dict | None = None
    best_score: tuple | None = None
    for track in terms:
        try:
            results = _lrclib_search(track, artist, duration)
        except Exception as exc:
            logger.debug("LRCLIB 搜索失败 track=%r：%s", track, exc)
            continue
        for item in results[:8]:
            if not isinstance(item, dict) or not item.get("syncedLyrics"):
                continue
            score = _lrclib_score(item, title, artist, duration)
            if best_score is None or score < best_score:
                best, best_score = item, score
        if best is not None and best_score is not None and best_score[1] <= 20:
            break
    if best is None:
        return None
    lines = parse_lrc(best.get("syncedLyrics") or "")
    if not lines:
        return None
    return {
        "lines": lines,
        "track": best.get("trackName"),
        "artist": best.get("artistName"),
        "duration": best.get("duration"),
        "external_id": best.get("id"),
    }


def _first_entry(info: dict) -> dict:
    entries = info.get("entries") if isinstance(info, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                return entry
    return info if isinstance(info, dict) else {}


def _available_languages(info: dict) -> tuple[set[str], set[str]]:
    manual = set((info.get("subtitles") or {}).keys())
    auto = set((info.get("automatic_captions") or {}).keys())
    return manual, auto


def _ordered_languages(
    requested: str, manual: set[str], auto: set[str]
) -> list[str]:
    """根据可用字幕语言生成 yt-dlp 的 sub-langs 列表。"""
    available = manual | auto
    if requested and requested.lower() != "auto":
        prefs = [requested]
    else:
        prefs = list(LANG_PRIORITY)

    ordered: list[str] = []

    def add(value: str) -> None:
        if value and value not in ordered:
            ordered.append(value)

    # 先精确匹配，再按基础语言匹配（例如 zh -> zh-Hans）。
    for pref in prefs:
        if pref in available:
            add(pref)
    for pref in prefs:
        base = pref.split("-")[0].lower()
        for language in sorted(available):
            if language.lower().split("-")[0] == base:
                add(language)

    if not ordered:
        # 请求的语言完全不存在时，按全局优先级挑现有字幕，而不是随意取一条。
        for pref in LANG_PRIORITY:
            base = pref.split("-")[0].lower()
            for language in sorted(available):
                if language.lower().split("-")[0] == base:
                    add(language)
    if not ordered:
        # 元数据里没列字幕也交给 yt-dlp 再试一次常见语言。
        ordered.extend(list(LANG_PRIORITY[:5]))
    return ordered[:6]


def _parse_subtitle_file(path: Path, video_id: str) -> tuple[str, str]:
    """从 yt-dlp 输出文件名还原语言与来源。例：id.zh-Hans-auto.vtt。"""
    stem = path.stem
    tail = stem[len(video_id) + 1:] if stem.startswith(video_id + ".") else stem
    source = "auto" if tail.endswith("-auto") else "manual"
    if source == "auto":
        tail = tail[:-5]
    # yt-dlp 有时输出 id.en.zh-Hans.vtt（语言代码带基础语言前缀），只取最后一段。
    tail = tail.split(".")[-1]
    return tail or "unknown", source


def _pick_subtitle_file(files: Iterable[Path], video_id: str, requested: str) -> Path | None:
    candidates = list(files)
    if not candidates:
        return None

    def score(path: Path) -> tuple:
        language, source = _parse_subtitle_file(path, video_id)
        want = (requested or "auto").lower()
        exact = (
            want != "auto"
            and (language.lower() == want or language.lower().startswith(want + "-"))
        )
        if exact:
            lang_rank = 0
        else:
            # 请求语言不可用时仍按中文优先的全局顺序回退。
            try:
                lang_rank = LANG_PRIORITY.index(language) + 1
            except ValueError:
                lang_rank = len(LANG_PRIORITY) + 1
        source_rank = 0 if source == "manual" else 1
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return (lang_rank, source_rank, -size, path.name)

    return min(candidates, key=score)


def _download_vtt(
    target: Target, settings: Settings, video_id: str, langs: list[str], requested: str = "auto"
) -> tuple[str | None, str | None, str | None]:
    """拉取字幕文件，返回 (vtt 文本, 语言, 来源)；无字幕时文本为 None。"""
    backend = resolve_backend(settings)
    opts = _base_opts(settings)
    opts.update(
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "vtt",
            "ignoreerrors": True,
            "noplaylist": True,
        }
    )

    with tempfile.TemporaryDirectory(prefix="music-api-lyrics-") as tmp:
        tmpdir = Path(tmp)
        opts["outtmpl"] = str(tmpdir / "%(id)s.%(language)s.%(ext)s")

        if backend == "python":
            try:
                import yt_dlp

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([target.value])
            except Exception as exc:
                raise DownloadError(f"字幕拉取失败：{exc}") from exc
        else:
            exe = find_cli()
            if not exe:
                raise DependencyMissing("未找到 yt-dlp 命令行，无法获取字幕")
            cmd = [
                exe,
                *_cli_common_args_for_extract(opts),
                "--skip-download",
                "--write-subs",
                "--write-auto-subs",
                "--sub-format", "vtt",
                "--sub-langs", ",".join(langs),
                "-o", str(tmpdir / "%(id)s.%(language)s.%(ext)s"),
                target.value,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=settings.ytdlp_timeout * 4,
                )
            except subprocess.TimeoutExpired as exc:
                raise DownloadError("字幕拉取超时") from exc
            if proc.returncode != 0 and not list(tmpdir.glob(f"{video_id}.*.vtt")):
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
                raise DownloadError("字幕拉取失败：" + " / ".join(detail))

        chosen = _pick_subtitle_file(tmpdir.glob(f"{video_id}.*.vtt"), video_id, requested)
        if chosen is None:
            return None, None, None
        language, source = _parse_subtitle_file(chosen, video_id)
        try:
            return chosen.read_text(encoding="utf-8", errors="replace"), language, source
        except OSError as exc:
            raise DownloadError(f"读取字幕文件失败：{exc}") from exc


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("lines"), list):
            return data
    except (OSError, ValueError):
        pass
    return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except Exception:
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning("歌词缓存写入失败 %s：%s", path, exc)


def _is_fresh(data: dict, settings: Settings) -> bool:
    ttl = settings.lyrics_ttl_days * 86400
    if ttl <= 0:
        return True
    generated = data.get("generated_at") or 0
    try:
        return time.time() - float(generated) <= ttl
    except (TypeError, ValueError):
        return False


def get_lyrics(target: Target, settings: Settings, lang: str = "auto") -> dict:
    """获取指定视频的同步歌词；优先读缓存，未命中时拉取字幕并解析。"""
    requested = (lang or "auto").strip() or "auto"
    video_id = target.video_id
    if not video_id:
        info = _first_entry(extract_info(target, settings))
        video_id = info.get("id")
        if not video_id:
            raise DownloadError("未能解析出视频 ID，无法获取歌词")

    path = cache_path(video_id, requested)
    cached = _read_cache(path)
    if cached is not None and _is_fresh(cached, settings):
        return {**cached, "cache_hit": True, "stale": False}

    try:
        info = _first_entry(extract_info(target, settings))
        resolved_id = info.get("id") or video_id
        manual, auto = _available_languages(info)
        langs = _ordered_languages(requested, manual, auto)
        logger.info(
            "拉取歌词 video=%s requested=%s langs=%s manual=%s auto=%s",
            resolved_id, requested, langs, sorted(manual)[:5], sorted(auto)[:5],
        )
        vtt, language, source = _download_vtt(target, settings, resolved_id, langs, requested)
        lines = parse_vtt(vtt) if vtt else []
        external: dict | None = None
        if not lines:
            try:
                external = fetch_external_lyrics(info, settings)
            except Exception as exc:
                logger.warning("第三方歌词兜底失败 video=%s：%s", resolved_id, exc)
            if external:
                lines = external["lines"]
                language, source = "lrc", "lrclib"
        if source == "lrclib":
            language_label = "外部歌词"
        else:
            language_label = LANG_LABELS.get(language or "", language or "")
        payload = {
            "video_id": resolved_id,
            "title": info.get("title"),
            "requested_lang": requested,
            "language": language,
            "language_label": language_label,
            "source": source,
            "available": bool(lines),
            "line_count": len(lines),
            "generated_at": time.time(),
            "lines": lines,
        }
        if external:
            payload["external"] = {
                "provider": "lrclib",
                "track": external.get("track"),
                "artist": external.get("artist"),
                "duration": external.get("duration"),
                "id": external.get("external_id"),
            }
        if resolved_id != video_id:
            path = cache_path(resolved_id, requested)
        _write_cache(path, payload)
        return {**payload, "cache_hit": False, "stale": False}
    except Exception:
        if cached is not None:
            logger.warning("歌词刷新失败，返回旧缓存：%s", path)
            return {**cached, "cache_hit": True, "stale": True}
        raise
