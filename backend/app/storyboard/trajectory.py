"""走向模型（2026-08-19，导演思维核心落地）：一段视频怎么走。

两层统一：
- 世界骨架（确定性）：始态 → 事件链 → 终态（状态机给出，事件可一步到位或多步推进）
- 花样层（选择性）：节拍的 时间/节奏(pace)/情绪弧线/意义目标（观众体验包装）

从走向派生一切元素（机位/站位/光线/升格/表情），不再独立决策后拼装——单一事实源=走向。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrajectoryBeat:
    """一个关键节拍：走向上的拐点/步骤。"""
    event: str            # 触发事件描述（世界改变什么）
    time: str             # 段内时间/顺序（如 铺垫/转折/收束 或 0-3s）
    pace: str             # 铺垫 / 升格 / 实时 / 快切
    emotion_arc: str      # 此刻观众感受
    visual: str           # 此刻画面核心（谁在哪、什么姿态/光）


@dataclass
class SegmentTrajectory:
    """一段视频的走向。"""
    seg_index: int
    start_state: str      # 始态（角色状态摘要）
    end_state: str        # 终态
    beats: list[TrajectoryBeat] = field(default_factory=list)
    meaning: str = ""     # 意义目标（观众体验：秩序/惊变/悲壮/爽…）
    spatial_lock: str = ""  # 走向锁定的空间事实（如"全程罗伊娜正后方看后背"）


# ---------------- 走向推导（世界骨架 + 花样层） ----------------

_EMOTION_ARC = {
    "仪式": "肃穆→庄重→期待",
    "刺杀": "肃穆→骤紧→惊变→痛苦/难以置信",
    "对峙": "压抑→剑拔弩张→爆发",
    "日常": "平静→温暖→柔和",
}


def build_trajectory(scene, seg, next_states: dict | None = None) -> SegmentTrajectory:
    """从场景段推导走向：始态/终态（状态机）+ 事件链 + 节奏/情绪/意义（花样层）。

    世界骨架：plan_states 给段开始状态；事件链从 beat 文本映射（_map_event）。
    花样层：节奏用 speed_control（升格分级）；情绪弧线按场景/事件类型；意义按事件类型。
    """
    from app.storyboard.beat_events import _map_event, plan_states
    from app.storyboard.speed_control import decide_speed
    from app.storyboard.emotion_infer import infer_emotion

    states = plan_states(scene)
    # plan_states 的 snapshots[i] 存段 i 结束态（=段 i+1 开始态）
    # 本段始态 = 上一段结束态（段1 无上一段则用段1 自身结束态兜底）
    start = states.get(seg.seg_index - 1, {}) or states.get(seg.seg_index, {})
    end = next_states if next_states is not None else states.get(seg.seg_index, {})

    def _state_summary(snaps: dict) -> str:
        if not snaps:
            return ""
        from app.state.character_state import BodyDamage, Stance
        parts = []
        for cid, st in snaps.items():
            loc = next((e.note for e in st.effects if e.type == "位置"), "")
            dmg = [p for p, v in st.body.model_dump().items() if v in (BodyDamage.SEVERE, BodyDamage.FATAL)]
            pose = st.stance.value
            if dmg:
                parts.append(f"{cid}{'、'.join(dmg)}受创")
            elif loc:
                parts.append(f"{cid}{loc}")
            elif pose != "站立":
                parts.append(f"{cid} {pose}")
        return "；".join(parts[:4])

    traj = SegmentTrajectory(
        seg_index=seg.seg_index,
        start_state=_state_summary(start),
        end_state=_state_summary(end),
    )

    # 事件链 + 节奏 + 情绪
    is_stab = any("贯穿" in (b.action or "") or "刺入" in (b.action or "") for b in seg.beats)
    is_ceremony = any("主礼人" in (b.action or "") or "授剑" in (b.action or "") or "开口" in (b.action or "") for b in seg.beats)
    # 旁观退避（主礼人/伊索尔德惊惶退避）是刺杀动作的衍生物，不进画面节拍（不给镜头，镜头预算给核心事件）
    bystander_retreat = ("惊惶退避", "退避", "退后两步", "惊呼后退", "倒吸冷气")
    for i, b in enumerate(seg.beats):
        text = (b.action or "") + " " + (b.dialogue or "")
        if any(w in text for w in bystander_retreat) and not any(k in text for k in ("罗伊娜", "珍妮芙")):
            continue  # 旁观者退避：背景衍生物，不派节拍
        ev = _map_event(text)
        ev_name = ev[0].value if ev else (b.action or "")[:14]
        spd = decide_speed(b.action or "", b.emotion or "")
        if spd.get("mode") == "slow_mo":
            pace = f"升格{spd.get('seconds', 1.0):.0f}s"  # 升格只给关键转折（刺入瞬间）
        elif is_ceremony:
            pace = "铺垫"
        else:
            pace = "实时"
        # 动作物理链补全（action_detail：如"一剑贯穿"→"自背后左肩胛下刺入…血自背后伤口涌出"）
        from app.production.segment_export import _enrich_beat_action
        act = _enrich_beat_action(scene, b)
        # 导演拍板：旁观退避是刺杀衍生物，不进画面（镜头预算给核心事件）→ 移除含惊惶退避/倒吸冷气的子句
        import re as _re
        act = _re.sub(r"；[^；；]*(?:惊惶退避|倒吸冷气|退后两步|惊呼后退)[^；；]*", "", act)
        visual = f"{b.subject or ''}：{act}"
        if is_stab and i == len(seg.beats) - 1:
            # 收束（回头+对白）：保持跪姿、头微侧向背后，只露侧脸轮廓，不转体
            visual += "（保持跪姿面朝圣坛，头微侧向背后，只露侧脸轮廓，不转体）"
        traj.beats.append(TrajectoryBeat(
            event=ev_name,
            time="转折" if spd.get("mode") == "slow_mo" else ("铺垫" if i == 0 else "收束"),
            pace=pace,
            emotion_arc=_EMOTION_ARC["刺杀"] if is_stab else _EMOTION_ARC["仪式"],
            visual=visual,
        ))

    # 意义 + 空间锁定（花样层，按走向）
    if is_stab:
        traj.meaning = "惊变：至亲从背后捅破仪式秩序（非悲壮，是背叛寒意）"
        traj.spatial_lock = "全程罗伊娜正后方看后背"  # 只放可执行指令，后台推理(走向是受难)只在 meaning，不进前端
    elif is_ceremony:
        traj.meaning = "秩序：建立仪式稳定感，为段3打破蓄势（先稳后破）"
        traj.spatial_lock = "过肩看主礼人与王剑，轴线稳定"
    return traj


def render_trajectory_prompt(traj: SegmentTrajectory, seg) -> str:
    """从走向渲染段提示词（先走向后元素：机位/站位/光线/升格/表情都从走向派生）。"""
    lines = [
        f"【走向】段{traj.seg_index}｜始态：{traj.start_state or '—'}｜终态：{traj.end_state or '—'}",
        f"意义：{traj.meaning}",
        f"空间锁定：{traj.spatial_lock}",
        "",
        "【节拍】",
    ]
    for i, bt in enumerate(traj.beats, 1):
        lines.append(f"  {i}. [{bt.time}|{bt.pace}] {bt.event}｜{bt.emotion_arc}")
    lines.append("")
    lines.append("【从走向派生】")
    lines.append(f"  机位：{traj.spatial_lock}")
    lines.append(f"  画面：{' → '.join(bt.visual for bt in traj.beats)}")
    return "\n".join(lines)
