# -*- coding: utf-8 -*-
"""app.production.spine_storyline：骨架 -> 故事线（backend 合并补齐）。

storyline_from_spine(plan, brief) -> StoryLine
从 ChapterPlan 提取主题/前提/四线/人物弧光；字段缺失时保守兜底，不抛异常。
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


def storyline_from_spine(plan: dict, brief: StoryBrief) -> StoryLine:
    plan = plan or {}
    theme = plan.get("theme") or ""
    premise = plan.get("premise") or plan.get("premise_text") or brief.synopsis[:120]

    lines: list[Line] = []
    for chg in plan.get("required_changes") or []:
        cid = chg.get("change_id", "")
        ctype = chg.get("change_type", "")
        name, kind = _CHANGE_LINE.get(ctype, (ctype or cid, "支线"))
        lines.append(Line(
            name=name, kind=kind,
            carrier=chg.get("subject", "") or "",
            start_ep=1,
            summary="%s：%s -> %s" % (cid, chg.get("before", ""), chg.get("after", "")),
        ))

    characters: list[CharacterArc] = []
    for ch in plan.get("characters") or []:
        if isinstance(ch, dict):
            characters.append(CharacterArc(
                name=str(ch.get("name") or ch.get("character_id") or ""),
                role=str(ch.get("role") or ""),
                goal=str(ch.get("goal") or ""),
                flaw=str(ch.get("flaw") or ""),
            ))

    peaks = []
    for p in plan.get("emotional_peaks") or []:
        if isinstance(p, dict) and p.get("ep"):
            peaks.append({"ep": int(p["ep"]), "type": p.get("type", "")})

    return StoryLine(
        brief=brief,
        theme=theme,
        premise=premise,
        world_rules=list(plan.get("world_rules") or []),
        lines=lines,
        characters=characters,
        emotional_peaks=peaks,
    )
