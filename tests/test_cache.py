from __future__ import annotations

import time
from dataclasses import replace

import pytest

from app.cache import CacheManager
from app.config import get_settings


def make_cache(tmp_path, *, mb: float = 0, ttl_days: float = 0):
    settings = replace(
        get_settings(), cache_dir=tmp_path, max_cache_mb=mb, cache_ttl_days=ttl_days
    )
    return CacheManager(settings), settings


def make_source(tmp_path, name="src.mp3", size=256):
    path = tmp_path / name
    path.write_bytes(b"ID3" + bytes(size))
    return path


def test_put_get_and_persistence(tmp_path):
    cm, _ = make_cache(tmp_path)
    src = make_source(tmp_path)
    meta = {"title": "晴天", "id": "dQw4w9WgXcQ", "channel": "Test"}
    entry = cm.put("dQw4w9WgXcQ", 192, "mp3", src, meta)

    assert entry.ext == ".mp3"
    assert entry.size == src.stat().st_size
    assert (tmp_path / entry.filename).is_file()

    hit = cm.get("dQw4w9WgXcQ", 192, "mp3")
    assert hit is not None and hit.key == entry.key
    assert cm.path_of(hit).is_file()

    # 重启后索引仍可恢复
    cm2, _ = make_cache(tmp_path)
    assert cm2.get("dQw4w9WgXcQ", 192, "mp3") is not None


def test_bitrate_and_codec_isolation(tmp_path):
    cm, _ = make_cache(tmp_path)
    src = make_source(tmp_path)
    cm.put("dQw4w9WgXcQ", 192, "mp3", src)
    assert cm.get("dQw4w9WgXcQ", 320, "mp3") is None
    assert cm.get("dQw4w9WgXcQ", 192, "native") is None
    assert cm.get("dQw4w9WgXcQ", 192, "mp3") is not None


def test_ttl_expiry(tmp_path):
    cm, _ = make_cache(tmp_path, ttl_days=1)
    entry = cm.put("dQw4w9WgXcQ", 192, "mp3", make_source(tmp_path))
    # 手动把时间拨回，避免测试里 sleep
    entry.last_access = time.time() - 3 * 86400
    cm._persist_locked()
    assert cm.get("dQw4w9WgXcQ", 192, "mp3") is None
    assert not (tmp_path / entry.filename).exists()


def test_lru_eviction(tmp_path):
    cm, _ = make_cache(tmp_path, mb=0.0004)  # ~419 字节
    for i in range(4):
        src = make_source(tmp_path, name=f"s{i}.mp3", size=150)
        cm.put(f"video{i:011d}", 192, "mp3", src)

    stats = cm.stats()
    assert stats["total_bytes"] <= cm.max_bytes
    assert stats["count"] < 4
    # 最后写入的条目受保护，不应被淘汰
    assert cm.get("video00000000003", 192, "mp3") is not None


def test_delete_and_clear(tmp_path):
    cm, _ = make_cache(tmp_path)
    src = make_source(tmp_path)
    cm.put("dQw4w9WgXcQ", 192, "mp3", src)
    cm.put("dQw4w9WgXcQ", 320, "mp3", src)
    assert cm.delete(video_id="dQw4w9WgXcQ") == 2
    assert cm.stats()["count"] == 0

    cm.put("aaaaaaaaaaa", 192, "mp3", src)
    assert cm.clear() == 1
    assert cm.stats()["count"] == 0


def test_prune_orphans(tmp_path):
    cm, _ = make_cache(tmp_path)
    (tmp_path / "leftover.part").write_bytes(b"x")
    (tmp_path / "orphan.mp3").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("keep me")
    assert cm.prune_orphans() == 2
    assert (tmp_path / "notes.txt").exists()
