# -*- coding: utf-8 -*-
"""app.production.bible_check：世界观/设定 bible 约束检查（backend 合并补齐）。

check_bible_constraints(episodes, bible) -> list[QualityItem]
保守策略：bible 为 None/空 -> 返回 []；仅检查可明确判定的项，不引入启发式误报。
bible 结构（若提供）：{"forbidden": {"facts": [...], "characters": [...]}} 之类。
"""
from __future__ import annotations
from typing import Optional

from app.production.schemas import EpisodeScript, QualityItem


def check_bible_constraints(episodes: list[EpisodeScript],
                            bible: Optional[dict]) -> list[QualityItem]:
    if not bible or not isinstance(bible, dict):
        return []
    items: list[QualityItem] = []
    forbidden = bible.get("forbidden") or {}
    forbidden_facts = forbidden.get("facts") or []
    forbidden_chars = forbidden.get("characters") or []
    for ep in episodes:
        text = _episode_text(ep)
        for f in forbidden_facts:
            if isinstance(f, str) and f and f in text:
                items.append(QualityItem(ep=ep.ep, dimension="bible", passed=False,
                                         severity="fatal",
                                         evidence="第%d集出现 bible 禁止事实：%s" % (ep.ep, f),
                                         suggestion="删除或改写该事实"))
        for c in forbidden_chars:
            if isinstance(c, str) and c and c in text:
                items.append(QualityItem(ep=ep.ep, dimension="bible", passed=False,
                                         severity="fatal",
                                         evidence="第%d集出现 bible 禁止角色：%s" % (ep.ep, c),
                                         suggestion="移除该角色"))
    return items


def _episode_text(ep: EpisodeScript) -> str:
    parts = []
    for scene in ep.scenes:
        parts.extend(a for a in scene.action_blocks if a)
        for d in scene.dialogues:
            if d.line:
                parts.append(d.line)
    return "\n".join(parts)
