"""download 辅助逻辑的离线测试（不触网、不调 ffmpeg 可执行文件）。"""
from __future__ import annotations

from app import downloader as d


def test_extractor_args_cli_format():
    args = d._extractor_args_cli(
        {"youtube": {"player_client": "web_safari", "po_token": "abc"}, "generic": {"x": True}}
    )
    assert args[0] == "--extractor-args"
    assert "youtube:player_client=web_safari;po_token=abc" in args
    assert "generic:x=true" in args
    assert d._extractor_args_cli({}) == []


def test_normalize_info_picks_webpage_url():
    info = d.normalize_info(
        {
            "id": "dQw4w9WgXcQ",
            "title": "Song",
            "uploader": "Artist",
            "duration": "213.5",
            "thumbnails": [{"url": "small.jpg"}, {"url": "big.jpg"}],
            "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
    )
    assert info["duration"] == 213
    assert info["channel"] == "Artist"
    assert info["thumbnail"] == "big.jpg"
    assert info["webpage_url"].startswith("https://www.youtube.com/")


def test_normalize_info_falls_back_to_id():
    info = d.normalize_info({"id": "dQw4w9WgXcQ"})
    assert info["webpage_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert info["title"] == "dQw4w9WgXcQ"


def _fake_downloader(tmp_path):
    """不初始化线程池/缓存，只借用 _transcode 方法。"""
    import os

    from app.config import get_settings

    obj = d.Downloader.__new__(d.Downloader)
    obj.settings = get_settings()
    obj._ffmpeg_dir = str(tmp_path)
    (tmp_path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).write_bytes(b"")
    return obj


def test_transcode_builds_correct_ffmpeg_command(tmp_path, monkeypatch):
    """用假 ffmpeg 验证命令参数与码率，不真正转码。"""
    obj = _fake_downloader(tmp_path)
    calls = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / "out.mp3").write_bytes(b"ID3fake")
        return FakeProc()

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    src = tmp_path / "in.m4a"
    src.write_bytes(b"fake")
    dest = tmp_path / "out.mp3"
    result = obj._transcode(src, 256, dest)

    assert result == dest
    assert "-vn" in calls[0]
    assert "-c:a" in calls[0]
    assert "libmp3lame" in calls[0]
    assert "256k" in calls[0]
    assert str(dest) == calls[0][-1]


def test_transcode_failure_raises(tmp_path, monkeypatch):
    obj = _fake_downloader(tmp_path)

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "Unknown encoder 'libmp3lame'\nError while opening encoder"

    monkeypatch.setattr(d.subprocess, "run", lambda cmd, **kw: FakeProc())
    src = tmp_path / "in.m4a"
    src.write_bytes(b"fake")

    try:
        obj._transcode(src, 192, tmp_path / "out.mp3")
    except d.DownloadError as exc:
        assert "转码失败" in str(exc)
    else:
        raise AssertionError("应当抛出 DownloadError")
