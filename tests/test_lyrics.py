"""同步歌词：VTT 解析、语言选择与 /api/lyrics 接口测试（不依赖外网）。"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import app.lyrics as lyrics
import app.main as main
from app.config import get_settings
from app.lyrics import (
    _ordered_languages,
    _parse_subtitle_file,
    _search_terms,
    clean_cue_text,
    fetch_external_lyrics,
    parse_lrc,
    parse_timestamp,
    parse_vtt,
)
from app.security import build_target

VID = "dQw4w9WgXcQ"


def test_parse_timestamp_formats():
    assert parse_timestamp("00:01:02.345") == 62.345
    assert parse_timestamp("01:02,5") == 62.5
    assert parse_timestamp("00:00:03") == 3.0
    assert parse_timestamp("bad") == 0.0


def test_clean_cue_text():
    raw = "&lt;合唱&gt; <c.yellow_bold>一起唱</c> {\\an8}\u200b"
    assert clean_cue_text(raw) == "<合唱> 一起唱"


def test_parse_vtt_merges_repeated_rolling_cues():
    vtt = """WEBVTT
Kind: captions
Language: zh-Hans

00:00:01.000 --> 00:00:03.000 align:start position:0%
第一句 &amp; 歌词

00:00:03.000 --> 00:00:04.000
第一句 &amp; 歌词

00:00:05.000 --> 00:00:07.500
第二句 <c>歌词</c>
"""
    lines = parse_vtt(vtt)
    assert len(lines) == 2
    assert lines[0] == {"start": 1.0, "end": 4.0, "text": "第一句 & 歌词"}
    assert lines[1]["text"] == "第二句 歌词"


def test_parse_vtt_collapses_rolling_captions():
    vtt = """WEBVTT

00:00:21.790 --> 00:00:21.800
我们对爱情并不陌生

00:00:21.800 --> 00:00:25.950
我们对爱情并不陌生 。 你我都清楚规则。

00:00:25.950 --> 00:00:25.960
。 你我都清楚规则。
"""
    lines = parse_vtt(vtt)
    assert len(lines) == 1
    assert lines[0]["start"] == 21.79
    assert lines[0]["end"] == 25.96
    assert lines[0]["text"] == "我们对爱情并不陌生 。 你我都清楚规则。"


def test_ordered_languages_prefers_exact_then_base():
    manual = {"en"}
    auto = {"zh-Hans", "zh-Hant"}
    assert _ordered_languages("zh-Hant", manual, auto)[0] == "zh-Hant"
    auto_order = _ordered_languages("auto", manual, auto)
    assert auto_order[0] == "zh-Hans"
    assert "en" in auto_order


def test_parse_subtitle_file_language_and_source():
    from pathlib import Path

    assert _parse_subtitle_file(Path(f"{VID}.zh-Hans-auto.vtt"), VID) == ("zh-Hans", "auto")
    assert _parse_subtitle_file(Path(f"{VID}.en.vtt"), VID) == ("en", "manual")
    assert _parse_subtitle_file(Path(f"{VID}.en.zh-Hans.vtt"), VID) == ("zh-Hans", "manual")


def test_parse_lrc_skips_metadata_and_sets_end_times():
    lrc = """[ar:周笔畅]
[ti:笔记]
[offset:+100]
[00:00.00]作词 : 唐恬
[00:12.42][01:12.42]我看见天空很蓝
[00:18.21]就像你在我身边的温暖
"""
    lines = parse_lrc(lrc)
    assert len(lines) == 3
    assert lines[0]["start"] == 12.52
    assert lines[0]["end"] == 18.31
    assert lines[1]["text"] == "就像你在我身边的温暖"
    assert lines[2]["start"] == 72.52
    assert lines[2]["text"] == "我看见天空很蓝"


def test_search_terms_extracts_track_from_youtube_title():
    info = {
        "title": "周笔畅   笔记（国） ILOVE classicalmusic",
        "duration": 259,
        "channel": "ILOVE classicalmusic",
    }
    terms, artist, duration = _search_terms(info)
    assert terms[0] == "周笔畅 笔记 ILOVE classicalmusic"
    assert "周笔畅" in terms
    assert "笔记" in terms
    assert duration == 259.0


def test_external_lyrics_fallback(monkeypatch):
    settings = get_settings()
    info = {"title": "周笔畅   笔记（国） ILOVE classicalmusic", "duration": 259}
    monkeypatch.setattr(
        lyrics,
        "_lrclib_search",
        lambda track, artist, duration: [
            {
                "id": 30749044,
                "trackName": "笔记",
                "artistName": "周笔畅",
                "duration": 256.0,
                "syncedLyrics": "[00:12.42]我看见天空很蓝\n[00:18.21]就像你在我身边的温暖\n",
            }
        ],
    )
    result = fetch_external_lyrics(info, settings)
    assert result is not None
    assert result["track"] == "笔记"
    assert len(result["lines"]) == 2

    disabled = replace(settings, lyrics_external=False)
    assert fetch_external_lyrics(info, disabled) is None


def test_get_lyrics_uses_external_when_no_subtitles(tmp_path, monkeypatch):
    info = {
        "id": VID,
        "title": "周笔畅 笔记",
        "duration": 259,
        "artist": "周笔畅",
    }
    monkeypatch.setattr(lyrics, "cache_path", lambda vid, lang: tmp_path / "lyrics.json")
    monkeypatch.setattr(lyrics, "extract_info", lambda target, settings: dict(info))
    monkeypatch.setattr(lyrics, "_download_vtt", lambda *args, **kwargs: (None, None, None))
    monkeypatch.setattr(
        lyrics,
        "fetch_external_lyrics",
        lambda info, settings: {
            "lines": [{"start": 1.0, "end": 5.0, "text": "外部歌词"}],
            "track": "笔记",
            "artist": "周笔畅",
            "duration": 256.0,
            "external_id": 30749044,
        },
    )
    target = build_target(video_id=VID, settings=main.settings)
    result = lyrics.get_lyrics(target, main.settings, "auto")
    assert result["source"] == "lrclib"
    assert result["language_label"] == "外部歌词"
    assert result["lines"][0]["text"] == "外部歌词"


def test_lyrics_endpoint(monkeypatch):
    payload = {
        "video_id": VID,
        "requested_lang": "auto",
        "language": "zh-Hans",
        "language_label": "简体中文",
        "source": "auto",
        "available": True,
        "line_count": 1,
        "cache_hit": False,
        "stale": False,
        "lines": [{"start": 1.0, "end": 3.0, "text": "测试歌词"}],
    }
    seen = {}

    def fake_get_lyrics(target, settings, lang="auto"):
        seen["value"] = target.value
        seen["lang"] = lang
        return payload

    monkeypatch.setattr(main, "get_lyrics", fake_get_lyrics)
    client = TestClient(main.app)
    r = client.get("/api/lyrics", params={"video_id": VID})
    assert r.status_code == 200
    assert r.json()["lines"][0]["text"] == "测试歌词"
    assert seen["lang"] == "auto"
    assert VID in seen["value"]

    r2 = client.get("/api/lyrics", params={"video_id": VID, "lang": "ja"})
    assert r2.status_code == 200
    assert seen["lang"] == "ja"


def test_lyrics_endpoint_rejects_invalid_video_id():
    client = TestClient(main.app)
    assert client.get("/api/lyrics", params={"video_id": "bad"}).status_code == 422
