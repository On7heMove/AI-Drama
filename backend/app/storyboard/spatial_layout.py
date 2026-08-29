"""空间层级布局（2026-08-18，配置 config/storyboard/spatial_layout.json）。

仪式/典礼类场景：把空间关系写死成五层（前景中央跪地者/两侧贵族虚化/后方阴影隐藏威胁/
更后主礼人不抢镜）+ 显式轴侧恒定 + 绝对禁止清单。解决：面对面模板套错、180°越轴、层级混乱。
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "spatial_layout.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


def ceremony_triggers() -> list[str]:
    return _data().get("ceremony_triggers", [])


def hidden_markers() -> list[str]:
    return _data().get("hidden_marker", [])


def officiant_markers() -> list[str]:
    return _data().get("officiant_marker", [])


def is_ceremony(text: str) -> bool:
    return any(t and t in (text or "") for t in ceremony_triggers())


def classify_roles(text: str, participants: list[str]) -> dict[str, str]:
    """把参与者分成 隐藏威胁(hidden)/主礼人(officiant)/跪地者(kneelers)。

    - officiant：名字含角色词（主礼人/神父…）
    - hidden：名字出现在文本、且名字邻近（±12字）有隐藏标记（兜帽/阴影/怨毒/隐…）
    """
    # 主礼人按角色词识别（即使本段文本未提其名，如段1主礼人在段2才登场，仍应归为 officiant 而非跪地者）
    officiant = next((p for p in participants if p and any(m and m in p for m in officiant_markers())), "")
    hidden = ""
    for p in participants:
        if not p or p == officiant or p not in text:
            continue
        i = text.find(p)
        # 窗口截断到分句边界（；。！？），避免跨角色串扰：
        # 若某角色名±8字窗口撞见下一个分句里的"隐/兜帽/阴影"，会被误判为隐藏威胁
        left = max(0, i - 8)
        right = min(len(text), i + len(p) + 8)
        for _sep in ("；", "。", "！", "？"):
            _j = text.rfind(_sep, left, i)
            if _j >= 0:
                left = _j + 1
                break
        for _sep in ("；", "。", "！", "？"):
            _j = text.find(_sep, i + len(p), right)
            if _j >= 0:
                right = _j
                break
        window = text[left:right]
        if any(m and m in window for m in hidden_markers()):
            hidden = p
            break
    kneelers = [p for p in participants if p not in (hidden, officiant) and p in text]
    return {"hidden": hidden, "officiant": officiant, "kneelers": kneelers}


def build_spatial_block(text: str, participants: list[str]) -> dict | None:
    """仪式类场景 → 五层空间站位块（zh/en）+ 轴侧 + 禁止清单；非仪式返回 None。"""
    if not is_ceremony(text):
        return None
    roles = classify_roles(text, participants)
    hidden, officiant = roles["hidden"], roles["officiant"]
    kneelers = roles["kneelers"]
    if not kneelers:
        return None
    t = _data()["template_zh"]
    te = _data()["template_en"]
    kneelers_zh = "、".join(kneelers)
    kneelers_en = ", ".join(kneelers)
    kn_side = _data().get("kneeler_side_zh", "")
    def fill(s, **kw):
        out = s
        for k, v in kw.items():
            out = out.replace("{" + k + "}", v)
        return out
    zh_lines = [
        fill(t["front_center"], kneelers=kneelers_zh, kneelers_side=kn_side),
        t["side_mid"],
        fill(t["back_shadow"], hidden=hidden) if hidden else None,
        fill(t["far_altar"], officiant=officiant) if officiant else None,
        t["axis"],
    ]
    en_lines = [
        fill(te["front_center"], kneelers=kneelers_en, kneelers_side_en="Isolde left, Rowena right, bodies facing right, gaze toward the right-rear shadow"),
        te["side_mid"],
        fill(te["back_shadow"], hidden=hidden) if hidden else None,
        fill(te["far_altar"], officiant=officiant) if officiant else None,
        te["axis"],
    ]
    zh = "；".join(x for x in zh_lines if x)
    en = "; ".join(x for x in en_lines if x)
    forbidden_zh = "；".join(fill(f, hidden=hidden, kneelers=kneelers_zh, officiant=officiant) for f in t["forbidden"])
    forbidden_en = "; ".join(fill(f, hidden=hidden, kneelers=kneelers_en, officiant=officiant) for f in te["forbidden"])
    return {
        "blocking_zh": zh,
        "blocking_en": en,
        "forbidden_zh": forbidden_zh,
        "forbidden_en": forbidden_en,
    }


def audit_spatial(zh: str) -> list[str]:
    """提示词级空间合规：仪式类场景必须含 层级站位+轴侧恒定+禁止清单；缺项告警。"""
    if not is_ceremony(zh):
        return []
    warns = []
    if "前景中央" not in zh or "后方人群阴影" not in zh:
        warns.append("仪式类场景缺五层空间站位（前景中央/后方阴影）")
    if "轴侧" not in zh and "180°越轴" not in zh:
        warns.append("仪式类场景缺轴侧恒定约束")
    if "禁止" not in zh:
        warns.append("仪式类场景缺绝对禁止清单")
    return warns


def build_scene_space(scene) -> dict | None:
    """场景级空间状态（2026-08-18）：用场景全文本+participants 一次推理角色身份与空间因果链，
    跨段共享，避免'段文本缺标记→角色身份漂移'（如段3珍妮芙被误判为跪地者）。

    输出：roles(hidden/officiant/kneelers) + facing(面朝锚点) + back(背对方向)
         + threat_side(威胁方位) + camera_side(镜头侧)。
    """
    if scene is None:
        return None
    acts = "；".join((getattr(b, "action", "") or "") for b in (getattr(scene, "beats", []) or []))
    text = " ".join(x for x in [getattr(scene, "summary", "") or "", getattr(scene, "location", "") or "", acts] if x)
    if not is_ceremony(text):
        return None
    participants = list(dict.fromkeys(getattr(scene, "participants", []) or []))
    roles = classify_roles(text, participants)   # 场景全文本 → 身份稳定
    kneelers = roles.get("kneelers", [])
    if not kneelers:
        return None
    return {
        "roles": roles,
        "hidden": roles.get("hidden", ""),
        "officiant": roles.get("officiant", ""),
        "kneelers": kneelers,
        "facing": "圣坛/主礼人",
        "facing_en": "the altar / the officiant",
        "back": "人群入口侧",
        "back_en": "the crowd / entrance side",
        "threat_side": "跪地者背后（人群侧）",
        "camera_side": "入口侧（殿门内）",
        "camera_side_en": "entrance side (inside the doors)",
    }


def _seg_delta(space: dict, seg) -> str:
    """段级增量：本段动作里威胁从背后方向冲出/刺入等，追加空间说明。"""
    seg_text = "；".join((getattr(b, "action", "") or "") for b in (getattr(seg, "beats", []) or []))
    hidden = space.get("hidden", "")
    out = ""
    if hidden and hidden in seg_text:
        if any(k in seg_text for k in ("冲到", "冲至", "扑到", "靠近")):
            out += f"本段：{hidden}从跪地者背后方向（人群侧）冲出"
        if any(k in seg_text for k in ("刺入", "贯穿", "刺中", "捅")):
            out += "，持剑自背后刺入；镜头保持入口侧，观众看到后背与剑入瞬间"
        if "回头" in seg_text or "回望" in seg_text:
            out += "，被刺者只转头回望（头转向背后，身体保持面朝圣坛的跪姿）"
    return out


def build_llm_spatial_block(scene) -> dict | None:
    """LLM spatial 数据非空 → 生成站位行（角色+位置+朝向），忠实原文不套模板（批次B）。"""
    spatial = [x for x in (getattr(scene, "spatial", None) or []) if isinstance(x, dict)]
    items = [x for x in spatial if str(x.get("character") or "").strip()]
    if not items:
        return None
    parts: list[str] = []
    for it in items:
        char = str(it.get("character") or "").strip()
        pos = str(it.get("position") or "").strip()
        facing = str(it.get("facing") or "").strip()
        if pos and facing:
            parts.append(f"{char}：{pos}，面向{facing}")
        elif pos:
            parts.append(f"{char}：{pos}")
        elif facing:
            parts.append(f"{char}：面向{facing}")
        else:
            parts.append(char)
    blocking_zh = "；".join(parts)
    return {
        "blocking_zh": blocking_zh,
        "blocking_en": "",
        "forbidden_zh": "",
        "forbidden_en": "",
        "space": {"spatial": items},
    }


def build_spatial_block_scene(scene, seg, states: dict | None = None) -> dict | None:
    """仪式类场景 → 场景级空间因果链站位块（跨段一致基础 + 段级增量 + 角色状态快照）。

    spatial 数据非空时优先用 LLM 原文空间信息；模板仅作 spatial 为空时的兜底（批次B）。

    states: 本段开始时 {cid: CharacterState}（来自 beat_events.plan_states，复用前期状态机）。
    已退避/倒地/离场角色不再出现在'并肩跪'主体，改由状态备注明确标注。
    """
    _llm_block = build_llm_spatial_block(scene)
    if _llm_block is not None:
        return _llm_block
    space = build_scene_space(scene)
    if space is None:
        return None
    roles = space["roles"]
    hidden, officiant = space["hidden"], space["officiant"]
    kneelers = space["kneelers"]
    all_kneelers = list(kneelers)  # 原始跪地者名单（含已退避者，供状态备注）
    if states:
        from app.state.character_state import Stance as _Stance
        kneeling_now = [cid for cid in kneelers
                        if cid in states and states[cid].stance == _Stance.KNEELING]
        # 注意：不允许空名单回退——若跪地者全部已退避/倒地，主体就是空（改由状态备注说明）
        kneelers = kneeling_now
    t = _data()["template_zh_scene"]
    te = _data()["template_en_scene"]
    kneelers_zh = "、".join(kneelers)
    kneelers_en = ", ".join(kneelers)

    def fill(s, **kw):
        out = s
        for k, v in kw.items():
            out = out.replace("{" + k + "}", v)
        return out

    if kneelers:
        # 人数感知文案：1 人"跪在"，≥2 人"并肩跪在"（单人"并肩"是病句）
        is_pair = len(kneelers) >= 2
        kneel_phrase = "并肩跪在" if is_pair else "跪在"
        kneel_phrase_en = "kneel side by side on" if is_pair else "kneels on"
        zh_parts = [
            fill(t["front_center"], kneelers=kneelers_zh, kneel_phrase=kneel_phrase,
                 facing=space["facing"], back=space["back"]),
            t["side_mid"],
        ]
        en_parts = [
            fill(te["front_center"], kneelers=kneelers_en, kneel_phrase_en=kneel_phrase_en,
                 facing_en=space["facing_en"], back_en=space["back_en"]),
            te["side_mid"],
        ]
    else:
        # 全部跪地者已退避/倒地：前景中央不再有跪地主体，由状态备注说明（避免空名单'并肩跪'）
        zh_parts = [t["side_mid"]]
        en_parts = [te["side_mid"]]
    if hidden:
        # hidden 位置阶段：仍在人群阴影 → 初始模板；已冲出/倒地 → 省略初始窥视模板（由状态备注输出）
        _hidden_state = states.get(hidden) if states else None
        _h_loc = ""
        if _hidden_state is not None:
            _h_loc = next((e.note for e in _hidden_state.effects if e.type == "位置"), "")
        if _h_loc and "人群阴影" not in _h_loc:
            pass
        else:
            zh_parts.append(fill(t["back_shadow"], hidden=hidden))
            en_parts.append(fill(te["back_shadow"], hidden=hidden))
    if officiant:
        zh_parts.append(fill(t["far_altar"], officiant=officiant))
        en_parts.append(fill(te["far_altar"], officiant=officiant))
    zh_parts.append(fill(t["axis"], camera_side=space["camera_side"]))
    en_parts.append(fill(te["axis"], camera_side_en=space["camera_side_en"]))
    delta = _seg_delta(space, seg)
    if delta:
        zh_parts.append(delta)
    if states:
        from app.storyboard.beat_events import stance_notes as _notes
        notes = _notes(states, kneelers=all_kneelers)
        # hidden（威胁）位置阶段单独标注（不在 kneelers 名单内）
        _h_st = states.get(hidden) if hidden else None
        if _h_st is not None:
            _h_loc = next((e.note for e in _h_st.effects if e.type == "位置"), "")
            if _h_loc:
                notes = (notes + "；" if notes else "") + f"{hidden}{_h_loc}"
        if notes:
            zh_parts.append(notes)
    zh = "；".join(x for x in zh_parts if x)
    en = "; ".join(x for x in en_parts if x)
    forbidden_zh = "；".join(fill(f, hidden=hidden, kneelers=kneelers_zh, officiant=officiant, camera_side=space["camera_side"]) for f in t["forbidden"])
    forbidden_en = "; ".join(fill(f, hidden=hidden, kneelers=kneelers_en, officiant=officiant, camera_side_en=space["camera_side_en"]) for f in te["forbidden"])
    return {
        "blocking_zh": zh,
        "blocking_en": en,
        "forbidden_zh": forbidden_zh,
        "forbidden_en": forbidden_en,
        "space": space,
    }

