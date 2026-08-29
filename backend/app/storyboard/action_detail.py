"""动作细节补全层（2026-08-18，配置 config/storyboard/action_detail.json）。

原理：剧本原文是"信息骨架"（谁对谁做了什么），SD 需要"导演级补全"才能收敛——
持械方式 / 角度 / 刺入部位 / 物理链（刺入→没入→透出）/ 被作用者反应链（出血/僵直/回头）/
旁观者反应 / 时序。补全结果进可见【画面·连续动作】，忠实原文、只增不删。

v0.1：先做透"刺杀/贯穿"模板族；本地命中为主，低置信借 LLM 补全并本地校验。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "action_detail.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class ActionDetail:
    action_type: str
    detail_zh: str
    detail_en: str
    confidence: int = 0
    source: str = "local"          # local / llm
    parts_zh: list[str] = field(default_factory=list)   # [physics过程, victim反应, bystander旁观]
    parts_en: list[str] = field(default_factory=list)


def templates() -> dict:
    return _data().get("templates", {})


def detect_action_type(action: str) -> str | None:
    """命中触发词的动作类型；未命中返回 None（不补全）。"""
    if not action:
        return None
    for key, tpl in templates().items():
        if any(t and t in action for t in tpl.get("triggers", [])):
            return key
    return None


def _extract_target(action: str, subject: str, participants: list[str]) -> str:
    """从动作原文提取被作用者：参与者在动作文本中、且非主体者优先；否则 '对方'。"""
    for p in participants:
        if p and p != subject and p in action:
            return p
    return "对方"


def enrich_action(action: str, subject: str, participants: list[str],
                  emotion: str = "") -> ActionDetail | None:
    """本地补全：命中模板 → 用 主体/目标/旁观者 填充物理链+反应链+旁观反应。

    返回 None 表示无适用模板（保持原样）。
    """
    participants = list(dict.fromkeys(participants or []))  # 去重，避免旁观者重复
    atype = detect_action_type(action)
    if atype is None:
        return None
    tpl = templates()[atype]
    parts = tpl.get("parts", {})
    target = _extract_target(action, subject or "", participants)
    bystanders = [p for p in participants if p and p != subject and p != target]
    bs_zh = "、".join(bystanders[:3]) if bystanders else ""
    bs_en = ", ".join(bystanders[:3]) if bystanders else ""
    def fill(s: str, *vals: tuple[str, str]) -> str:
        out = s
        for k, v in vals:
            out = out.replace("{" + k + "}", v)
        return out
    zh_parts = []
    en_parts = []
    for key in ("physics", "victim", "bystander"):
        pz = parts.get(key + "_zh", "")
        pe = parts.get(key + "_en", "")
        if key == "bystander" and not bystanders:
            continue
        zh_parts.append(fill(pz, ("actor", subject or "对方"), ("target", target), ("bystanders", bs_zh)))
        en_parts.append(fill(pe, ("actor", subject or "the attacker"), ("target", target), ("bystanders", bs_en)))
    return ActionDetail(action_type=atype, detail_zh="；".join(zh_parts), detail_en="；".join(en_parts),
                        confidence=1, source="local",
                        parts_zh=list(zh_parts), parts_en=list(en_parts))


def audit_action_detail(zh: str) -> list[str]:
    """物理校验：画面行含 贯穿/刺入 必须带 物理链（透出/没入）+ 血必须写明伤口来源。"""
    warns: list[str] = []
    for ln in zh.splitlines():
        if not ln.startswith("【画面") and not ln.startswith("【画面·连续动作】"):
            continue
        if any(t in ln for t in ("贯穿", "刺入", "刺中", "刺进", "捅")):
            if not any(k in ln for k in ("透出", "没入", "穿透")):
                warns.append("贯穿类动作缺物理链（需 刺入→没入→透出/胸前透出）")
        # 血：仅当出现"流动/浸染"类动词（涌/流/浸/渗/喷/滴/溅）且未写伤口来源时告警；
        # 静态血泊/血迹/血雾不判（它们是既有状态，不需要伤口来源）
        if "血" in ln and any(k in ln for k in ("涌", "流", "浸", "渗", "喷", "滴", "溅")):
            if not any(k in ln for k in ("伤口", "创口", "剑", "刀", "刺", "荆棘", "扎")):
                warns.append("流动的血未写明来源（伤口/创口/剑/刀/刺）")
    return warns


def should_llm_refine(action: str) -> bool:
    """本地无模板命中时建议借 LLM 补全。"""
    return detect_action_type(action) is None


# ---------------- LLM 辅助：动作细节补全 + 本地校验 ----------------
_LLM_SYSTEM = (
    "你是资深动作片分镜导演。任务：为剧本节拍补全'动作细节'，让 AI 视频模型能收敛。"
    "输出 JSON：{\"action_type\": \"...\", \"detail_zh\": \"...\", \"detail_en\": \"...\"}。"
    "action_type 必须是：" + "、".join(templates().keys()) + " 或 other。"
    "detail_zh 必须写清：持械方式/角度/刺入部位/物理链（刺入→没入→透出）/被作用者反应链（出血/僵直/回头）"
    "；有旁观者时加旁观反应。detail_en 为等价英文。不要输出 JSON 之外内容。"
)


async def llm_propose_detail(client, *, action: str, subject: str = "",
                             target: str = "", participants: list[str] | None = None) -> dict:
    """借用 LLM 提议动作细节（DeepSeek），失败抛异常由调用方回退。"""
    user = json.dumps({
        "action": action or "",
        "subject": subject or "",
        "target": target or "",
        "participants": participants or [],
    }, ensure_ascii=False)
    text = await client.chat(_LLM_SYSTEM, user, json_mode=True, max_tokens=900, temperature=0.3)
    return json.loads(text)


def validate_proposal(prop: dict) -> ActionDetail | None:
    """本地校验 LLM 提议：贯穿类必须含 透出/没入 物理链，且 detail 非空；否则回退。"""
    if not isinstance(prop, dict):
        return None
    dz = (prop.get("detail_zh") or "").strip()
    de = (prop.get("detail_en") or "").strip()
    if not dz or not de:
        return None
    if "贯穿" in dz or "刺入" in dz:
        if not any(k in dz for k in ("透出", "没入", "穿透")):
            return None
    return ActionDetail(action_type=prop.get("action_type") or "other", detail_zh=dz, detail_en=de,
                        confidence=2, source="llm", parts_zh=[dz], parts_en=[de])
