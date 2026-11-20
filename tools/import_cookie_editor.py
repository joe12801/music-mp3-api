"""把 Cookie-Editor（或任意扩展）导出的 cookies 转成 Netscape cookies.txt。

为什么需要它：Chrome 127+ 的 App-Bound Encryption（v20）把登录 cookie 绑定到
原 profile 环境。任何"重启 Chrome / 复制 profile / 调试端口"的自动提取都会让
Chrome 判定环境变化并丢弃 v20 登录 cookie（表现为账号被退出）。
唯一稳妥的做法是让**正在运行的浏览器内部**导出明文 cookie。

用法（在 youtube.com 页面用 Cookie-Editor 导出并复制后）：
  1) 复制导出的内容到剪贴板
  2) python tools/import_cookie_editor.py --from-file data/_clipboard_raw.txt --verify

也支持直接粘贴/管道：
  python tools/import_cookie_editor.py --input exported.json --verify

支持三种输入格式：
  - Cookie-Editor JSON（数组，字段 name/value/domain/path/expirationDate/...）
  - Netscape HTTP Cookie File（含 #HttpOnly_ 前缀）
  - Header 字符串（name=value; name2=value2，domain 默认 .youtube.com）

只保留 YouTube/Google 相关域名；不打印任何 cookie 值。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT = BASE_DIR / "data" / "cookies.txt"
KEEP_DOMAIN_KEYWORDS = (
    "youtube", "googlevideo", "ytimg", "google", "gstatic", "googleapis",
    "youtube-nocookie", "doubleclick",
)
LOGIN_COOKIE_HINTS = (
    "SAPISID", "1PSID", "3PSID", "APISID", "HSID", "SSID", "SIDCC",
    "LOGIN_INFO", "1PAPISID", "3PAPISID",
)


def keep(domain: str) -> bool:
    d = (domain or "").lower().lstrip(".")
    return any(k in d for k in KEEP_DOMAIN_KEYWORDS)


def is_login(name: str) -> bool:
    return any(h in (name or "") for h in LOGIN_COOKIE_HINTS)


def parse_json_cookies(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("cookies", "data", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("JSON 顶层不是 cookie 数组")
    out = []
    for c in data:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        domain = (c.get("domain") or "").strip()
        if not domain:
            continue
        # hostOnly=true 的域不带前导点；Cookie-Editor 里 hostOnly 可能与 domain 不一致，
        # 以 hostOnly 为准规范化，避免 yt-dlp 把 cookie 发错域。
        host_only = bool(c.get("hostOnly", not domain.startswith(".")))
        if host_only:
            domain = domain.lstrip(".")
        elif not domain.startswith("."):
            domain = "." + domain
        raw_exp = c.get("expirationDate") or c.get("expires")
        try:
            expires = int(float(raw_exp)) if raw_exp else 0
        except (TypeError, ValueError):
            expires = 0
        if c.get("session"):
            expires = 0
        out.append({
            "domain": domain,
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure")),
            "httpOnly": bool(c.get("httpOnly", c.get("httponly"))),
            "expires": expires,
            "name": str(c["name"]),
            "value": str(c.get("value") or ""),
        })
    return out


def parse_netscape(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
            if len(parts) < 7:
                continue
        domain, flag, path, secure, expires, name = parts[:6]
        value = "\t".join(parts[6:])
        try:
            expires = int(float(expires))
        except ValueError:
            expires = 0
        out.append({
            "domain": domain.strip(),
            "path": path.strip() or "/",
            "secure": secure.strip().upper() == "TRUE",
            "httpOnly": http_only,
            "expires": expires,
            "name": name.strip(),
            "value": value,
        })
    return out


def parse_header_string(text: str) -> list[dict]:
    out = []
    for pair in text.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        out.append({
            "domain": ".youtube.com", "path": "/", "secure": True,
            "httpOnly": False, "expires": 0,
            "name": name.strip(), "value": value.strip(),
        })
    return out


def detect_and_parse(text: str) -> tuple[str, list[dict]]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("输入为空")
    if stripped.startswith("[") or stripped.startswith("{"):
        return "Cookie-Editor JSON", parse_json_cookies(stripped)
    if "Netscape HTTP Cookie File" in text or "\t" in text:
        return "Netscape", parse_netscape(text)
    if "=" in stripped and ";" in stripped or "=" in stripped:
        return "Header 字符串", parse_header_string(stripped)
    raise ValueError("无法识别的输入格式（期望 JSON / Netscape / name=value; ...）")


def write_netscape(cookies: list[dict], out: pathlib.Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by tools/import_cookie_editor.py",
        "# 仅含 YouTube/Google 相关 cookie；请勿提交或分享",
        "",
    ]
    now = time.time()
    for c in cookies:
        domain = c["domain"]
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        prefix = "#HttpOnly_" if c["httpOnly"] else ""
        expires = c["expires"]
        if 0 < expires < now:
            expires = 0
        lines.append(
            f"{prefix}{domain}\t{flag}\t{c['path']}\t"
            f"{'TRUE' if c['secure'] else 'FALSE'}\t{expires}\t{c['name']}\t{c['value']}"
        )
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    return len(lines) - 4


def verify(out: pathlib.Path, video_id: str) -> int:
    import yt_dlp

    # yt-dlp 的 YoutubeDL.close() 会把 cookiejar 回写到 cookiefile，
    # 直接验证主文件会丢失 HttpOnly 登录态，因此只验证临时副本。
    os.environ.pop("NODE_OPTIONS", None)
    fd, tmp = tempfile.mkstemp(prefix="music_api_cookie_verify_", suffix=".txt")
    os.close(fd)
    shutil.copyfile(out, tmp)
    print(f"[verify] 用 {out.name} 的临时副本解析 https://www.youtube.com/watch?v={video_id} ...")
    try:
        with yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "noplaylist": True,
             "cookiefile": tmp, "proxy": "", "socket_timeout": 30,
             "skip_download": True}
        ) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        print(f"[verify] 成功：{info.get('title')} | {info.get('duration')}s")
        return 0
    except Exception as exc:
        print(f"[verify] 解析失败：{str(exc)[:260]}", file=sys.stderr)
        return 1
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="导入 Cookie-Editor 导出的 cookies")
    ap.add_argument("--input", "--from-file", dest="input", default=None,
                    help="导出内容文件；默认读 data/_clipboard_raw.txt，未提供则从 stdin 读")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--video-id", default="9E6b3swbnWg")
    args = ap.parse_args()

    if args.input:
        src = pathlib.Path(args.input).expanduser()
        if not src.is_absolute():
            src = (BASE_DIR / src).resolve()
    else:
        src = BASE_DIR / "data" / "_clipboard_raw.txt"

    text = src.read_text(encoding="utf-8-sig", errors="replace") if src.is_file() else sys.stdin.read()
    try:
        fmt, cookies = detect_and_parse(text)
    except Exception as exc:
        print(f"[import] 解析失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    selected = [c for c in cookies if keep(c["domain"])]
    login = sorted({c["name"] for c in selected if is_login(c["name"])})
    print(f"[import] 格式：{fmt}，共 {len(cookies)} 条，YouTube/Google 相关 {len(selected)} 条")
    print(f"[import] 登录态 cookie：{login or '（未发现）'}")
    if not selected:
        print("[import] 没有 YouTube 相关 cookie；请在 youtube.com 页面打开 Cookie-Editor 再导出", file=sys.stderr)
        return 1
    if not login:
        print("[import] 警告：未发现登录态 cookie，机器人校验可能仍会拦截", file=sys.stderr)

    out = pathlib.Path(args.out).expanduser()
    if not out.is_absolute():
        out = (BASE_DIR / out).resolve()
    n = write_netscape(selected, out)
    print(f"[import] 已写出 {n} 条 -> {out}")
    print(f"[import] 域名：{', '.join(sorted({c['domain'] for c in selected})[:12])}")

    if args.verify:
        return verify(out, args.video_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
