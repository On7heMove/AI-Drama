# -*- coding: utf-8 -*-
"""app.quality.emotion_pacing：情绪节奏门禁 C1-C7 的确定性轻量实现（backend 合并补齐）。

pacing_from_text(text, expected_arc, params) -> list[dict]
保守策略：文本过短/无明确弧线信号 -> 返回 []；仅对可判定的节奏风险给出 suggestion。
"""
from __future__ import annotations
from typing import Optional

_CALM = ("平静", "安静", "坐下", "看", "沉默", "缓缓", "轻声", "恢复")
_TENSE = ("突然", "撞", "吼", "冲", "摔", "巨响", "危险", "逼近", "颤抖", "尖叫", "狂奔")


def pacing_from_text(text: str, expected_arc: Optional[str] = None,
                     params: Optional[dict] = None) -> list[dict]:
    if not text or not text.strip():
        return []
    params = params or {}
    checks: list[dict] = []
    total = len(text)
    calm = sum(1 for w in _CALM if w in text)
    tense = sum(1 for w in _TENSE if w in text)
    # 弧线过短：有效情绪词不足，无法构成可检弧线 -> 保守不报
    if calm + tense < 2:
        return []
    # C4：全篇无紧张信号 -> 提示
    if tense == 0:
        checks.append({"id": "C4", "passed": False,
                       "evidence": "全篇无紧张信号，情绪弧线缺失", "suggestion": "加入冲突/压力信号"})
    # C7：结尾无释放/未解决标记（保守：仅提示）
    tail = text[-80:]
    if not any(w in tail for w in ("沉默", "没有", "明天", "再看", "等", "留下", "走了")):
        checks.append({"id": "C7", "passed": False,
                       "evidence": "结尾缺少可承接的未解决状态", "suggestion": "留一个未解决的问题"})
    return checks
