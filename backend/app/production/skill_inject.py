# -*- coding: utf-8 -*-
"""app.production.skill_inject：方法论技能约束注入（backend 合并补齐）。

skill_constraints(stage, genre) -> str（可空串）。
按阶段（outline/episode/storyline）与题材（genre）返回确定性技能约束文本，
拼接到用户 prompt。不含密钥/敏感信息。
"""
from __future__ import annotations

_STAGE_SKILL = {
    "outline": (
        "方法：以'主题命题 -> 人物困境 -> 分集功能 -> 场景任务'分层推进；\n"
        "每集至少一个可观察行动/信息变化；禁止用旁白概括关键冲突。"
    ),
    "episode": (
        "方法：'异常/压力 -> 分歧或风险暴露 -> 被迫应对 -> 有限的协作尝试 -> 留下未解决'；\n"
        "对白口语化且角色有区分度；动作用全角括号；结尾留承接点。"
    ),
    "storyline": (
        "方法：主线服务于主题；三条副线（归责/信任/协作）独立推进且互相影响；\n"
        "角色动机一致，新增事实必须有前文来源。"
    ),
}

_GENRE_HINTS = {
    "科幻": "保持技术设定的有限解释：不主动展开细节，只呈现可见后果。",
    "情感": "冲突落到人物关系与选择，避免说教。",
    "悬疑": "信息按需释放，保留未解问题到集尾。",
    "喜剧": "用行动与节奏制造反差，减少直白解释。",
}


def skill_constraints(stage: str, genre: str = "") -> str:
    parts = []
    base = _STAGE_SKILL.get(stage)
    if base:
        parts.append(base)
    if genre and genre in _GENRE_HINTS:
        parts.append(_GENRE_HINTS[genre])
    return "\n".join(parts)
