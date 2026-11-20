"""接口层测试：全部 mock 掉网络下载，不依赖外网。"""
from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.cache import CacheEntry
from app.downloader import AudioResult, DownloadError
from app.security import InvalidTarget

VID = "dQw4w9WgXcQ"
FAKE_INFO = {
    "id": VID,
    "title": "测试曲目 - Test Song",
    "channel": "Test Channel",
    "duration": 213,
    "thumbnail": None,
    "webpage_url": f"https://www.youtube.com/watch?v={VID}",
    "description": "",
    "view_count": None,
    "upload_date": None,
}


def fake_result(tmp_path, *, hit: bool = False) -> AudioResult:
    audio = tmp_path / "fake.mp3"
    audio.write_bytes(b"ID3" + bytes(2048))
    entry = CacheEntry(
        key="fake-key",
        video_id=VID,
        bitrate=192,
        codec="mp3",
        filename="fake.mp3",
        size=audio.stat().st_size,
        created_at=time.time(),
        last_access=time.time(),
        meta=dict(FAKE_INFO),
    )
    return AudioResult(entry, dict(FAKE_INFO), hit, "mp3", audio)


@pytest.fixture
def client(tmp_path, monkeypatch):
    result = fake_result(tmp_path)
    monkeypatch.setattr(
        main.downloader, "get_audio", lambda target, bitrate=None, cb=None: result
    )
    monkeypatch.setattr(
        main, "search_videos", lambda target, settings: [FAKE_INFO]
    )
    monkeypatch.setattr(main, "probe", lambda target, settings: dict(FAKE_INFO))
    # 不用 with：避免 lifespan 关闭全局线程池，导致后续任务测试失败
    yield TestClient(main.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["download"]["backend"] in {"python", "cli"}
    assert "ffmpeg_available" in body["download"]


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Music MP3 API" in r.text


def test_search(client):
    r = client.get("/api/search", params={"q": "晴天", "limit": 3})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["results"][0]["id"] == VID


def test_resolve(client):
    r = client.get("/api/resolve", params={"video_id": VID})
    assert r.status_code == 200
    assert r.json()["video"]["title"] == FAKE_INFO["title"]


def test_music_returns_audio(client):
    r = client.get("/api/music", params={"video_id": VID})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["x-cache"] == "MISS"
    assert r.headers["x-video-id"] == VID
    assert len(r.content) > 2000


def test_music_supports_range(client):
    r = client.get("/api/music", params={"video_id": VID}, headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.content == b"ID3\x00"


def test_music_attachment(client):
    r = client.get("/api/music", params={"video_id": VID, "download": "true"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


def test_music_cache_hit_header(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        main.downloader, "get_audio",
        lambda target, bitrate=None, cb=None: fake_result(tmp_path, hit=True),
    )
    r = client.get("/api/music", params={"video_id": VID})
    assert r.headers["x-cache"] == "HIT"


@pytest.mark.parametrize(
    "params,status",
    [
        ({}, 422),
        ({"bitrate": 999}, 422),
        ({"url": "https://evil.example.com/x"}, 422),
        ({"video_id": "bad"}, 422),
    ],
)
def test_invalid_requests(client, params, status):
    assert client.get("/api/music", params=params).status_code == status


def test_download_error_mapping(client, monkeypatch):
    def boom(*args, **kwargs):
        raise DownloadError("模拟解析失败")

    monkeypatch.setattr(main.downloader, "get_audio", boom)
    r = client.get("/api/music", params={"video_id": VID})
    assert r.status_code == 502
    assert "模拟解析失败" in r.json()["detail"]


def test_invalid_target_mapping(client, monkeypatch):
    def boom(*args, **kwargs):
        raise InvalidTarget("非法域名")

    monkeypatch.setattr(main, "build_target", boom)
    r = client.get("/api/music", params={"video_id": VID})
    assert r.status_code == 422


def test_cache_lookup(client):
    r = client.get("/api/cache/lookup", params={"video_id": "aaaaaaaaaaa"})
    assert r.status_code == 200
    assert r.json()["hit"] is False


def test_cache_stats_and_clear(client):
    assert client.get("/api/cache").status_code == 200
    r = client.delete("/api/cache")
    assert r.status_code == 200
    assert "removed" in r.json()


def test_job_flow(client):
    created = client.post("/api/jobs", json={"video_id": VID, "bitrate": 192})
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    detail = None
    for _ in range(60):
        detail = client.get(f"/api/jobs/{job_id}").json()
        if detail["status"] in {"done", "error"}:
            break
        time.sleep(0.05)
    assert detail["status"] == "done", detail
    assert detail["result"]["id"] == VID

    r = client.get(f"/api/jobs/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")


def test_job_not_found(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_api_key_protection(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="s3cr3t"))
    c = TestClient(main.app)
    assert c.get("/api/cache").status_code == 401
    assert c.get("/api/cache", headers={"X-API-Key": "s3cr3t"}).status_code == 200
    assert c.get("/api/cache?api_key=s3cr3t").status_code == 200
