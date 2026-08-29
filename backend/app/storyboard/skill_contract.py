"""视听语言技能契约（25 技能）内置调用：按场景上下文 + 20维向量路由触发技能。

对齐 docs/20维向量接入产线二.md 第 4 步：skill_contracts.call_skill(scene_ctx, vector, slug)
→ 追加 shot_params/建议。技能数据源：books/audiovisual-language/skill_contracts.json。

路由：
1. 文本 trigger 命中（skill description 的关键 trigger 词 ∈ 场景上下文）
2. affinity 主维度路由（dialogue/combat/aerial/... → 对应技能族）
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

CONTRACTS_PATH = data_root() / "books" / "audiovisual-language" / "skill_contracts.json"

# affinity 主维 → 技能族（粗路由）
AFFINITY_SKILLS: dict[str, list[str]] = {
    "dialogue": ["dialogue-camera-system", "dialogue-cutting", "cut-is-a-blink"],
    "monologue": ["voiceover-and-os", "cut-is-a-blink"],
    "standoff": ["confrontation-shot-techniques", "dialogue-camera-system"],
    "chase": ["chase-shot-techniques", "aerial-shot-techniques"],
    "combat": ["fight-shot-techniques", "action-sequence-grammar"],
    "intimacy": ["intimacy-shot-techniques", "close-up-grammar"],
    "ceremony": ["ceremony-shot-techniques", "wide-establishing-grammar"],
    "empty": ["establishing-and-inserts", "visual-progression"],
    "aerial": ["aerial-shot-techniques", "establishing-and-inserts"],
}
# trigger 词 → 技能（文本路由，覆盖 affinity 覆盖不到的具体场景）
TRIGGER_SKILLS: list[tuple[list[str], str]] = [
    (["先闻其声", "J-Cut", "L-Cut", "音画同步", "增值", "声音先于画面"], "audiovisual-added-value"),
    (["静音", "无声", "沉默", "留白"], "silence-and-sound-omission"),
    (["对峙", "僵持", "谁怕谁", "故作镇定", "越轴", "荷兰角"], "confrontation-shot-techniques"),
    (["打斗", "战斗", "拳", "击倒", "借位", "落点"], "fight-shot-techniques"),
    (["情绪曲线", "强度", "视觉复杂度", "进阶"], "visual-progression"),
    (["对白", "正反打", "轴线", "三角机位", "过肩"], "dialogue-camera-system"),
    (["切点", "眨眼", "剪切", "剪辑"], "cut-is-a-blink"),
    (["追逐", "追车", "奔跑", "穿越机", "航拍", "俯冲"], "aerial-shot-techniques"),
    (["耳语", "亲昵", "拥抱", "亲密"], "intimacy-shot-techniques"),
    (["仪式", "婚礼", "典礼"], "ceremony-shot-techniques"),
]


@lru_cache(maxsize=1)
def _contracts() -> dict:
    if not CONTRACTS_PATH.exists():
        return {}
    try:
        return json.loads(CONTRACTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _skill_map() -> dict:
    return {s.get("slug"): s for s in (_contracts().get("skills") or [])}


def route_skills(scene_ctx: str, affinity: dict | None = None) -> list[str]:
    """按文本 trigger + affinity 主维路由，返回命中技能 slug（去重）。"""
    hits: list[str] = []
    for words, slug in TRIGGER_SKILLS:
        if any(w in scene_ctx for w in words):
            hits.append(slug)
    aff = affinity or {}
    if aff:
        top = max(aff, key=lambda k: aff.get(k, 0))
        if aff.get(top, 0) >= 0.6:
            for slug in AFFINITY_SKILLS.get(top, []):
                if slug in _skill_map():
                    hits.append(slug)
    return list(dict.fromkeys(hits))


def call_skill(scene_ctx: str, vector: dict, affinity: dict, slug: str) -> dict | None:
    """单个技能契约调用：返回 {slug, advice, params_mapping, source_book} 或 None（未命中/无契约）。"""
    sk = _skill_map().get(slug)
    if not sk:
        return None
    return {
        "slug": slug,
        "title_zh": sk.get("title_zh", slug),
        "source_book": sk.get("source_book", ""),
        "advice": (sk.get("description") or "")[:200],
        "params_mapping": sk.get("params_mapping", ""),
        "input_fields": sk.get("input_fields", []),
        "output_fields": sk.get("output_fields", []),
    }


def apply_skills(scene_ctx: str, profile: dict) -> dict:
    """场景 → 命中技能建议列表（供渲染层追加）。"""
    aff = profile.get("affinity") or {}
    vector = profile.get("vector") or {}
    slugs = route_skills(scene_ctx, aff)
    return {
        "hit_skills": slugs,
        "advices": [call_skill(scene_ctx, vector, aff, s) for s in slugs if call_skill(scene_ctx, vector, aff, s)],
    }
