# -*- coding: utf-8 -*-
"""app.schemas.issues：严重度枚举（backend 合并补齐）。"""
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    STRONG = "strong"
    SUGGESTION = "suggestion"
