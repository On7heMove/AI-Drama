# -*- coding: utf-8 -*-
"""app.production.knowledge_inject：编剧方法论知识库注入（backend 合并补齐）。

knowledge_constraints(stage) -> str（可为空串）。
stage ∈ {outline, episode, storyline}；返回确定性约束文本，拼接到用户 prompt。
不含密钥/敏感信息。
"""
from __future__ import annotations

_KNOWLEDGE = {
    "outline": (
        "1. 分集大纲遵循'每集一个明确推进 + 一个未解决问题'；\n"
        "2. 事件之间必须有因果链，禁止无因之果；\n"
        "3. 每集结尾保留钩子（悬念/反转/代价），供下一集承接；\n"
        "4. 角色动机一致：同一角色不得在同一窗口内行为矛盾；\n"
        "5. 伏笔登记项必须在窗口内至少被提及或推进一次。"
    ),
    "episode": (
        "1. 单集剧本以'异常/压力 -> 分歧或风险暴露 -> 被迫应对 -> 有限的协作尝试 -> 留下未解决'为节奏；\n"
        "2. 对白口语化且角色有区分度；动作/情境用全角括号；\n"
        "3. 禁止通过台词直接宣告与状态目标冲突的结论；\n"
        "4. 事件类型与计划约束一致：未完成状态不得写成完成；\n"
        "5. 结尾必须留下可由下一集承接的具体状态。"
    ),
    "storyline": (
        "1. 故事线服务主题：单纯追求效率是错的，带感情的人才完整；\n"
        "2. 主要角色线需推进关系或信念，禁止原地踏步；\n"
        "3. 归责/信任/协作三条线保持独立推进且互相影响；\n"
        "4. 新增事实必须能在前文找到来源，否则列为待确认。"
    ),
}


def knowledge_constraints(stage: str) -> str:
    return _KNOWLEDGE.get(stage, "")
