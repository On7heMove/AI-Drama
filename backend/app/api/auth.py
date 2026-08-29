# -*- coding: utf-8 -*-
"""app.api.auth：认证路由（backend 合并补齐，骨架实现）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "auth": "skeleton"}
