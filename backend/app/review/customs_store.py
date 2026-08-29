# -*- coding: utf-8 -*-
"""app.review.customs_store：审核人礼俗存储（backend 合并补齐，内存按 doc_id 隔离）。"""
from __future__ import annotations
import threading

from app.schemas.review import CustomsOverride

_lock = threading.Lock()
_DATA: dict[str, list[CustomsOverride]] = {}


class CustomsStore:
    def list(self, doc_id: str) -> list[CustomsOverride]:
        with _lock:
            return list(_DATA.get(doc_id, []))

    def add(self, doc_id: str, override: CustomsOverride) -> CustomsOverride:
        with _lock:
            _DATA.setdefault(doc_id, []).append(override)
            return override
