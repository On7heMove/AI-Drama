# -*- coding: utf-8 -*-
"""app.services.jobs：异步任务状态（backend 合并补齐）。

进程内内存存储 + 线程安全；字段：id/kind/status/stage/total/done/result/error/created_at。
"""
from __future__ import annotations
import threading
import uuid
import datetime
from typing import Optional

_lock = threading.Lock()
_JOBS: dict[str, dict] = {}

_STATUS = ("pending", "running", "done", "failed")


def create_job(kind: str, total: int = 0, **extra) -> str:
    job_id = "job_%s" % uuid.uuid4().hex[:12]
    with _lock:
        _JOBS[job_id] = {"id": job_id, "kind": kind, "status": "pending", "stage": "",
                         "total": int(total), "done": 0, "result": None, "error": None,
                         "created_at": datetime.datetime.now().isoformat(), **extra}
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        return dict(_JOBS.get(job_id) or {})


def set_running(job_id: str) -> None:
    with _lock:
        if job_id in _JOBS:
            _JOBS[job_id]["status"] = "running"


def update_job(job_id: str, stage: Optional[str] = None, done: Optional[int] = None) -> None:
    with _lock:
        j = _JOBS.get(job_id)
        if not j:
            return
        if stage is not None:
            j["stage"] = stage
        if done is not None:
            j["done"] = int(done)


def finish_job(job_id: str, result: object = None, error: Optional[str] = None) -> None:
    with _lock:
        j = _JOBS.get(job_id)
        if not j:
            return
        j["status"] = "failed" if error else "done"
        j["error"] = error
        if result is not None:
            j["result"] = result


def job_to_dict(job: dict) -> dict:
    return {k: v for k, v in (job or {}).items()}
