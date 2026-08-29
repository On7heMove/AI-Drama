# -*- coding: utf-8 -*-
"""app.production.spine_storyline：骨架 -> 故事线（backend 合并补齐）。

storyline_from_spine(plan, brief) -> StoryLine
plan 可为 dict 或 pydantic 对象（SpinePlan）；字段缺失时保守兜底。
"""
from __future__ import annotations

from app.production.schemas import (
    CharacterArc, Line, StoryBrief, StoryLine,
)

_CHANGE_LINE = {
    "group_collaboration": ("协作线", "主线"),
    "protective_constraint": ("保护约束线", "支线"),
    "public_blame": ("归责线", "支线"),
    "identity_reveal": ("身份线", "暗线"),
    "trust": ("信任线", "暗线"),
}


def _g(obj, key, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _items(obj, key):
    v = _g(obj, key, None)
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        return list(v)
    except TypeError:
        return []


def storyline_from_spine(plan, brief: StoryBrief) -> StoryLine:
    theme = _g(plan, "theme") or ""
    premise = _g(plan, "premise") or _g(plan, "premise_text") or brief.synopsis[:120]

    lines: list[Line] = []
    for chg in _items(plan, "required_changes"):
        if isinstance(chg, dict):
            cid = chg.get("change_id", "")
            ctype = chg.get("change_type", "")
            subject = chg.get("subject", "")
            before, after = chg.get("before", ""), chg.get("after", "")
        else:
            cid = getattr(chg, "change_id", "")
            ctype = getattr(chg, "change_type", "")
            subject = getattr(chg, "subject", "")
            before, after = getattr(chg, "before", ""), getattr(chg, "after", "")
        name, kind = _CHANGE_LINE.get(ctype, (ctype or cid, "支线"))
        lines.append(Line(
            name=name, kind=kind, carrier=subject or "",
            start_ep=1,
            summary="%s：%s -> %s" % (cid, before, after),
        ))

    characters: list[CharacterArc] = []
    for ch in _items(plan, "characters"):
        if isinstance(ch, dict):
            characters.append(CharacterArc(
                name=str(ch.get("name") or ch.get("character_id") or ""),
                role=str(ch.get("role") or ""),
                goal=str(ch.get("goal") or ""),
                flaw=str(ch.get("flaw") or ""),
            ))
        elif hasattr(ch, "name"):
            characters.append(CharacterArc(
                name=str(getattr(ch, "name", "") or getattr(ch, "character_id", "")),
                role=str(getattr(ch, "role", "")),
                goal=str(getattr(ch, "goal", "")),
                flaw=str(getattr(ch, "flaw", "")),
            ))

    peaks = []
    for p in _items(plan, "emotional_peaks"):
        if isinstance(p, dict) and p.get("ep"):
            peaks.append({"ep": int(p["ep"]), "type": p.get("type", "")})
        elif hasattr(p, "ep"):
            peaks.append({"ep": int(getattr(p, "ep")), "type": getattr(p, "type", "")})

    return StoryLine(
        brief=brief, theme=theme, premise=premise,
        world_rules=[str(x) for x in _items(plan, "world_rules")],
        lines=lines, characters=characters, emotional_peaks=peaks,
    )
