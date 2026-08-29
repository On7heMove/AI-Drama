# -*- coding: utf-8 -*-
"""app.api.production：生产路由（backend 合并补齐，骨架实现）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/production", tags=["production"])


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "production": "skeleton"}
