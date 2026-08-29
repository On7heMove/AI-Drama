"""场景情绪本地推理（2026-08-15，配置 config/storyboard/emotion_infer.json）。

从节拍动作/对白/台词命中共情词推断情绪标签（怨毒/悲痛/震惊/恐惧…）；
本地无命中按场景类型回退；低置信/缺失时借用 LLM 提议并本地校验。
情绪标签为后台数据，与镜头动机一样不进提示词（产品护城河），用于驱动动机推导。
对齐 pacing.json emotion_intensity 与 speed_control 情绪表。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "emotion_infer.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class EmotionInfer:
    emotion: str
    confidence: int = 0
    source: str = "local"          # local / fallback / llm
    matched: str = ""


def llm_refine_threshold() -> int:
    return int(_data().get("llm_refine_threshold", 2))


def known_emotions() -> list[str]:
    return list(_data().get("known_emotions", []))


def validate_emotion(name: str) -> bool:
    """本地校验：情绪名必须在已知情绪表内（LLM 提议也走此校验）。"""
    return bool(name) and name in known_emotions()


def infer_emotion(action: str = "", dialogue: str = "", scene_type: str = "") -> EmotionInfer:
    """本地推理：命中触发词最多者为该拍情绪；无命中按场景类型回退。

    - action/dialogue 为强信号；命中数=置信度
    - 平局取规则表先出现者；无命中 → fallback_by_scene_type / default
    """
    text = " ".join(x for x in (action or "", dialogue or "") if x)
    text_l = text.lower()  # 2026-08-21：英文触发词大小写不敏感（Why/Die/fate）
    best: EmotionInfer | None = None
    for rule in _data().get("rules", []):
        hits = [t for t in rule.get("triggers", []) if t and (t in text or t.lower() in text_l)]
        if hits and (best is None or len(hits) > best.confidence):
            best = EmotionInfer(emotion=rule["emotion"], confidence=len(hits), matched="、".join(hits[:3]))
    if best is not None:
        return best
    fb = _data().get("fallback_by_scene_type", {}).get(scene_type or "", "")
    if fb:
        return EmotionInfer(emotion=fb, confidence=1, source="fallback")
    return EmotionInfer(emotion=_data().get("default_emotion", "中性"), confidence=0, source="fallback")


def should_llm_refine(e: EmotionInfer) -> bool:
    """本地置信度低于阈值或缺失（default/fallback 且无强命中）时建议 LLM 辅助。"""
    if e.source == "llm":
        return False
    if e.confidence >= llm_refine_threshold():
        return False
    return True


# ---------------- LLM 辅助：情绪提议 + 本地校验 ----------------
_LLM_SYSTEM = (
    "你是资深影视分镜导演。任务：为单个分镜节拍推断人物情绪标签。只输出 JSON："
    '{"emotion": "...", "reason_zh": "..."}。'
    "emotion 必须是以下之一：" + "、".join(known_emotions()) + "。"
    "reason_zh 用一句话说明从动作/台词中推断的依据。不要输出 JSON 之外的任何内容。"
)


async def llm_propose_emotion(client, *, action: str = "", dialogue: str = "",
                              scene_type: str = "") -> dict:
    """借用 LLM 提议情绪（DeepSeek），失败抛异常由调用方回退本地。"""
    user = json.dumps({
        "action": action or "",
        "dialogue": dialogue or "",
        "scene_type": scene_type or "",
    }, ensure_ascii=False)
    text = await client.chat(_LLM_SYSTEM, user, json_mode=True, max_tokens=800, temperature=0.3)
    return json.loads(text)


def validate_proposal(prop: dict) -> EmotionInfer | None:
    """本地校验 LLM 提议：emotion 必须在已知情绪表内；否则返回 None 回退本地。"""
    if not isinstance(prop, dict):
        return None
    emo = (prop.get("emotion") or "").strip()
    if not validate_emotion(emo):
        return None
    return EmotionInfer(emotion=emo, confidence=llm_refine_threshold() + 1, source="llm",
                        matched=(prop.get("reason_zh") or "")[:40])
