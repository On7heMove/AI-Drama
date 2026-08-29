# -*- coding: utf-8 -*-
"""app.production.verb_selector：中文动作 -> 英文动词选择（backend 合并补齐）。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

_ZH_EN = [
    (("看", "望", "注视", "盯"), ("look", "gaze")),
    (("走", "迈", "踱", "跑"), ("walk", "step")),
    (("说", "喊", "吼", "轻声", "问"), ("speak", "say")),
    (("拿", "握", "捡", "举"), ("grasp", "hold")),
    (("倒", "跌", "跪", "瘫"), ("fall", "drop")),
    (("挡", "护", "拦"), ("shield", "guard")),
    (("推", "顶", "压"), ("push", "press")),
]


@dataclass(frozen=True)
class VerbChoice:
    verb_en: str
    alt_en: str = ""
    matched_zh: str = ""


def select_verb(action_zh: str) -> VerbChoice:
    action_zh = action_zh or ""
    for zh_group, en_group in _ZH_EN:
        for zh in zh_group:
            if zh in action_zh:
                return VerbChoice(verb_en=en_group[0], alt_en=en_group[1] if len(en_group) > 1 else "",
                                  matched_zh=zh)
    return VerbChoice(verb_en="moves", matched_zh="")
