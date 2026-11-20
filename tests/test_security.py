from __future__ import annotations

import pytest

from app.config import get_settings
from app.security import InvalidTarget, build_target, safe_filename

S = get_settings()
VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://m.youtube.com/watch?v={VID}&t=30",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}?t=10",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}",
        f"www.youtube.com/watch?v={VID}",
        f"https://www.youtube-nocookie.com/embed/{VID}",
    ],
)
def test_valid_youtube_urls(url):
    target = build_target(url=url, settings=S)
    assert target.kind == "url"
    assert target.value.startswith("https://")
    assert target.video_id == VID


@pytest.mark.parametrize(
    "url",
    [
        f"http://www.youtube.com/watch?v={VID}",
        "https://evil.example.com/watch?v=" + VID,
        "https://www.youtube.com.evil.example.com/watch?v=" + VID,
        "https://127.0.0.1/watch?v=" + VID,
        f"https://user:pass@www.youtube.com/watch?v={VID}",
        f"https://www.youtube.com:8080/watch?v={VID}",
        f"https://www.youtube.com:notaport/watch?v={VID}",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://www.bilibili.com/video/BV1xx411c7mD",
    ],
)
def test_rejected_urls(url):
    with pytest.raises(InvalidTarget):
        build_target(url=url, settings=S)


def test_extra_allowed_hosts(monkeypatch):
    monkeypatch.setenv("MUSIC_EXTRA_ALLOWED_HOSTS", "media.example.org")
    get_settings.cache_clear()
    try:
        custom = get_settings()
        target = build_target(url="https://media.example.org/audio/1", settings=custom)
        assert target.kind == "url"
    finally:
        monkeypatch.delenv("MUSIC_EXTRA_ALLOWED_HOSTS")
        get_settings.cache_clear()


def test_video_id_target():
    target = build_target(video_id=VID, settings=S)
    assert target.kind == "video_id"
    assert target.value == f"https://www.youtube.com/watch?v={VID}"
    assert target.video_id == VID
    with pytest.raises(InvalidTarget):
        build_target(video_id="short", settings=S)


def test_search_target():
    target = build_target(q="  周杰伦  晴天 ", search_limit=5, settings=S)
    assert target.kind == "search"
    assert target.value == "ytsearch5:周杰伦 晴天"
    assert target.video_id is None
    assert target.display == "周杰伦 晴天"


def test_search_sanitization():
    assert not build_target(q="--help", settings=S).value.startswith("ytsearch1:-")
    assert build_target(q="ytsearch9:abc", settings=S).value == "ytsearch1: ytsearch9:abc"
    with pytest.raises(InvalidTarget):
        build_target(q="   ", settings=S)
    with pytest.raises(InvalidTarget):
        build_target(settings=S)


def test_search_limit_is_clamped():
    target = build_target(q="test", search_limit=999, settings=S)
    assert target.search_limit == S.search_max_limit
    assert target.value.startswith(f"ytsearch{S.search_max_limit}:")


def test_safe_filename():
    assert "/" not in safe_filename('a/b:c*"d|e')
    assert safe_filename("  ...  ") == "audio"
    assert safe_filename("周杰伦 - 晴天").startswith("周杰伦")
