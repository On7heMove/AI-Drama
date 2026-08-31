# -*- coding: utf-8 -*-
"""app.production.verb_selector：中文动作 -> 英文动词选择（backend 合并补齐）。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

_ZH_EN = [
    (("看", "望", "注视", "盯"), ("look", "gaze"), "观察"),
    (("走", "迈", "踱", "跑"), ("walk", "step"), "移动-常规与速度"),
    (("说", "喊", "吼", "轻声", "问"), ("speak", "say"), "表达"),
    (("拿", "握", "捡", "举"), ("grasp", "hold"), "获取"),
    (("倒", "跌", "跪", "瘫"), ("fall", "drop"), "跌落"),
    (("挡", "护", "拦"), ("shield", "guard"), "防御"),
    (("推", "顶", "压"), ("push", "press"), "推压"),
]


@dataclass(frozen=True)
class VerbChoice:
    verb_en: str
    alt_en: str = ""
    matched_zh: str = ""
    family: str = ""   # 动作族（2026-08-31 补齐：_en_scene_line 依赖 family 选择介词句式）


def select_verb(action_zh: str) -> VerbChoice:
    action_zh = action_zh or ""
    for zh_group, en_group, family in _ZH_EN:
        for zh in zh_group:
            if zh in action_zh:
                return VerbChoice(verb_en=en_group[0], alt_en=en_group[1] if len(en_group) > 1 else "",
                                  matched_zh=zh, family=family)
    return VerbChoice(verb_en="moves", matched_zh="", family="")
