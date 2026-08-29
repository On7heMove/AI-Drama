"""镜头动机规则包（2026-08-15，配置 config/storyboard/shot_motivation.json）。

镜头动机 = 导演通过呈现什么画面向观众传递什么信息/情绪/衔接理由。
机制：本地确定性推导为主；低置信节拍借用 LLM 提议动机并本地校验后落地；
审查：audit_motivation 校验每镜必有【镜头动机】行，接入合规维度「镜头动机」。

v0.13.1 修复（实测暴君狼王两景后）：
- 场景级上下文（summary/location）只在节拍自身无触发词时弱兜底，不再污染每镜打分
- 平局按 _PRIORITY 优先级取键（情感/结果/揭示 优先于 介绍环境）
- 首镜（index==0）加权 介绍环境+2，末镜加权 连接/转场+1
- 扩充触发词：怨毒/窥视/阴影/冷笑→揭示；望向/视线/盯着→反馈；涌出/拔出/扭转→结果
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "shot_motivation.json"

# 本地推导置信度低于该值时，建议借用 LLM 精修动机
LLM_REFINE_THRESHOLD = 2

# 平局优先级：情感/结果/揭示 优先于 介绍环境（避免通用环境动机吞掉具体镜头）
_PRIORITY = (
    "emotion_pressure", "emotion_haste", "emotion_amplify",
    "info_result", "info_reveal", "info_reaction", "info_relation",
    "grammar_reaction", "grammar_cut",
    "info_introduce_person", "info_introduce_env",
)

# 兜底：按场景类型给动机（无触发词命中时的确定性回退）
_SCENE_FALLBACK = {
    "establishing": ["info_introduce_env", "info_introduce_person"],
    "dialogue": ["grammar_reaction", "info_reaction"],
    "action": ["info_result", "emotion_haste"],
    "emotion": ["emotion_amplify", "info_reaction"],
    "suspense": ["info_reveal", "emotion_pressure"],
    "reveal": ["info_reveal", "info_result"],
    "transition": ["grammar_cut", "info_introduce_env"],
    "fantasy": ["info_reveal", "emotion_amplify"],
}


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class Motivation:
    key: str
    name: str
    goal_zh: str
    goal_en: str
    tool_zh: str
    tool_en: str
    confidence: int = 0
    source: str = "local"          # local / llm
    jl_cut: str = ""               # 可选剪辑提示（L-Cut/J-Cut）
    jl_cut_en: str = ""
    justification_zh: str = ""     # 动机形式学（波德维尔四来源，后台数据）
    justification_en: str = ""


def _taxonomy() -> dict:
    return _data().get("taxonomy", {})


def _trigger_map() -> dict:
    return _data().get("trigger_map", {})


def _tool_map() -> dict:
    return _data().get("tool_map", {})


def _j_l_cut() -> dict:
    return _data().get("j_l_cut", {})


def motivation_formalism() -> dict:
    """波德维尔动机形式学（四来源：写实/叙事/互文/艺术）。"""
    return _data().get("motivation_formalism", {})


def known_justifications() -> list[str]:
    return list(motivation_formalism().get("types", {}).keys())


def _justification_by_key() -> dict:
    return motivation_formalism().get("justification_by_key", {})


def known_keys() -> list[str]:
    return list(_taxonomy().keys())


def _score_text(text: str) -> dict[str, int]:
    """按触发词统计每个动机类型的命中数。"""
    out: dict[str, int] = {}
    for key, triggers in _trigger_map().items():
        n = sum(1 for t in triggers if t and t in text)
        if n:
            out[key] = n
    return out


def derive_motivation(*, summary: str = "", action: str = "", emotion: str = "",
                      scene_type: str = "", has_dialogue: bool = False,
                      index: int = 0, total: int = 1, location: str = "") -> Motivation:
    """本地确定性推导：节拍自身文本为主，场景上下文仅兜底，位置规则加权，平局按优先级。

    - 强信号 = action + emotion（节拍自身），先打分
    - 上下文 = summary + location，仅当强信号无命中时 +1 弱兜底
    - 对话镜：含反馈词 → 反应衔接/展现反馈；否则默认反应衔接 +1
    - 首镜（index==0）介绍环境 +2 / 介绍人物 +1；末镜 连接转场 +1
    """
    strong = " ".join(x for x in (action, emotion) if x)
    context = " ".join(x for x in (summary, location) if x)
    scores = _score_text(strong)
    if not scores:
        for k, v in _score_text(context).items():
            scores[k] = scores.get(k, 0) + 1   # 场景上下文弱兜底（只 +1，不压倒强信号）

    if has_dialogue:
        # 对话镜：节拍自身含反馈词 → 反应衔接/展现反馈；否则默认反应衔接
        if any(t in strong for t in _trigger_map().get("info_reaction", [])):
            scores["grammar_reaction"] = scores.get("grammar_reaction", 0) + 1
            scores["info_reaction"] = scores.get("info_reaction", 0) + 1
        else:
            scores["grammar_reaction"] = scores.get("grammar_reaction", 0) + 1

    if index == 0 and total > 1:
        scores["info_introduce_env"] = scores.get("info_introduce_env", 0) + 2
        scores["info_introduce_person"] = scores.get("info_introduce_person", 0) + 1
    if index == total - 1 and total > 1:
        scores["grammar_cut"] = scores.get("grammar_cut", 0) + 1

    best_key: str | None = None
    best_score = -1
    for k in _PRIORITY:
        v = scores.get(k, 0)
        if v > best_score:
            best_key, best_score = k, v
    if best_key is None or best_score <= 0:
        fallback = _SCENE_FALLBACK.get(scene_type or "", ["info_reaction", "grammar_reaction"])
        best_key, best_score = fallback[0], 1

    tax = _taxonomy().get(best_key, {})
    tool = _tool_map().get(best_key, {})
    m = Motivation(
        key=best_key,
        name=tax.get("name", best_key),
        goal_zh=tax.get("goal_zh", ""),
        goal_en=tax.get("goal_en", ""),
        tool_zh=tool.get("zh", ""),
        tool_en=tool.get("en", ""),
        confidence=best_score,
        source="local",
    )
    _attach_jl_cut(m, strong, has_dialogue)
    _attach_justification(m, best_key)
    return m


def _attach_jl_cut(m: Motivation, text: str, has_dialogue: bool) -> None:
    """J/L-Cut 判定：对话+反应词 → L-Cut（声音先入）；先闻其声类 → J-Cut（画面先入）。"""
    # J/L-Cut 是声音-画面剪辑技法：需有可衔接的声音（对白/旁白），静默段不配
    if not has_dialogue:
        return
    jl = _j_l_cut()
    lc, jc = jl.get("l_cut", {}), jl.get("j_cut", {})
    if any(t in text for t in lc.get("triggers", [])):
        m.jl_cut = lc.get("zh", "")
        m.jl_cut_en = lc.get("en", "")
    elif any(t in text for t in jc.get("triggers", [])):
        m.jl_cut = jc.get("zh", "")
        m.jl_cut_en = jc.get("en", "")


def _attach_justification(m: Motivation, key: str) -> None:
    """波德维尔动机形式学：按动机类型给 正当理由（后台数据，不进提示词）。"""
    jk = _justification_by_key().get(key, "narrative")
    jt = motivation_formalism().get("types", {}).get(jk, {})
    m.justification_zh = jt.get("zh", "")
    m.justification_en = jt.get("en", "")


def motivation_zh(m: Motivation) -> str:
    """渲染【镜头动机】行（中文）。"""
    parts = [f"类型：{m.name}", f"目的：{m.goal_zh}", f"手段：{m.tool_zh}"]
    if m.jl_cut:
        parts.append(f"剪辑：{m.jl_cut}")
    return "；".join(parts)


def motivation_en(m: Motivation) -> str:
    """渲染 Motivation 行（英文）。"""
    parts = [f"goal: {m.goal_en}", f"tool: {m.tool_en}"]
    if m.jl_cut_en:
        parts.append(f"edit: {m.jl_cut_en}")
    return "; ".join(parts)


def audit_shot_motivation(shot) -> list[str]:
    """后台审查（不进提示词）：每镜必须有动机数据且含 目的/手段。"""
    if shot is None:
        return ["镜头动机缺失（后台数据）"]
    m = getattr(shot, "motivation", "") or ""
    if not m:
        return ["镜头动机缺失（后台数据）"]
    if "目的" not in m and "让观众" not in m:
        return ["镜头动机缺少'目的'（后台数据）"]
    return []


def should_llm_refine(m: Motivation) -> bool:
    """低置信（触发词命中不足）时建议借用 LLM 精修动机。"""
    return m.confidence < LLM_REFINE_THRESHOLD


def has_motivation_line(zh: str) -> bool:
    if not zh or "【镜头动机】" not in zh:
        return False
    line = next((l for l in zh.splitlines() if l.startswith("【镜头动机】")), "")
    return bool(line.strip() and len(line.strip()) > len("【镜头动机】"))


def audit_motivation(zh: str) -> list[str]:
    """审查：每镜必须有【镜头动机】且含 目的/手段；命中无动机模式告警。"""
    warns: list[str] = []
    if not has_motivation_line(zh):
        warns.append("缺镜头动机行（每镜必须有【镜头动机】：向观众传达什么信息/情绪/衔接理由）")
    else:
        line = next((l for l in zh.splitlines() if l.startswith("【镜头动机】")), "")
        if "目的" not in line and "让观众" not in line:
            warns.append("镜头动机行缺少'目的'（要写明向观众传达什么）")
    for p in _data().get("review_rules", {}).get("unmotivated_patterns", []):
        if p and p in zh:
            warns.append(f"无动机模式：{p}")
    return warns


# ---------------- LLM 辅助：动机生成 + 本地校验 ----------------
_LLM_SYSTEM = (
    "你是资深影视分镜导演。任务：为单个分镜节拍推导『镜头动机』——导演通过呈现什么画面，"
    "向观众传递什么信息/情绪/衔接理由。只输出 JSON："
    '{"key": "...", "goal_zh": "...", "tool_zh": "...", "goal_en": "...", "tool_en": "..."}。'
    "key 必须是以下之一：" + "、".join(known_keys()) + "。"
    "goal 一句话说明向观众传达什么；tool 一句话给出具体镜头手段（景别/机位/角度/运动/剪辑，可用 J-Cut/L-Cut）。"
    "不要输出 JSON 之外的任何内容。"
)


async def llm_propose_motivation(client, *, summary: str = "", action: str = "",
                                 emotion: str = "", dialogue: str = "",
                                 scene_type: str = "", location: str = "") -> dict:
    """借用 LLM 生成动机提议（DeepSeek），失败抛异常由调用方回退本地。"""
    user = json.dumps({
        "scene_type": scene_type or "",
        "location": location or "",
        "action": action or "",
        "emotion": emotion or "",
        "dialogue": dialogue or "",
        "context": summary or "",
    }, ensure_ascii=False)
    text = await client.chat(_LLM_SYSTEM, user, json_mode=True, max_tokens=800, temperature=0.3)
    return json.loads(text)


def validate_proposal(prop: dict) -> Motivation | None:
    """本地校验 LLM 提议：key 必须在动机类型学内；goal/tool 缺失时用配置兜底。"""
    if not isinstance(prop, dict):
        return None
    key = prop.get("key", "")
    if key not in _taxonomy():
        return None
    tax = _taxonomy()[key]
    tool = _tool_map().get(key, {})
    m = Motivation(
        key=key,
        name=tax.get("name", key),
        goal_zh=prop.get("goal_zh") or tax.get("goal_zh", ""),
        goal_en=prop.get("goal_en") or tax.get("goal_en", ""),
        tool_zh=prop.get("tool_zh") or tool.get("zh", ""),
        tool_en=prop.get("tool_en") or tool.get("en", ""),
        confidence=LLM_REFINE_THRESHOLD + 1,
        source="llm",
    )
    text = " ".join(x for x in (prop.get("goal_zh", ""), prop.get("tool_zh", "")) if x)
    _attach_jl_cut(m, text, has_dialogue=False)
    _attach_justification(m, key)
    return m
