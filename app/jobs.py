"""异步任务队列：适合首次下载较慢时先拿 job_id，再轮询进度。

同步接口 /api/music 会一直等到下载完成；任务接口把同样的流程放到后台线程池，
前端可展示进度条，完成后用 /api/jobs/{id}/download 取文件。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .config import Settings
from .downloader import AudioResult
from .security import Target

WorkerFn = Callable[[Target, int, Callable[[float, str], None]], AudioResult]


@dataclass
class Job:
    id: str
    status: str  # queued | running | done | error
    params: dict
    created_at: float
    progress: float = 0.0
    message: str = "排队中"
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None
    artifact: AudioResult | None = field(default=None, repr=False)

    def as_dict(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": round(self.progress, 1),
            "message": self.message,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "params": self.params,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self, settings: Settings, worker: WorkerFn):
        self._worker = worker
        self._ttl = settings.job_ttl_sec
        self._executor = ThreadPoolExecutor(
            max_workers=settings.job_workers, thread_name_prefix="music-job"
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, target: Target, bitrate: int) -> Job:
        self.prune()
        job = Job(
            id=uuid.uuid4().hex,
            status="queued",
            params={
                "kind": target.kind,
                "value": target.display,
                "video_id": target.video_id,
                "bitrate": bitrate,
            },
            created_at=time.time(),
        )
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job.id, target, bitrate)
        return job

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                setattr(job, key, value)

    def _run(self, job_id: str, target: Target, bitrate: int) -> None:
        def on_progress(pct: float, message: str) -> None:
            self._update(job_id, progress=pct, message=message)

        self._update(job_id, status="running", message="开始处理", progress=0.0)
        try:
            result = self._worker(target, bitrate, on_progress)
            payload = result.as_dict()
            vid = result.meta.get("id")
            payload["download_url"] = f"/api/jobs/{job_id}/download"
            if vid:
                payload["audio_url"] = f"/api/music?video_id={vid}&bitrate={result.entry.bitrate}"
            self._update(
                job_id,
                status="done",
                progress=100.0,
                message="完成",
                finished_at=time.time(),
                result=payload,
                artifact=result,
            )
        except Exception as exc:  # 统一转成任务错误，不影响线程池
            self._update(
                job_id,
                status="error",
                message="失败",
                error=str(exc),
                finished_at=time.time(),
            )

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]

    def prune(self) -> int:
        cutoff = time.time() - self._ttl
        with self._lock:
            expired = [k for k, j in self._jobs.items() if j.finished_at and j.finished_at < cutoff]
            for key in expired:
                del self._jobs[key]
        return len(expired)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
