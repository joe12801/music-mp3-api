"""安全刷新 Chrome 的 YouTube 登录 cookie：原始方式重启 + 打开 YouTube。

背景与约束
----------
Chrome 127+ 的 App-Bound Encryption（v20）把登录 cookie 绑定到**原始 profile 路径**。
给 Chrome 传任何 `--user-data-dir` 变体（8.3 短路径、目录联接、复制的 profile）
都会让它判定环境变化并丢弃解不开的 v20 登录 cookie —— 表现为账号被退出。

因此本脚本：
  * **不传 `--user-data-dir`**（也不传调试端口、不加载扩展），完全按用户平时的
    方式启动 Chrome，登录态 100% 保留；
  * 只做一次优雅重启并打开 youtube.com，让 Chrome 自己刷新短效 cookie
    （`__Secure-1PSIDTS` 等有时效，靠页面请求续期）；
  * 关闭后、启动前，把 cookie 库与 Local State 备份到 data/_profile_backup/，
    并只读诊断登录态 cookie 是否齐全（不打印任何 cookie 值）；
  * 全程不删除、不改写任何 cookie 文件。

用法：
  python tools/refresh_chrome.py
  python tools/refresh_chrome.py --url https://www.youtube.com/ --wait 10

刷新后请在 youtube.com 页面用 Cookie-Editor 导出并复制，再运行：
  python tools/import_cookie_editor.py --from-file data/_clipboard_raw.txt --verify
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import time

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
BACKUP_DIR = BASE_DIR / "data" / "_profile_backup"
LOGIN_COOKIE_HINTS = (
    "SAPISID", "1PSID", "3PSID", "APISID", "HSID", "SSID", "SIDCC",
    "LOGIN_INFO", "1PAPISID", "3PAPISID",
)


def chrome_exe() -> str:
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ):
        if os.path.isfile(p):
            return p
    raise RuntimeError("找不到 chrome.exe")


def user_data_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def current_profile(base: pathlib.Path) -> str:
    try:
        ls = json.loads((base / "Local State").read_text(encoding="utf-8"))
        return ls.get("profile", {}).get("last_used") or "Default"
    except Exception:
        return "Default"


def chrome_count() -> int:
    """tasklist 中文输出是 GBK，不能用 text=True(UTF-8)，否则解码线程会崩。"""
    try:
        raw = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                             capture_output=True, timeout=15).stdout
        text = raw.decode("gbk", errors="replace")
        return sum(1 for line in text.splitlines() if "chrome.exe" in line.lower())
    except Exception:
        return -1


def close_chrome() -> None:
    """优先优雅关闭（让 Chrome 正常 flush cookie 库），必要时才强杀。"""
    print("[refresh] 优雅关闭 Chrome ...")
    subprocess.run(["taskkill", "/IM", "chrome.exe"], capture_output=True, timeout=30)
    for _ in range(6):
        time.sleep(1)
        if chrome_count() == 0:
            break
    if chrome_count() != 0:
        print("[refresh] 仍有残留进程，强制结束 ...")
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=30)
        time.sleep(2)
    print(f"[refresh] 关闭完成，剩余 chrome.exe 进程：{chrome_count()}")


def backup_and_diag(base: pathlib.Path, profile: str) -> None:
    """只读诊断 + 备份：确认登录态齐全，且留一份可恢复的副本。"""
    src = base / profile / "Network" / "Cookies"
    if not src.is_file():
        src = base / profile / "Cookies"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copyfile(src, BACKUP_DIR / "Cookies")
    ls = base / "Local State"
    if ls.is_file():
        shutil.copyfile(ls, BACKUP_DIR / "Local State")
    print(f"[refresh] 已备份 cookie 库与 Local State -> {BACKUP_DIR}")

    tmp = pathlib.Path(tempfile.gettempdir()) / "mkt_cookies_diag.db"
    try:
        shutil.copyfile(src, tmp)
        con = sqlite3.connect(str(tmp))
        cur = con.cursor()
        cur.execute("select count(*) from cookies")
        total = cur.fetchone()[0]
        cur.execute(
            "select name from cookies where host_key like '%youtube%' "
            "or host_key like '%google%'"
        )
        names = {r[0] for r in cur.fetchall()}
        con.close()
        login = sorted(n for n in names if any(h in n for h in LOGIN_COOKIE_HINTS))
        print(f"[refresh] cookie 库：总 {total} 条，YouTube/Google 相关 {len(names)} 种")
        print(f"[refresh] 登录态 cookie：{login or '（未发现，请先在 Chrome 登录 YouTube）'}")
        if not login:
            print("[refresh] 警告：当前没有登录态 cookie，重启不会让它凭空出现", )
    except Exception as exc:
        print(f"[refresh] 诊断失败（不影响重启）：{type(exc).__name__}: {str(exc)[:150]}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def launch_original(exe: str, url: str) -> None:
    """原始方式启动，彻底脱离当前命令会话；不传任何 user-data-dir 变体。

    分两步：先起 Chrome 本体，再把 URL 交给已运行的实例开新标签。避免把 URL
    塞进 ShellExecute 参数造成解析问题，也避免新旧实例启动竞态。
    """
    try:
        os.startfile(exe, "open", "--restore-last-session")
    except Exception:
        subprocess.Popen(
            [exe, "--restore-last-session"],
            creationflags=0x00000008 | 0x00000200,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    time.sleep(4)
    try:
        subprocess.Popen(
            [exe, url],
            creationflags=0x00000008 | 0x00000200,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception:
        try:
            os.startfile(url)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="安全重启 Chrome 并打开 YouTube 刷新 cookie")
    ap.add_argument("--url", default="https://www.youtube.com/")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--wait", type=int, default=10, help="启动后等待页面刷新的秒数")
    ap.add_argument("--no-restart", action="store_true", help="只做备份与诊断，不重启")
    args = ap.parse_args()

    base = user_data_dir()
    profile = args.profile or current_profile(base)
    exe = chrome_exe()
    print(f"[refresh] profile={profile}  user-data-dir=（不传，使用 Chrome 默认）")

    close_chrome()
    backup_and_diag(base, profile)

    if args.no_restart:
        print("[refresh] --no-restart：未重启 Chrome（当前浏览器处于关闭状态，请手动打开）")
        return 0

    print(f"[refresh] 原始方式启动 Chrome 并打开 {args.url} ...")
    launch_original(exe, args.url)
    time.sleep(args.wait)
    print(f"[refresh] 启动完成，当前 chrome.exe 进程：{chrome_count()}")
    print("[refresh] 下一步：在 youtube.com 页面用 Cookie-Editor 导出并复制，"
          "然后运行 tools/import_cookie_editor.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
