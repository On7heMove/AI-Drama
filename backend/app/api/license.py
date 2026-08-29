# -*- coding: utf-8 -*-
"""app.api.license：许可路由（backend 合并补齐，骨架实现）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/license", tags=["license"])


@router.get("/status")
async def status() -> dict:
    return {"licensed": True, "note": "skeleton"}
