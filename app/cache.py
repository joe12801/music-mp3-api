"""本地音频缓存。

设计要点：
- key = sha1(video_id | bitrate | codec)，同一视频不同码率/编码互不覆盖；
- 索引 index.json 原子写入，进程重启后可恢复缓存；
- 支持 LRU 容量淘汰、滑动 TTL 过期、单条删除与整体清空；
- 相同 key 的并发请求由调用方（downloader）加去重锁，避免重复下载。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Settings

logger = logging.getLogger(__name__)
INDEX_NAME = "index.json"
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".webm", ".wav", ".flac"}


@dataclass
class CacheEntry:
    key: str
    video_id: str
    bitrate: int
    codec: str
    filename: str
    size: int
    created_at: float
    last_access: float
    meta: dict = field(default_factory=dict)

    @property
    def ext(self) -> str:
        return Path(self.filename).suffix.lower()

    def summary(self) -> dict:
        return {
            "key": self.key,
            "video_id": self.video_id,
            "title": self.meta.get("title"),
            "channel": self.meta.get("channel"),
            "duration": self.meta.get("duration"),
            "bitrate": self.bitrate,
            "codec": self.codec,
            "ext": self.ext,
            "size": self.size,
            "created_at": self.created_at,
            "last_access": self.last_access,
            "webpage_url": self.meta.get("webpage_url"),
        }


class CacheManager:
    def __init__(self, settings: Settings, prune_orphans: bool = True):
        self.dir: Path = settings.cache_dir
        self.max_bytes: int = settings.max_cache_bytes
        self.ttl: float = settings.ttl_seconds
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / INDEX_NAME

        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._dirty = False
        self._last_persist = 0.0
        self._load()
        if prune_orphans:
            self.prune_orphans()

    # ---------- 键与路径 ----------
    @staticmethod
    def key_for(video_id: str, bitrate: int, codec: str) -> str:
        raw = f"{video_id}|{bitrate}|{codec}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:20]

    def path_of(self, entry: CacheEntry) -> Path:
        return self.dir / entry.filename

    # ---------- 索引持久化 ----------
    def _load(self) -> None:
        if not self.index_path.is_file():
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("缓存索引损坏，忽略：%s", exc)
            return
        now = time.time()
        with self._lock:
            for raw in data.get("entries", []):
                try:
                    entry = CacheEntry(**raw)
                except TypeError:
                    continue
                if not (self.dir / entry.filename).is_file():
                    continue
                if self.ttl and now - entry.last_access > self.ttl:
                    self._unlink(entry.filename)
                    continue
                self._entries[entry.key] = entry

    def _persist_locked(self) -> None:
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "entries": [asdict(e) for e in self._entries.values()],
        }
        tmp = self.dir / (INDEX_NAME + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.index_path)
            self._dirty = False
            self._last_persist = time.time()
        except OSError as exc:
            logger.warning("写入缓存索引失败（内存缓存仍可用）：%s", exc)

    def _maybe_persist(self) -> None:
        if self._dirty and time.time() - self._last_persist > 5:
            self._persist_locked()

    # ---------- 基础文件操作 ----------
    def _unlink(self, filename: str) -> None:
        try:
            (self.dir / filename).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除缓存文件失败 %s: %s", filename, exc)

    def _drop_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry:
            self._unlink(entry.filename)
            self._dirty = True

    # ---------- 对外接口 ----------
    def get(self, video_id: str, bitrate: int, codec: str) -> CacheEntry | None:
        key = self.key_for(video_id, bitrate, codec)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            path = self.path_of(entry)
            if not path.is_file():
                self._drop_locked(key)
                self._persist_locked()
                return None
            if self.ttl and time.time() - entry.last_access > self.ttl:
                self._drop_locked(key)
                self._persist_locked()
                return None
            entry.last_access = time.time()  # 滑动过期：常听的歌不会被清掉
            self._dirty = True
            self._maybe_persist()
            return entry

    def put(
        self,
        video_id: str,
        bitrate: int,
        codec: str,
        src_path: Path,
        meta: dict | None = None,
    ) -> CacheEntry:
        key = self.key_for(video_id, bitrate, codec)
        ext = Path(src_path).suffix.lower() or ".mp3"
        filename = f"{key}{ext}"
        dest = self.dir / filename
        tmp = self.dir / f".{key}.part"

        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dest)  # 原子落盘，避免读到半截文件
        now = time.time()
        entry = CacheEntry(
            key=key,
            video_id=video_id,
            bitrate=bitrate,
            codec=codec,
            filename=filename,
            size=dest.stat().st_size,
            created_at=now,
            last_access=now,
            meta=dict(meta or {}),
        )
        with self._lock:
            old = self._entries.get(key)
            if old and old.filename != filename:
                self._unlink(old.filename)
            self._entries[key] = entry
            self._enforce_quota_locked(protect_key=key)
            self._persist_locked()
        return entry

    def _enforce_quota_locked(self, protect_key: str) -> None:
        if self.max_bytes <= 0:
            return
        total = sum(e.size for e in self._entries.values())
        while total > self.max_bytes:
            candidates = [e for k, e in self._entries.items() if k != protect_key]
            if not candidates:
                break
            oldest = min(candidates, key=lambda e: e.last_access)
            total -= oldest.size
            self._drop_locked(oldest.key)
            logger.info("缓存超限，淘汰 %s (%s 字节)", oldest.filename, oldest.size)

    def delete(self, *, video_id: str | None = None, key: str | None = None) -> int:
        removed = 0
        with self._lock:
            if key:
                if key in self._entries:
                    self._drop_locked(key)
                    removed = 1
            elif video_id:
                for k in [k for k, e in self._entries.items() if e.video_id == video_id]:
                    self._drop_locked(k)
                    removed += 1
            if removed:
                self._persist_locked()
        return removed

    def clear(self) -> int:
        with self._lock:
            removed = len(self._entries)
            for key in list(self._entries):
                self._drop_locked(key)
            self._persist_locked()
        return removed

    def prune_orphans(self) -> int:
        """清理由崩溃残存的 .part 文件与不在索引中的音频文件。"""
        removed = 0
        with self._lock:
            known = {e.filename for e in self._entries.values()} | {INDEX_NAME, ".gitkeep"}
            for path in self.dir.iterdir():
                if not path.is_file() or path.name in known:
                    continue
                if path.name.endswith(".part") or path.suffix.lower() in AUDIO_EXTS:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
        return removed

    def entries(self) -> list[CacheEntry]:
        with self._lock:
            return sorted(self._entries.values(), key=lambda e: e.last_access, reverse=True)

    def stats(self) -> dict:
        with self._lock:
            items = list(self._entries.values())
            total = sum(e.size for e in items)
            return {
                "enabled": True,
                "directory": str(self.dir),
                "count": len(items),
                "total_bytes": total,
                "total_mb": round(total / 1024 / 1024, 2),
                "limit_mb": round(self.max_bytes / 1024 / 1024, 2) if self.max_bytes else None,
                "ttl_seconds": self.ttl,
                "entries": [e.summary() for e in sorted(items, key=lambda x: x.last_access, reverse=True)],
            }
