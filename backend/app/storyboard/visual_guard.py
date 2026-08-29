"""画面纯视觉 + 物理合理性守卫（2026-08-14 建立）。

标准：画面行只写"可见物"，禁止拟声词/听觉词（声音只进【声音】行）；
动作符合基础物理（如 织物=撕开/裂开，不是 绷断/崩断）。
SD（Seedance）会把画面里的声音词误渲染成意外画面，本守卫用于校验与告警。
"""
from __future__ import annotations

import re

# 听觉感知动词（画面污染，2026-08-21 修订）：仅"听到/听见/传来/响起"是叙事者视角的听觉感知，
# 画面本身不可渲染"听到"这个词。
# 拟声词（砰/咔嚓/轰/嘎吱…）与"X声"名词（脚步声/关门声…）是物体被施加动作后的物理反应、
# 画面的伴生物——真实存在，画面保留（用户指示 2026-08-21：把这些当污染是逻辑错误）。
SOUND_WORDS = (
    "听到", "听见", "传来", "响起",
)

# 角色发声/表演反应（与对白等价：画面可渲染张嘴/表情/身体姿态，声音由音效轨承担，禁止剔除）
VOICE_REACT_WORDS = (
    "惨叫", "尖叫", "呐喊", "呻吟", "喘息", "咳嗽", "哭喊", "嚎叫",
    "鸣叫", "叫声", "口哨", "叫出声",
)

# 物理合理性规则：(触发词, 违禁动作, 原因, 建议)
PHYSICS_RULES = [
    (
        ("织物", "布料", "衣料", "衣服", "裙子", "布"),
        ("绷断", "崩断", "咔嚓断", "像绳一样断"),
        "织物是撕裂/裂开，不是绳线那样绷断",
        "用「撕开 / 裂口沿纹理蔓延 / 边缘翻卷出毛边与断丝」",
    ),
]


def find_sound_in_visual(text: str) -> list[str]:
    """返回画面行中出现的拟声词/听觉词。"""
    if not text:
        return []
    return [w for w in SOUND_WORDS if w in text]


def find_physics_violations(text: str) -> list[str]:
    """返回物理不合理提示。"""
    out = []
    for mats, verbs, why, fix in PHYSICS_RULES:
        if any(m in text for m in mats) and any(v in text for v in verbs):
            out.append(f"{why}（触发：{'/'.join(mats)} + {'/'.join(verbs)}）→ {fix}")
    return out


# 比喻/修辞句检测（2026-08-14 段6点评落盘）：画面只写客观物理世界的主体+动作+结果，
# 禁止 像/仿佛/如同/宛如… 这类比喻——SD 会把比喻当字面渲染或误解析。
_METAPHOR_RES = (
    re.compile(r"像[^，。；、！？]{0,12}(一样|一般|似的|般)"),
    re.compile(r"(仿佛|如同|宛如|犹如|好似|恍如|就像|恰如)[^，。；、！？]{0,16}"),
    re.compile(r"[^，。；、！？]{1,10}(般|似的)(地|的|，|。|；)"),
)
# 强比喻名词（直接命中即判；SD 极易把这类形象化名词渲染成实体）
_METAPHOR_NOUNS = ("破布娃娃", "断线风筝", "纸片", "落叶", "提线木偶", "稻草人", "泥塑")


def find_metaphors(text: str) -> list[str]:
    """返回画面行中的比喻/修辞片段（SD 易误解析）。"""
    if not text:
        return []
    hits: list[str] = []
    for r in _METAPHOR_RES:
        m = r.search(text)
        if m and m.group(0) not in hits:
            hits.append(m.group(0))
    for n in _METAPHOR_NOUNS:
        if n in text and n not in hits:
            hits.append(n)
    return hits


def audit(action_zh: str) -> list[str]:
    """综合校验：声音污染 + 物理不合理 + 比喻句。"""
    out = []
    s = find_sound_in_visual(action_zh)
    if s:
        out.append("画面含声音词：" + "、".join(s[:6]))
    out.extend(find_physics_violations(action_zh))
    m = find_metaphors(action_zh)
    if m:
        out.append("画面含比喻句（SD易误解析，请改为客观物理描述：主体+动作+结果）：" + "、".join(m[:4]))
    return out

import re as _re

# 英文骨架化信号：LLM 翻译失败回退本地模板时，Scene 退化为 "X verb."（无细节）或过短文本
_SKELETON_RE = _re.compile(r"^\s*\S+\s+\S+\s*\.", _re.I)


def is_skeleton_en(text: str) -> bool:
    """判断英文 Scene 是否为骨架回退（空/过短/主语+单动词）。"""
    t = (text or "").strip()
    if not t or len(t) < 15:
        return True
    return bool(_SKELETON_RE.match(t))
