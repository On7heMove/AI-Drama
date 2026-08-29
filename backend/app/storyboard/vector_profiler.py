"""20维向量场景分析（vector profiler）：LLM 按契约输出场景向量，本地校验。

对齐 docs/20维向量接入产线二.md（原则轴 Schema 运行态）：
场景上下文包 → LLM 出 20 维向量(11 vector + 9 affinity + confidence) → 本地校验 → vector_render 渲染。

复用：app.production.llm_client.DeepSeekClient（项目真实 LLM）；app.storyboard.vector_render.render_prompt。
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

from app.production.llm_client import DeepSeekClient
from app.storyboard.vector_render import render_prompt

_log = logging.getLogger(__name__)

# ---- 20 维契约（复用 spr_llm_real.py 实测过的 prompt）----
LLM_SYSTEM = (
    "你是影视分镜导演。针对给定的剧本镜头场景原文，输出该场景的 20 维分镜向量 JSON。\n"
    "所有数值取 0~1 浮点（valence 例外：-1~1）。\n"
    "【vector 11 维】\n"
    "subject_count 主体数量(0=空镜,1=群像填满画面)\n"
    "conflict_level 冲突强度(0=无冲突,1=生死冲突)\n"
    "tension 紧张度(0=松弛,1=窒息)\n"
    "valence 情绪正负(-1=极负面,0=中性,1=极正面)\n"
    "arousal 生理唤起/刺激度(0=平静,1=极度刺激)\n"
    "pacing_need 节奏需求(0=绵长,1=急促快切)\n"
    "motion_level 画面运动量(0=静止,1=剧烈运动)\n"
    "spatial_openness 空间开放度(0=封闭压抑,1=开阔)\n"
    "novelty 新颖/意外度(0=常规,1=极其意外)\n"
    "danger 危险度(0=安全,1=致命)\n"
    "valence_contrast 情绪对比度(0=单一情绪,1=强烈反差)\n"
    "【affinity 9 维】镜头语法族亲和度(0~1, 最大的那个决定镜头分支)：\n"
    "monologue 独白 / dialogue 对话 / standoff 对峙 / chase 追逐 / intimacy 亲昵 /\n"
    "ceremony 仪式 / combat 战斗 / empty 空镜 / aerial 航拍\n"
    "【confidence】0~1 你对向量判定的置信度。\n"
    "【emotion】本场景主导情绪标签（中文 1-3 个词，如 悬疑/压抑/紧张/愤怒/悲伤/平静/温馨/惊惧，按场景实际情绪给，不要泛化）。\n"
    "【lighting】本场景光线设计（中文 1 句，如 低调冷光、窗外雨光、高对比、霓虹冷蓝，按场景实际给，不要泛化）。\n"
    "【negative】本场景专属负面提示词（中文短语，逗号分隔，如 禁越轴、禁同侧跳、禁穿帮；没有则空字符串）。\n"
    "严格只输出 JSON，格式：{\"vector\": {...11维...}, \"affinity\": {...9维...}, \"confidence\": 0.9, \"emotion\": \"悬疑\", \"lighting\": \"低调冷光\", \"negative\": \"禁越轴\"}"
)

VECTOR_DIMS = ("subject_count", "conflict_level", "tension", "valence", "arousal",
               "pacing_need", "motion_level", "spatial_openness", "novelty", "danger",
               "valence_contrast")
AFFINITY_DIMS = ("monologue", "dialogue", "standoff", "chase", "intimacy",
                 "ceremony", "combat", "empty", "aerial")

# 兜底默认（LLM 失败时）：普通对话场景的均衡向量
DEFAULT_VECTOR = {d: 0.5 for d in VECTOR_DIMS}
DEFAULT_VECTOR["valence"] = 0.0
DEFAULT_AFFINITY = {d: 0.0 for d in AFFINITY_DIMS}
DEFAULT_AFFINITY["dialogue"] = 0.8


def _bounded(x: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(x)
        return default if math.isnan(v) else min(hi, max(lo, v))
    except (TypeError, ValueError):
        return default


def normalize(raw: dict) -> dict:
    """把 LLM 返回的向量夹取到合法范围 + 补缺失维度。"""
    vector = raw.get("vector") or {}
    affinity = raw.get("affinity") or {}
    out_v = {d: _bounded(vector.get(d), 0.0, 1.0, DEFAULT_VECTOR[d])
             for d in VECTOR_DIMS}
    out_v["valence"] = _bounded(vector.get("valence"), -1.0, 1.0, 0.0)
    out_a = {d: _bounded(affinity.get(d), 0.0, 1.0, DEFAULT_AFFINITY[d])
             for d in AFFINITY_DIMS}
    conf = _bounded(raw.get("confidence", 0.8), 0.0, 1.0, 0.8)
    emotion = str(raw.get("emotion", "") or "").strip()
    if not emotion or len(emotion) > 8:
        emotion = "中性"
    lighting = str(raw.get("lighting", "") or "").strip()
    negative = str(raw.get("negative", "") or "").strip()
    return {"vector": out_v, "affinity": out_a, "confidence": conf,
            "emotion": emotion, "lighting": lighting, "negative": negative}


def validate(vector: dict, affinity: dict) -> list[str]:
    """一致性检查（对齐 vector_render 降置信逻辑 + 常识约束）。"""
    issues: list[str] = []
    a = affinity
    v = vector
    if a.get("aerial", 0) > 0.7 and v.get("spatial_openness", 0) < 0.3:
        issues.append("aerial 高但空间开放度低")
    if a.get("chase", 0) > 0.7 and v.get("motion_level", 0) < 0.4:
        issues.append("chase 高但运动量低")
    if a.get("combat", 0) > 0.7 and v.get("conflict_level", 0) < 0.4:
        issues.append("combat 高但冲突强度低")
    if a.get("intimacy", 0) > 0.7 and v.get("valence", 0) < 0:
        issues.append("intimacy 高但情绪为负")
    return issues


async def profile_scene(scene_ctx: str, client: DeepSeekClient | None = None,
                        scene_id: str = "scene") -> dict:
    """场景上下文包 → 20 维向量（LLM 提议 + 本地校验）。失败回退默认向量。"""
    client = client or DeepSeekClient()
    try:
        raw = await client.chat(LLM_SYSTEM, f"场景 {scene_id}：\n{scene_ctx}",
                                json_mode=True, max_tokens=2000, temperature=0.3)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:  # noqa: BLE001  LLM 失败回退默认，不阻塞管线
        _log.warning("vector profiler LLM 失败(%s)，回退默认向量", e)
        data = {}
    norm = normalize(data)
    issues = validate(norm["vector"], norm["affinity"])
    if issues:
        norm["confidence"] = round(norm["confidence"] * 0.7, 2)
        norm["issues"] = issues
    else:
        norm["issues"] = []
    return {"scene_id": scene_id, **norm}


def render_from_profile(profile: dict) -> dict:
    """向量 → 分镜提示词参数包（接 vector_render）。"""
    return render_prompt(profile["vector"], profile["affinity"], profile["confidence"])
