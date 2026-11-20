"""测试环境：把缓存指向 tests/_tmp_cache，避免污染真实缓存目录。"""
from __future__ import annotations

import os
from pathlib import Path

TMP_CACHE = Path(__file__).resolve().parent / "_tmp_cache"


def pytest_configure(config):
    os.environ["MUSIC_CACHE_DIR"] = str(TMP_CACHE)
    os.environ["MUSIC_API_KEY"] = ""
    os.environ["MUSIC_DEMO_UI"] = "true"
    os.environ["MUSIC_YTDLP_BACKEND"] = "python"
