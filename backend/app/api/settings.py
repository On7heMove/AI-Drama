# -*- coding: utf-8 -*-
"""app.api.settings：设置路由（backend 合并补齐，骨架实现）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/info")
async def info() -> dict:
    from app.config import settings
    return {"app_name": settings.app_name}
