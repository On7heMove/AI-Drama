# -*- coding: utf-8 -*-
"""app.production.segment_export：分段导出辅助（backend 合并补齐）。

long_dialogue_plan：长对白分段规划。参数不固定，宽松实现；
无调用时可安全返回空规划，不影响导入。
"""
from __future__ import annotations


def long_dialogue_plan(*args, **kwargs):
    """长对白/长段划分规划（骨架：返回空 dict 表示无额外分段）。"""
    return {}
