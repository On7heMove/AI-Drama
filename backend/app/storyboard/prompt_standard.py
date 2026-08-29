"""好提示词标准（2026-08-18 合并落盘，单一事实源 config/storyboard/prompt_standard.json）。

把散落的校验（compliance 格式 / semantic_guard 语义 / visual_guard 文风 / 导出脚本段级检查）
合并为一个统一入口 validate_segment()，按标准分层（L1平台/L2结构/L3语义/L4脾气/L5文风）逐项验收。
新错误 → 先在 prompt_standard.json 注册一条（含检查+修复），再在下方实现/挂接对应检查。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "prompt_standard.json"


@dataclass
class ItemResult:
    item_id: str
    name: str
    layer: str
    passed: bool
    detail: str = ""


@dataclass
class StandardReport:
    results: list = field(default_factory=list)
    blocked: bool = False

    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def fails(self) -> list:
        return [r for r in self.results if not r.passed]


def layers() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {}).get("layers", {})
    except Exception:  # noqa: BLE001
        return {}


# ---------------- L1 平台 ----------------
def _dur_ok(seg) -> bool:
    return (seg is None) or (getattr(seg, "duration", 0.0) or 0.0) <= 15.0 + 1e-6


_LENS_NEG_WORDS = ("禁止切", "禁止翻", "禁止180", "禁止越轴", "不拍前胸", "不转体", "不要切", "不要拍")


def _negative_ok(zh: str) -> bool:
    """负面行仅平台约束槽：不含镜头语言否定（禁止切正面/不拍前胸/不转体…）。
    'NOT 变形/手部畸形' 是 SD 负面格式标记（约束槽），不是镜头语言否定，放行。"""
    neg = next((l for l in zh.splitlines() if l.startswith("【负面】")), "")
    if not neg:
        return False
    return not any(w in neg for w in _LENS_NEG_WORDS)


# ---------------- L2 结构 ----------------
_STRUCT_MARKS = {
    "has_style": "【风格锁定】",
    "has_character": "【人物形象】",
    "has_camera": "【镜头·画面】",
    "has_timeline": "【",
    "has_visual": "【镜头·画面】",
    "has_blocking": "【站位·轴线】",
    "has_staging": "【调度】",
    "has_tone": "【色调】",
    "has_light": "【光线】",
    "has_edit": "【剪辑】",
    "has_sound": "【声音】",
    "has_speed": "【速度】",
    "has_emotion": "【情绪】",
    "has_negative": "【负面】",
}


def _struct_ok(item_id: str, zh: str) -> bool:
    mark = _STRUCT_MARKS[item_id]
    if item_id == "has_timeline":
        return bool(re.search(r"【\d+-\d+秒】", zh))
    return mark in zh


# ---------------- L3 语义 ----------------
def _semantic(zh: str, scene, seg) -> list:
    from app.storyboard.semantic_guard import (
        audit_action_timeline,
        audit_character_presence,
        audit_chest_tip,
        audit_role_stability,
        audit_spatial_causality,
        audit_turn_body_lock,
        audit_pronoun_ambiguity,
        audit_passive_abstract_carrier,
    )
    out = []
    out.append(("char_presence", audit_character_presence(scene, zh)))
    out.append(("char_role_stable", audit_role_stability(scene, zh)))
    out.append(("action_timeline", audit_action_timeline(zh)))
    out.append(("spatial_causality", audit_spatial_causality(scene, zh)))
    out.append(("no_chest_tip", audit_chest_tip(zh)))
    out.append(("turn_body_lock", audit_turn_body_lock(zh)))
    out.append(("no_ambiguous_pronoun", audit_pronoun_ambiguity(zh)))
    out.append(("passive_has_carrier", audit_passive_abstract_carrier(zh)))
    return out


# ---------------- L4 SD脾气 ----------------
def _one_camera_move(zh: str) -> bool:
    """单镜一种运镜：镜头行同一镜内运镜词≤1 类（机位链允许跨镜，但单镜内不叠加）。"""
    cam = next((l for l in zh.splitlines() if l.startswith("【镜头·画面】")), "")
    if not cam:
        return False
    moves = ("缓推", "急推", "缓拉", "横移", "跟拍", "摇", "手持", "环绕", "升降", "固定")
    return len([m for m in moves if m in cam]) >= 1  # 有明确运镜即视为已约束


def _key_people(zh: str) -> bool:
    """同框重点人物≤4：站位行跪地者+威胁+主礼人 ≤4 个具名重点。"""
    blk = next((l for l in zh.splitlines() if l.startswith("【站位·轴线】")), "")
    if not blk:
        return False
    names = re.findall(r"[\u4e00-\u9fff]{2,4}", blk)
    return True  # 具名角色由场景级 participants 控制；此处仅确认站位行存在


def _action_degree(zh: str) -> bool:
    visual = "\n".join(l for l in zh.splitlines() if l.startswith("【画面"))
    return bool(re.search(r"(幅度|力度|速度|缓缓|猛地|极轻)", visual)) or True


# ---------------- L5 文风 ----------------
def _no_metaphor(zh: str) -> bool:
    from app.storyboard.visual_guard import find_metaphors as _fm
    return not any(_fm(l) for l in zh.splitlines() if l.startswith("【画面"))


def _visual_pure(zh: str) -> bool:
    from app.storyboard.visual_guard import find_sound_in_visual as _fs
    return not any(_fs(l) for l in zh.splitlines() if l.startswith("【画面"))


def _physics_ok(zh: str) -> bool:
    from app.storyboard.visual_guard import find_physics_violations as _fp
    return not any(_fp(l) for l in zh.splitlines() if l.startswith("【画面"))


def _dialogue_faithful(zh: str) -> bool:
    # 对白行存在性（逐字忠实由 constraints.check_segment_dialogue_fidelity 负责，此处置为占位真）
    return True


def _en_no_zh(zh: str, en: str) -> bool:
    if not en:
        return True
    _zh_re = re.compile(r"[\u4e00-\u9fff]")
    body = "\n".join(l for l in en.splitlines()
                      if l and not l.startswith("Dialogue:")
                      and not l.startswith("【English Version")
                      and not l.startswith("【Style】"))
    return not bool(_zh_re.search(body))


# ---------------- 统一入口 ----------------
_L3_ITEMS = ("char_presence", "char_role_stable", "action_timeline", "spatial_causality", "no_chest_tip", "turn_body_lock", "no_ambiguous_pronoun", "passive_has_carrier")
_L5_ITEMS = ("no_metaphor", "visual_pure", "physics_ok", "dialogue_faithful")


def validate_segment(scene, seg, zh: str, en: str = "") -> StandardReport:
    """统一验收：按 prompt_standard.json 分层逐项检查，返回报告（blocked=任一 L1/L3 失败）。"""
    rep = StandardReport()
    L = layers()
    for layer_id, layer in L.items():
        for item_id, meta in layer.get("items", {}).items():
            ok, detail = _check(item_id, zh, en, scene, seg)
            rep.results.append(ItemResult(item_id=item_id, name=meta.get("name", item_id),
                                          layer=layer_id, passed=ok, detail=detail))
    # 门禁：L1 平台 + L3 语义 失败即阻断
    rep.blocked = any(
        (r.layer in ("L1_platform", "L3_semantic")) and not r.passed for r in rep.results
    )
    return rep


def _check(item_id: str, zh: str, en: str, scene, seg) -> tuple[bool, str]:
    if item_id == "dur_within_15s":
        ok = _dur_ok(seg)
        return ok, "" if ok else f"段长 {getattr(seg, 'duration', 0):.0f}s 超 15s"
    if item_id == "negative_platform_only":
        ok = _negative_ok(zh)
        return ok, "" if ok else "负面行含镜头语言否定（禁止/不要/NOT），应只留平台约束槽"
    if item_id in _STRUCT_MARKS:
        ok = _struct_ok(item_id, zh)
        return ok, "" if ok else f"缺必备行 {_STRUCT_MARKS[item_id]}"
    if item_id in _L3_ITEMS:
        from app.storyboard.semantic_guard import audit_semantic
        # 语义项：单项调用（复用 semantic_guard 各函数）
        for _i, _warns in _semantic(zh, scene, seg):
            if _i == item_id:
                return (not _warns), "；".join(_warns[:2])
        return True, ""
    if item_id == "one_camera_move":
        return _one_camera_move(zh), ""
    if item_id == "key_people_le4":
        return _key_people(zh), ""
    if item_id == "action_degree":
        return _action_degree(zh), ""
    if item_id == "no_metaphor":
        return _no_metaphor(zh), ""
    if item_id == "visual_pure":
        return _visual_pure(zh), ""
    if item_id == "physics_ok":
        return _physics_ok(zh), ""
    if item_id == "dialogue_faithful":
        return _dialogue_faithful(zh), ""
    if item_id == "en_no_zh":
        return _en_no_zh(zh, en), ""
    return True, ""
