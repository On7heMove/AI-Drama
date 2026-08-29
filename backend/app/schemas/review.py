# -*- coding: utf-8 -*-
"""app.schemas.review：审核人自定义礼俗（backend 合并补齐）。"""
from pydantic import BaseModel, Field


class CustomsOverride(BaseModel):
    rule_id: str = ""
    text: str = ""
    severity: str = "suggestion"
    note: str = ""
