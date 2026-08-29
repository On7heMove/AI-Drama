"""语义一致性校验（2026-08-18）：校验"内容正确性"而非"格式完备性"。

覆盖已踩过的四类逻辑错误：
1. 角色在场一致性：场景级跪地者须出现在站位跪地主体（伊索尔德丢失→拦截）
2. 角色身份稳定性：隐藏威胁者不得被误判为跪地者（珍妮芙跪地→拦截）
3. 动作时序：结果词（贯穿）不得先于过程词（紧握/刺入）出现（时序倒置→拦截）
4. 空间因果一致：画面写"背后刺入"须同时满足 镜头看到后背 + 威胁在背后（背刺变前刺→拦截）

定位：与 compliance（格式/存在性维度）互补；本模块只校验语义一致性，纯本地规则。
"""
from __future__ import annotations

import re

from app.storyboard.spatial_layout import build_scene_space

# 动作结果词（完成态；过程词应先于它出现）
RESULT_WORDS = ("贯穿", "刺中", "刺进", "穿透", "捅破")
# 动作过程词（紧接着结果的动作；结果词先于它=时序倒置）
PROCESS_WORDS = ("紧握剑柄", "握住剑柄", "举起", "扬起", "没入身体", "刺向")
# 空间因果：画面含背后刺入的标记
BEHIND_STAB_MARKS = ("背后", "身后", "后背")
# 站位须含的镜头/威胁方位标记
BACK_VIEW_MARKS = ("后背", "侧背")
THREAT_BEHIND_MARKS = ("背后方向", "人群侧", "背后（")


def _seg_block(zh: str) -> str:
    for ln in zh.splitlines():
        if ln.startswith("【站位·轴线】"):
            return ln
    return ""


def _visual_text(zh: str) -> str:
    return "\n".join(ln for ln in zh.splitlines()
                      if ln.startswith("【画面") or ln.startswith("【镜头·画面】"))


def _kneeler_names(block: str) -> list[str]:
    m = re.search(r"前景中央：([^，。；]+?)并肩跪", block)
    if not m:
        return []
    return [n.strip() for n in re.split(r"[、,]", m.group(1)) if n.strip()]


def audit_character_presence(scene, zh: str) -> list[str]:
    """角色在场一致性：场景级跪地者应出现在站位行（并肩跪主体，或退避/倒地/离场状态备注）。

    跨段状态链下，跪地者可能已退避/倒地而不再在'并肩跪'主体——只要站位行明确提及其状态
    （如'伊索尔德已起身退避''罗伊娜已倒地'）即算在场一致，不算丢失。
    """
    warns: list[str] = []
    block = _seg_block(zh)
    if not block:
        return warns
    space = build_scene_space(scene)
    if space is None:
        return warns
    for k in space.get("kneelers", []):
        if k and k not in block:
            warns.append(f"角色在场不一致：场景级跪地者「{k}」未出现在站位行（主体或退避/倒地备注，可能丢失）")
    return warns


def audit_role_stability(scene, zh: str) -> list[str]:
    """角色身份稳定性：隐藏威胁者不得出现在跪地主体。"""
    warns: list[str] = []
    block = _seg_block(zh)
    if not block:
        return warns
    space = build_scene_space(scene)
    if space is None:
        return warns
    hidden = space.get("hidden", "")
    if not hidden:
        return warns
    if hidden in _kneeler_names(block):
        warns.append(f"角色身份漂移：隐藏威胁者「{hidden}」被误判为跪地者（应位于人群阴影/背后方向）")
    return warns


def audit_action_timeline(zh: str) -> list[str]:
    """动作时序：结果词先于过程词出现 = 时序倒置。"""
    warns: list[str] = []
    visual = _visual_text(zh)
    if not visual:
        return warns
    r_idx = -1
    for w in RESULT_WORDS:
        i = visual.find(w)
        if i >= 0 and (r_idx < 0 or i < r_idx):
            r_idx = i
    p_idx = -1
    for w in PROCESS_WORDS:
        i = visual.find(w)
        if i >= 0 and (p_idx < 0 or i < p_idx):
            p_idx = i
    if r_idx >= 0 and p_idx >= 0 and r_idx < p_idx:
        warns.append("动作时序倒置：结果词先于过程词出现（先贯穿后补刺入）——过程链应插到结果词之前")
    return warns


def audit_spatial_causality(scene, zh: str) -> list[str]:
    """空间因果一致：画面写背后刺入，站位须有 镜头看到后背 + 威胁在背后。"""
    warns: list[str] = []
    visual = _visual_text(zh)
    block = _seg_block(zh)
    if not visual or not block:
        return warns
    is_behind = any(m in visual for m in BEHIND_STAB_MARKS) and any(
        w in visual for w in ("刺入", "贯穿", "刺中", "捅"))
    if not is_behind:
        return warns
    if not any(m in block for m in BACK_VIEW_MARKS):
        warns.append("空间因果不一致：画面写「背后刺入」但站位未锁「镜头看到后背/侧背」（模型易切正面）")
    if not any(m in block for m in THREAT_BEHIND_MARKS):
        warns.append("空间因果不一致：画面写「背后刺入」但站位未锁「威胁在背后方向（人群侧）」")
    return warns


def audit_semantic(scene, seg, zh: str) -> list[str]:
    """语义一致性总校验（seg 保留给未来镜头级规则）。"""
    out: list[str] = []
    out.extend(audit_character_presence(scene, zh))
    out.extend(audit_role_stability(scene, zh))
    out.extend(audit_action_timeline(zh))
    out.extend(audit_spatial_causality(scene, zh))
    out.extend(audit_chest_tip(zh))
    out.extend(audit_turn_body_lock(zh))
    return out


# 新增（2026-08-18 视频审计落盘）：刺入后受伤/转体逻辑
CHEST_TIP_WORDS = ("胸前透出", "自胸前", "前胸透出")   # 诱导前胸的表述
TURN_MARKS = ("回头", "回望", "转头")
NO_TURN_BODY_MARKS = ("不转体", "身体保持", "身体不动", "只转头", "面朝圣坛")


def audit_chest_tip(zh: str) -> list[str]:
    """背后刺入却写'胸前透出'：诱导SD切正面拍前胸，应删并改'血自背后涌出'。"""
    warns: list[str] = []
    visual = _visual_text(zh)
    if not visual:
        return warns
    is_behind = any(m in visual for m in BEHIND_STAB_MARKS) and any(
        w in visual for w in ("刺入", "贯穿", "刺中", "捅"))
    if not is_behind:
        return warns
    for w in CHEST_TIP_WORDS:
        if w in visual:
            warns.append(f"背后刺入却含诱导前胸表述「{w}」：SD 会为拍'透出'切正面，请改为'剑身没入至护手；血自背后伤口涌出、浸染衣袍后侧'")
            break
    return warns


def audit_turn_body_lock(zh: str) -> list[str]:
    """'回头/回望'必须配'身体保持不动/不转体'：否则SD为表现回头会转体，把背后伤口带到正面。"""
    warns: list[str] = []
    visual = _visual_text(zh)
    if not visual:
        return warns
    has_turn = any(m in visual for m in TURN_MARKS)
    if not has_turn:
        return warns
    if not any(m in visual for m in NO_TURN_BODY_MARKS):
        warns.append("「回头/回望」未锁'身体保持面朝圣坛'：SD 易转体把背后伤口带到正面，请补'头转向背后，身体保持面朝圣坛的跪姿'")
    return warns


def audit_pronoun_ambiguity(zh: str) -> list[str]:
    """指代消解：画面/镜头/站位/状态行不得残留未消解的 她/他/它。

    覆盖已踩过错误：camera_pos「贴脸高度，看她回头」的「她」指代不明（应为罗伊娜）。
    SD 无上下文推理，代词=歧义；对白行（角色台词）允许代词，不查。
    """
    warns: list[str] = []
    for ln in zh.splitlines():
        s = ln.strip()
        if not s:
            continue
        is_content = (s.startswith("【画面") or s.startswith("【镜头") or s.startswith("【站位")
                      or s.startswith("画面：") or s.startswith("镜头：")
                      or s.startswith("[空间]") or s.startswith("[状态]"))
        if not is_content:
            continue
        if any(p in s for p in ("她", "他", "它")):
            warns.append(f"指代歧义：{s[:18]}… 残留代词「她/他/它」——SD 无法推断指代，请替换为该镜明确角色名（如'看她回头'→'看罗伊娜回头'）")
            break
    return warns


# 被动/状态抽象词：画面行必须编译成物理载体（施加者+接触点+被动反应）
# 覆盖已反复踩错："被押入园" 无施加者→SD 退化成独自走进；"狠厉" 无物理表现→表情失控
_PASSIVE_VERBS = ("被押", "被带", "被救", "被吻", "被擒", "被捉", "被俘", "被抓", "被拖", "被推",
                  "被赶", "被护送", "被领", "被锁", "被绑", "被架", "被拽", "被按", "被拉")
# 载体词=施加者/接触点（不含被押者角色名，防"罗伊娜被押"里罗伊娜误判为施加者）
_CARRIER_WORDS = ("守卫", "士兵", "狱卒", "护卫", "随从", "押送", "架着", "按着", "拽着", "拖着",
                  "推着", "锁链", "铁链", "绳索", "绑着", "牵着", "锁在", "手", "臂", "肩", "拥着",
                  "两人", "几人", "一队")


def audit_passive_abstract_carrier(zh: str) -> list[str]:
    """画面/镜头行：'被X'被动抽象词必须带物理载体（施加者/接触点），否则 SD 无法渲染→退化成自主动作。

    已踩错误：'罗伊娜被押入园中' 无押送者→SD 生成罗伊娜独自走进园内。
    修复范式：'两名守卫架着罗伊娜双臂推入园中，她踉跄几步、回头望向合拢的园门'。
    """
    warns: list[str] = []
    for ln in zh.splitlines():
        s = ln.strip()
        if not (s.startswith("【画面") or s.startswith("【镜头") or s.startswith("画面：") or s.startswith("镜头：")):
            continue
        for v in _PASSIVE_VERBS:
            if v not in s:
                continue
            i = s.find(v)
            left = max(0, i - 10)
            right = min(len(s), i + len(v) + 10)
            window = s[left:right]
            if not any(cw in window for cw in _CARRIER_WORDS):
                warns.append(
                    f"被动抽象无物理载体：画面含「{v}」但近旁无施加者/接触点（{window[:24]}…）——SD 无法渲染'被X'，"
                    f"会退化成自主动作。请写 施加者+接触点+被动反应，如'两名守卫架着罗伊娜双臂推入园中，她踉跄回头'"
                )
                break
    return warns
