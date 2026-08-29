# -*- coding: utf-8 -*-
"""app.schemas.character：角色画像（backend 合并补齐）。"""
from pydantic import BaseModel, Field


class CharacterProfile(BaseModel):
    name: str = ""
    role: str = ""
    goal: str = ""
    flaw: str = ""
    notes: str = ""
