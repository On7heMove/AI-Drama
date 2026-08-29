# -*- coding: utf-8 -*-
"""梗概逆推 API（独立业务线：梗概 → 剧本）。不依赖 works/promptgen。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.inverse.service import run_inverse
from app.production.llm_client import DeepSeekClient
from app.services import jobs
from app.paths import runtime_dir

router = APIRouter(prefix="/api/v1/inverse", tags=["inverse"])


class InverseRecord(BaseModel):
    title: str = Field(default="", max_length=100)
    genre: str = ""
    synopsis: str = Field(min_length=1)
    brief: dict = Field(default_factory=dict)
    eps_start: int = Field(default=1, ge=1, le=200)
    eps_end: int = Field(default=8, ge=1, le=200)


class InverseRun(BaseModel):
    title: str = Field(default="", max_length=100)
    genre: str = ""
    synopsis: str = Field(min_length=1)
    brief: dict = Field(default_factory=dict)
    eps_start: int = Field(default=1, ge=1, le=200)
    eps_end: int = Field(default=8, ge=1, le=200)




def _pending_path() -> Path:
    d = runtime_dir() / "inverse"
    d.mkdir(parents=True, exist_ok=True)
    return d / "_pending_input.json"


@router.post("/record")
async def record_input(body: InverseRecord) -> dict:
    """记录用户在页面填写的梗概+必要信息（不启动任务），供对话中逆推读取。"""
    payload = {
        "title": body.title, "genre": body.genre, "synopsis": body.synopsis,
        "brief": body.brief, "eps_start": body.eps_start, "eps_end": body.eps_end,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = _pending_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "recorded", "path": str(path), "chars": len(body.synopsis)}


@router.get("/pending")
async def get_pending() -> dict:
    """读取最近一次记录的逆推输入（供对话/脚本消费）。"""
    path = _pending_path()
    if not path.exists():
        return {"status": "empty"}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "ok"
    return data


@router.post("")
async def run(body: InverseRun) -> dict:
    """创建梗概逆推任务（异步），返回 job_id；GET /api/v1/inverse/{job_id} 轮询。"""
    if body.eps_end < body.eps_start:
        return {"error": "eps_end 必须 >= eps_start", "status": "invalid"}
    job_id = jobs.create_job(kind="inverse", total=(body.eps_end - body.eps_start + 1) + 2)

    async def _worker() -> None:
        jobs.set_running(job_id)
        try:
            result = await run_inverse(
                title=body.title, genre=body.genre, synopsis=body.synopsis,
                brief=body.brief, eps_start=body.eps_start, eps_end=body.eps_end,
                client=DeepSeekClient(),
                progress=lambda stage: jobs.update_job(job_id, stage=stage),
            )
            jobs.finish_job(job_id, result=result)
        except Exception as exc:  # noqa: BLE001
            jobs.finish_job(job_id, error=f"{type(exc).__name__}: {exc}")

    asyncio.create_task(_worker())
    return {"job_id": job_id}


@router.get("/{job_id}")
async def get(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        return {"status": "not_found", "error": "任务不存在"}
    return jobs.job_to_dict(job)
