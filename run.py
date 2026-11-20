"""便捷启动：python run.py（自动读取 .env）。"""
from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402

if __name__ == "__main__":
    settings = get_settings()
    print(f"Music MP3 API  ->  http://{settings.host}:{settings.port}  (文档 /docs)")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
