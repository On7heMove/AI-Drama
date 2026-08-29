"""分镜节拍 → 事件流桥接（2026-08-18）：复用前期成熟状态机（app/state/）。

前期已建成：CharacterState(活/意识/姿态/六部位伤害/效果台账) + StateMachine.apply(event) +
EventSourcingStore(事件溯源、跨段快照、Violation)。本模块只做桥接：
把分镜 beat 的 action 文本映射为前期 Event 契约，喂给 EventSourcingStore，
产出每段开始时的角色状态快照，供站位渲染消费（跨段状态连续）。
"""
from __future__ import annotations

import re

from app.schemas.events import Event, EventType
from app.state.character_state import BodyDamage, CharacterState, Consciousness, Stance
from app.state.store import EventSourcingStore

# ---- 动作文本 → EventType 映射（本地规则；覆盖已踩过的状态变化） ----
_RULES: list[tuple[tuple[str, ...], EventType, str | None]] = [
    (("惊惶退避", "退避", "退后两步", "后退", "倒退", "退开"), EventType.RISE, None),  # 退避=起身离开跪位
    (("站起", "起身", "站起来"), EventType.RISE, None),
    (("垂剑而立", "低头看", "凑近", "来到身边"), EventType.ACTION, None),  # 逼近/立于身侧
    (("蹲下", "蹲在", "俯身"), EventType.ACTION, None),  # 蹲下靠近
    (("蜷缩", "卧在", "卧下", "趴伏", "趴"), EventType.FALL, None),  # 动物/倒地卧下
    (("贯穿", "刺入", "刺中", "刺进", "捅"), EventType.INJURY, "torso"),
    (("拔剑", "拔出", "持剑", "握剑", "紧握剑柄"), EventType.WIELD, "right_arm"),
    (("栽倒", "瘫软", "倒地", "扑入血泊", "趴在血泊", "被抛起"), EventType.FALL, None),
    (("僵立", "僵直", "猛地一僵"), EventType.ACTION, None),
    (("昏迷", "晕厥"), EventType.UNCONSCIOUS, None),
    (("说话", "说", "开口", "喊", "大喝", "小声说"), EventType.SPEECH, None),
    # 注：'发黑消失/消失' 是魔法/离场，不映射 DEATH（避免误判 subject 死亡）
]


def _map_event(text: str) -> tuple[EventType, str | None] | None:
    for triggers, etype, part in _RULES:
        if any(t in text for t in triggers):
            return etype, part
    return None


def _victim(text: str) -> str | None:
    for r in (re.compile(r"贯穿([\u4e00-\u9fff]{2,4})的身体"),
              re.compile(r"刺入([\u4e00-\u9fff]{2,4})"),
              re.compile(r"冲向([\u4e00-\u9fff]{2,4})身后")):
        m = r.search(text)
        if m:
            return m.group(1)
    return None


_FALL_RES = [
    re.compile(r"([\u4e00-\u9fff]{2,4})受重创失去支撑"),
    re.compile(r"([\u4e00-\u9fff]{2,4})(?:身体)?(?:瘫软)?(?:重重)?栽倒"),
    re.compile(r"([\u4e00-\u9fff]{2,4})(?:扑入|趴在|趴进)血泊"),
    re.compile(r"([\u4e00-\u9fff]{2,4})被(?:误射的箭|箭)射中"),
]


def _fall_target(text: str) -> str | None:
    """从文本提取'倒地/栽倒/被射中'的对象（不一定是 subject）。"""
    for r in _FALL_RES:
        m = r.search(text)
        if m:
            return m.group(1)
    return None


def _init_state(name: str, kneelers: list[str], hidden: str) -> CharacterState:
    st = CharacterState(character=name)
    if name in kneelers:
        st.stance = Stance.KNEELING
    elif name == hidden:
        st.stance = Stance.STANDING
    return st


def plan_states(scene) -> dict[int, dict[str, CharacterState]]:
    """按 beat 顺序推进，产出 {seg_index: {cid: CharacterState}}（每段开始快照，seg_index 从 1）。"""
    from app.storyboard.scene_segmenter import segment_scene
    from app.storyboard.spatial_layout import build_scene_space

    participants = list(dict.fromkeys(getattr(scene, "participants", []) or []))
    space = build_scene_space(scene)
    kneelers = (space or {}).get("kneelers", [])
    hidden = (space or {}).get("hidden", "")

    # 初始化快照（每角色初始姿态）
    store = EventSourcingStore()
    init: dict[str, CharacterState] = {
        n: _init_state(n, kneelers, hidden) for n in participants
    }
    for n, st in init.items():
        store._snapshots[(n, "main")] = st
    # hidden（隐藏威胁）初始位置：人群阴影（藏脸窥视）
    if hidden and hidden in init:
        from app.state.character_state import ActiveEffect
        init[hidden].effects = [e for e in init[hidden].effects if e.type != "位置"]
        init[hidden].effects.append(ActiveEffect(type="位置", note="位于人群阴影（初始窥视）"))

    segs = segment_scene(scene)
    seg_of_beat: list[int] = []
    for seg in segs:
        seg_of_beat.extend([seg.seg_index] * len(seg.beats))

    snapshots: dict[int, dict[str, CharacterState]] = {}
    _loc: dict[str, str] = {}   # 位置语义标签：退避/立于身侧/倒地（桥接层语义，写入 effects）
    cur = None
    seq = 0
    for i, b in enumerate(getattr(scene, "beats", []) or []):
        idx = seg_of_beat[i] if i < len(seg_of_beat) else (cur or 1)
        if idx != cur:
            if cur is not None:
                # 上一段已结束 → 快照其段结束态（反映该段画面）
                snapshots[cur] = {
                    n: store.snapshot(n).model_copy(deep=True) for n in participants
                }
            cur = idx
        subject = getattr(b, "subject", "") or ""
        text = (getattr(b, "action", "") or "") + " " + (getattr(b, "dialogue", "") or "")
        # 位置语义标签（供站位备注）：独立于事件映射——无事件词的 beat（如血雾侵蚀）也更新位置
        _crowd_retreat = any(w in text for w in ("贵族们", "人群", "众人", "四周贵族")) and any(
            w in text for w in ("惊惶退避", "退避", "退开", "惊呼后退"))
        if any(w in text for w in ("惊惶退避", "退避", "退后两步", "后退", "倒退", "退开")) and not _crowd_retreat:
            _loc[subject] = "已起身退避"
        elif any(w in text for w in ("冲到", "冲向")):
            tgt = _victim(text)  # 仅"冲到X身后"类提取成功才标（防"冲到她身边"误标）
            if tgt and tgt in participants:
                _loc[subject] = f"位于{tgt}身后（已冲出人群）"
        elif any(w in text for w in ("垂剑而立", "低头看", "凑近", "来到身边")):
            tgt = _victim(text) or "罗伊娜"
            _loc[subject] = f"立于{tgt}身侧"
        elif any(w in text for w in ("蹲下", "蹲在", "俯身")):
            _loc[subject] = "蹲下靠近"
        elif any(w in text for w in ("蜷缩",)):
            _loc[subject] = "蜷缩"
        elif any(w in text for w in ("卧在", "卧下", "趴伏")):
            # 仅当主语不在文本中（伪角色/旁观）时，才用文本中其他角色兜底（如"白狼卧在脚边"）
            tgt = subject
            if subject not in text:
                others = [p for p in participants if p != subject and p in text]
                if others:
                    tgt = others[-1]
            _loc[tgt] = "已卧下"
        elif any(w in text for w in ("倒地", "栽倒", "瘫软", "扑入血泊", "趴在血泊", "被射中", "中箭")):
            _ft = _fall_target(text)
            if _ft and _ft in participants:
                _loc[_ft] = "已倒地"
            elif subject:
                _loc[subject] = "已倒地"
        # 事件映射（位置更新后）
        mapped = _map_event(text)
        if not mapped or not subject:
            # 无事件也写入位置标签
            from app.state.character_state import ActiveEffect
            for _cid, _note in _loc.items():
                _st = store.snapshot(_cid)
                _st.effects = [e for e in _st.effects if e.type != "位置"]
                _st.effects.append(ActiveEffect(type="位置", note=_note))
            continue
        etype, part = mapped
        if etype == EventType.RISE and _crowd_retreat:
            from app.state.character_state import ActiveEffect
            for _cid, _note in _loc.items():
                _st = store.snapshot(_cid)
                _st.effects = [e for e in _st.effects if e.type != "位置"]
                _st.effects.append(ActiveEffect(type="位置", note=_note))
            continue
        seq += 1
        events = [Event(event_id=f"seg{idx}_b{i}_e{seq}", chapter=f"段{idx}", seq=seq,
                        type=etype, actor=subject, body_part=part,
                        detail=text[:40], citation=text[:80])]
        # 刺杀类：追加对受害者的 FIGHT + INJURY（目标=受害者）；旁观跪地者自动退避(RISE)
        if etype == EventType.INJURY:
            victim = _victim(text)
            if victim:
                events.append(Event(event_id=f"seg{idx}_b{i}_t{seq}", chapter=f"段{idx}", seq=seq,
                                    type=EventType.FIGHT, actor=subject, target=victim,
                                    detail="刺杀", citation=text[:80]))
                # INJURY 作用于受害者（actor=受害者；前期 StateMachine 的 INJURY 改 actor 自身）
                events.append(Event(event_id=f"seg{idx}_b{i}_i{seq}", chapter=f"段{idx}", seq=seq,
                                    type=EventType.INJURY, actor=victim, body_part="torso",
                                    detail="重伤", citation=text[:80]))
            for _n in participants:
                if _n == subject or _n == victim:
                    continue
                if _n in kneelers:  # 跪地旁观者（如伊索尔德）惊惶退避 → RISE + 标签
                    seq += 1
                    events.append(Event(event_id=f"seg{idx}_b{i}_r{seq}", chapter=f"段{idx}", seq=seq,
                                        type=EventType.RISE, actor=_n,
                                        detail="惊惶退避", citation="刺杀发生时旁观者退避"))
                    _loc[_n] = "已起身退避"
        # 把位置语义写入 effects 台账（替换旧"位置"效果）——覆盖 subject 与旁观者
        from app.state.character_state import ActiveEffect
        for _cid, _note in _loc.items():
            _st = store.snapshot(_cid)
            _st.effects = [e for e in _st.effects if e.type != "位置"]
            _st.effects.append(ActiveEffect(type="位置", note=_note))
        store.apply_many(events)
    # 循环结束：快照最后一段（段结束态）
    if cur is not None and cur not in snapshots:
        snapshots[cur] = {n: store.snapshot(n).model_copy(deep=True) for n in participants}
    return snapshots


def stance_notes(states: dict[str, CharacterState], kneelers: list[str] | None = None) -> str:
    """状态快照 → 站位增量文本（可见）：只标注跪地者(kneelers)的姿态变化（退避/倒地/受创）。

    初始就站立的角色（隐藏威胁/主礼人）不标注——它们不是'从跪到站'，不算退避。
    """
    notes = []
    for cid, st in states.items():
        if kneelers is not None and cid not in kneelers:
            continue  # 非跪地者：初始站立，姿态变化不计入退避
        if not st.alive:
            notes.append(f"{cid}已离场")
        elif st.stance == Stance.KNEELING:
            continue
        elif st.stance == Stance.PRONE:
            notes.append(f"{cid}已倒地")
        elif st.stance == Stance.RESTRAINED:
            notes.append(f"{cid}被压制")
        else:
            # 优先读 effects 台账的"位置"语义（退避/立于身侧），避免从 stance 猜测
            loc = next((e.note for e in st.effects if e.type == "位置"), "")
            dmg = [p for p, v in st.body.model_dump().items() if v in (BodyDamage.SEVERE, BodyDamage.FATAL)]
            if dmg:
                notes.append(f"{cid}已起身（{'/'.join(dmg)}受创）")
            elif loc:
                notes.append(f"{cid}{loc}")
            else:
                notes.append(f"{cid}已起身")
    return "；".join(notes)

