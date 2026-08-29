# -*- coding: utf-8 -*-
"""app.inverse.session：逆推会话持久化（backend 合并补齐）。

create_session(input) -> sid；load_session(sid) -> dict；save_session(sid, dict)。
JSON 文件存储于 runtime_dir()/sessions/。
"""
from __future__ import annotations
import json, os, uuid, datetime

from app.paths import runtime_dir


def _dir() -> str:
    d = os.path.join(str(runtime_dir()), "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def create_session(input_data: dict) -> str:
    sid = uuid.uuid4().hex[:12]
    doc = {"session_id": sid, "created_at": datetime.datetime.now().isoformat(),
           "input": input_data, "beats": [], "spine": {}, "final_draft": {},
           "timing": {}, "skill_notes": {}}
    save_session(sid, doc)
    return sid


def load_session(sid: str) -> dict:
    p = os.path.join(_dir(), "%s.json" % sid)
    if os.path.isfile(p):
        return json.load(open(p, encoding="utf-8"))
    return {}


def save_session(sid: str, doc: dict) -> None:
    p = os.path.join(_dir(), "%s.json" % sid)
    json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
