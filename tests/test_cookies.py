"""cookie 相关配置与导出格式的离线测试（不读取真实浏览器 cookie，不打印任何值）。"""
from __future__ import annotations

import importlib.util
import pathlib

from app.config import _browser_spec, get_settings
from app.downloader import _base_opts, _browser_spec_cli

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools" / "export_chrome_cookies.py"
spec = importlib.util.spec_from_file_location("export_chrome_cookies", TOOLS)
export_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_mod)


def test_browser_spec_missing_env():
    assert _browser_spec("NO_SUCH_VAR") is None


def test_browser_spec_via_env(monkeypatch):
    monkeypatch.setenv("MUSIC_COOKIES_FROM_BROWSER", "chrome:Profile 12")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.cookies_from_browser == ("chrome", "Profile 12", None, None)
    finally:
        monkeypatch.delenv("MUSIC_COOKIES_FROM_BROWSER")
        get_settings.cache_clear()


def test_browser_spec_all_parts(monkeypatch):
    monkeypatch.setenv("MUSIC_COOKIES_FROM_BROWSER", "firefox:default:keyring1:container1")
    get_settings.cache_clear()
    try:
        assert get_settings().cookies_from_browser == (
            "firefox", "default", "keyring1", "container1"
        )
    finally:
        monkeypatch.delenv("MUSIC_COOKIES_FROM_BROWSER")
        get_settings.cache_clear()


def test_browser_spec_cli_roundtrip():
    assert _browser_spec_cli(("chrome", "Profile 12", None, None)) == "chrome:Profile 12"
    assert _browser_spec_cli(("chrome", None, None, None)) == "chrome"
    assert _browser_spec_cli(None) is None


def test_base_opts_contains_cookie_sources(monkeypatch, tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("MUSIC_COOKIES_FILE", str(cookie_file))
    monkeypatch.setenv("MUSIC_COOKIES_FROM_BROWSER", "chrome:Profile 12")
    get_settings.cache_clear()
    try:
        s = get_settings()
        opts = _base_opts(s)
        snap = pathlib.Path(opts["cookiefile"])
        # yt-dlp 在 close() 会回写 cookiefile 并丢失 HttpOnly 登录态，
        # 因此必须使用隔离的临时副本，主凭证文件保持只读。
        assert snap != cookie_file.resolve()
        assert snap.is_file()
        assert snap.read_bytes() == cookie_file.read_bytes()
        assert "music-mp3-api-cookies" in str(snap)
        assert opts["cookiesfrombrowser"] == ("chrome", "Profile 12", None, None)
    finally:
        monkeypatch.delenv("MUSIC_COOKIES_FILE")
        monkeypatch.delenv("MUSIC_COOKIES_FROM_BROWSER")
        get_settings.cache_clear()


def test_cookie_snapshot_is_isolated(tmp_path):
    from app.downloader import _cookie_snapshot

    src = tmp_path / "cookies.txt"
    src.write_text("ORIGINAL\n", encoding="utf-8")
    snap = _cookie_snapshot(src)
    try:
        assert snap != src and snap.is_file()
        assert snap.read_text(encoding="utf-8") == "ORIGINAL\n"
        snap.write_text("OVERWRITTEN\n", encoding="utf-8")
        assert src.read_text(encoding="utf-8") == "ORIGINAL\n"
    finally:
        snap.unlink(missing_ok=True)


def test_relative_cookie_file_resolves_to_project_root(monkeypatch):
    monkeypatch.setenv("MUSIC_COOKIES_FILE", "./data/cookies.txt")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert pathlib.Path(s.cookies_file).is_absolute()
        assert s.cookies_file.endswith("cookies.txt")
    finally:
        monkeypatch.delenv("MUSIC_COOKIES_FILE")
        get_settings.cache_clear()


def test_write_netscape_format(tmp_path):
    cookies = [
        {"domain": ".youtube.com", "path": "/", "secure": True,
         "expires": 1893456000, "name": "SID", "value": "SECRET_VALUE_A"},
        {"domain": "www.youtube.com", "path": "/", "secure": False,
         "expires": -1, "name": "YSC", "value": "SECRET_VALUE_B", "httpOnly": True},
        {"domain": ".example.com", "path": "/", "secure": False,
         "expires": 1893456000, "name": "OTHER", "value": "SHOULD_NOT_BE_WRITTEN"},
    ]
    selected = [c for c in cookies if export_mod.keep(c["domain"])]
    assert len(selected) == 2
    out = tmp_path / "cookies.txt"
    n = export_mod.write_netscape(selected, out)
    assert n == 2
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Netscape HTTP Cookie File")
    assert "\tSID\tSECRET_VALUE_A" in text
    assert "\tYSC\tSECRET_VALUE_B" in text
    assert "#HttpOnly_www.youtube.com" in text
    assert "SHOULD_NOT_BE_WRITTEN" not in text
    # 会话 cookie 过期时间写 0
    ysc_line = [l for l in text.splitlines() if "\tYSC\t" in l][0]
    assert "\t0\tYSC\t" in ysc_line
