"""精简段提示词生成器（三层架构 L2）：全局锁定 + 场景头 + 段提示。

三层架构：
- L0 全局锁定（prompt_global.json）：风格/人物/负面基线，每部剧一次
- L1 场景头（scene_header.json）：时间/光线/色调/空间基线，每场景一次
- L2 段提示（本模块）：镜头·画面 + 动作 + 声音 + 速度，精简为关键词

核心需求：单场景内不同分镜（段）的人物状态合理性，由前期逻辑校验模块提供事实底座——
- beat_events.plan_states(scene)：把场景内全部节拍映射为事件流，经 EventSourcingStore+StateMachine
  推进，产出 {seg_index: {cid: CharacterState}}（每段开始时的状态快照）
- spatial_layout.build_spatial_block_scene(scene, seg, states)：按状态快照生成站位行
  （已退避/倒地者不再并肩跪；hidden 位置阶段切换；段级动作增量；状态备注）

本模块只做薄封装 + 精简排版，不重复实现状态机。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.paths import data_root

_GLOBAL_PATH = data_root() / "config" / "storyboard" / "prompt_global.json"
_SCENE_PATH = data_root() / "config" / "storyboard" / "scene_header.json"


@lru_cache(maxsize=1)
def _global() -> dict:
    try:
        return json.loads(_GLOBAL_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _scenes() -> dict:
    try:
        return json.loads(_SCENE_PATH.read_text(encoding="utf-8")).get("data", {}).get("scenes", {})
    except Exception:
        return {}


def style_lock(aspect: str = "9:16") -> str:
    key = f"zh_{aspect.replace(':', '_')}"
    return _global().get("style_lock", {}).get(key, "")


def negative_baseline() -> str:
    words = _global().get("negative_baseline", {}).get("zh", [])
    return "NOT " + "、".join(words) if words else ""


def negative_style() -> str:
    words = _global().get("negative_style", {}).get("zh", [])
    return "NOT " + "、".join(words) if words else ""


def negative_platform() -> str:
    return _global().get("negative_platform", {}).get("zh", "")


def scene_header(scene_id: str) -> dict:
    return _scenes().get(scene_id, {})


def format_spatial_compact(scene_id: str, overrides: dict | None = None) -> str:
    """紧凑空间描述：方位词+竖线分隔，替代自然语言段落。"""
    h = scene_header(scene_id)
    spatial = h.get("spatial", {}).copy()  # 复制避免修改原配置
    if overrides:
        spatial.update(overrides)
    parts = []
    for key in ("front_center", "mid_sides", "back_shadow", "back_high", "camera_side", "axis_lock"):
        v = spatial.get(key, "")
        if v:
            parts.append(f"{v}")
    return "｜".join(parts)


def format_state_compact(states: dict[str, str]) -> str:
    """紧凑状态链：角色:状态｜角色:状态。"""
    return "｜".join(f"{k}:{v}" for k, v in states.items() if v)


_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def _beat_dur(b) -> float:
    """每镜实际时长：用 effective_duration（含对白/声音扩展），保证 Σ每镜时长 = 段时长。"""
    from app.storyboard.duration_rule import effective_duration as _eff
    return _eff(getattr(b, "duration_sec", None) or 3.0,
                getattr(b, "dialogue", "") or "",
                getattr(b, "sound", "") or "")


def format_action_chain(beats: list, start_sec: float = 0.0, max_beats: int = 8, participants: list | None = None) -> str:
    """动作链箭头连接，每拍标注序号+起止秒（SD 按时间轴分配，时间明确）。"""
    actions = []
    t = start_sec
    for i, b in enumerate(beats[:max_beats]):
        act = getattr(b, "action", "") or ""
        # 精简：去"地/得"修饰，保留核心动词
        act = act.replace("猛地", "").replace("缓缓", "").replace("轻轻", "")
        # 动作物理链补全（复用 action_detail，如"一剑贯穿"→"自背后左肩胛下刺入…血自背后伤口涌出"）
        # 这是旧完整版能保持"背后捅人"的关键，精简版必须保留，否则 SD 默认画前胸
        from app.storyboard.action_detail import enrich_action as _enrich
        from app.production.segment_export import _timeline_join as _tl_join
        subj0 = getattr(b, "subject", "") or ""
        _ad = _enrich(act, subj0, list(participants or []))
        if _ad is not None:
            act = _tl_join(act, _ad)
        subj = subj0
        if subj:
            act = re.sub(r"[她他它]", subj, act)  # 代词消解：该镜主体的她/他/它 → 角色名
        dur = _beat_dur(b)
        mark = _MARKS[i] if i < len(_MARKS) else f"[{i + 1}]"
        actions.append(f"{mark} {act}（{t:.0f}-{t + dur:.0f}s）")
        t += dur
    return "→".join(actions)


def format_camera_compact(cam_zh: str) -> str:
    """紧凑镜头：机位→运动，去景别/角度冗余。"""
    parts = {}
    for seg in cam_zh.split("｜"):
        for sep in ("：", ":"):
            if sep in seg:
                k, v = seg.split(sep, 1)
                parts[k.strip()] = v.strip()
                break
    pos = parts.get("机位", "")
    move = parts.get("运动", "")
    if pos and move:
        return f"{pos}·{move}"
    return cam_zh[:50]


# ---------------- 复用前期逻辑校验模块（核心需求：单场景内分镜人物状态合理性） ----------------

def segment_states(scene, seg_index: int) -> dict:
    """本段开始时的角色状态快照 {cid: CharacterState}。

    复用 beat_events.plan_states：场景节拍 → 事件流 → EventSourcingStore+StateMachine 推进 →
    按段产出快照。核心需求：单场景内不同分镜（段）人物状态合理连续。
    """
    from app.storyboard.beat_events import plan_states
    snapshots = plan_states(scene)
    return snapshots.get(seg_index, {})


def segment_spatial_block(scene, seg) -> dict | None:
    """本段站位块（状态机驱动）：复用 spatial_layout.build_spatial_block_scene。

    返回 {blocking_zh, blocking_en, forbidden_zh, forbidden_en, space} 或 None（非仪式场景）。
    """
    from app.storyboard.spatial_layout import build_spatial_block_scene
    states = segment_states(scene, seg.seg_index)
    return build_spatial_block_scene(scene, seg, states=states)


# 伪角色（场景内组合名，非真实人物），状态备注跳过
_PSEUDO_IDS = ("一人一狼",)


def segment_state_notes(scene, seg) -> str:
    """通用段级状态备注（非仪式场景也输出）：角色姿态/位置标签，来自状态机快照。

    核心需求落点：单场景内不同分镜（段）的人物状态合理连续，不限于仪式场景。
    """
    from app.storyboard.beat_events import plan_states
    from app.state.character_state import Stance
    states = plan_states(scene).get(seg.seg_index, {})
    if not states:
        return ""
    notes = []
    for cid, st in states.items():
        if cid in _PSEUDO_IDS:
            continue
        if not st.alive:
            notes.append(f"{cid}已离场")
            continue
        loc = next((e.note for e in st.effects if e.type == "位置"), "")
        if st.stance == Stance.KNEELING:
            notes.append(f"{cid}跪姿")
        elif st.stance == Stance.PRONE:
            notes.append(f"{cid}{loc or '卧下'}")
        elif loc:
            notes.append(f"{cid}{loc}")
        # 站立且无位置标签 → 不输出（避免"已起身"噪音）
    return "；".join(notes)


def split_blocking(blocking_zh: str) -> tuple[str, str]:
    """把站位行拆成 [空间]（结构部分）与 [状态]（状态备注部分）。

    blocking_zh 形如：'前景中央：…；两侧中景：…；更远处…；镜头…；轴侧恒定；本段：…；伊索尔德已起身退避；珍妮芙位于罗伊娜身后'
    规则：'本段：' 之后的段级动作增量 + 状态备注归 [状态]，之前的结构归 [空间]。
    """
    if not blocking_zh:
        return "", ""
    if "本段：" in blocking_zh:
        head, tail = blocking_zh.split("本段：", 1)
        # 状态备注一般在 '本段：...' 之后（段级增量 与 状态备注 都属"本段事实"）
        return head.strip("； "), "本段：" + tail.strip("； ")
    # 无段级增量时：状态备注（退避/倒地/受创）在末尾，无法可靠拆分 → 全部归空间
    return blocking_zh, ""


def build_compact_segment(
    scene_id: str,
    seg_index: int,
    duration: float,
    time_range: str,
    camera: str,
    action_chain: str,
    dialogue: str = "",
    sound: str = "",
    speed: str = "",
    emotion: str = "",
    blocking_zh: str = "",
    forbidden_zh: str = "",
    state_notes: str = "",
    negative_extra: list[str] | None = None,
) -> str:
    """生成精简段提示词（三层架构 L2）。

    blocking_zh: 状态机驱动的站位行（仪式场景，来自 segment_spatial_block）。
    state_notes: 通用段级状态备注（非仪式场景，来自 segment_state_notes）。
    """
    spatial_part, _ = split_blocking(blocking_zh)
    if not spatial_part:
        # 非仪式场景：空间回退到场景头基线
        spatial_part = format_spatial_compact(scene_id)
    # 画面行：动作链 + 角色状态并入画面描述（状态机/推理/校验只为画面服务，不单独输出状态行）
    scene_line = action_chain
    if state_notes:
        scene_line = f"{action_chain}；全段角色状态：{state_notes}"
    lines = [
        f"【段{seg_index}】{duration:.0f}s｜{time_range}",
        "",
        f"镜头：{camera}",
        f"画面：{scene_line}",
    ]
    if dialogue:
        lines.append(f"对白：{dialogue}")
    if sound:
        lines.append(f"声音：{sound}")
    if speed:
        lines.append(f"速度：{speed}")
    if emotion:
        lines.append(f"情绪：{emotion}")
    lines.append("")
    if spatial_part:
        lines.append(f"[空间] {spatial_part}")
    # 负面词分级：基线 + 场景级（如有）+ 平台
    neg_parts = [negative_baseline()]
    if forbidden_zh:
        neg_parts.append("；" + forbidden_zh)
    if negative_extra:
        neg_parts.append("；" + "、".join(negative_extra))
    neg_parts.append("；" + negative_platform())
    lines.append(f"[负面] {''.join(neg_parts)}")
    return "\n".join(lines)


def build_global_header(aspect: str = "9:16", characters: str = "") -> str:
    """生成全局头（L0），每部剧一次。"""
    lines = [
        "【全局锁定】",
        f"风格：{style_lock(aspect)}",
    ]
    if characters:
        lines.append(f"人物：{characters}")
    lines.append(f"风格负面：{negative_style()}")
    return "\n".join(lines)


def build_scene_header_block(scene_id: str) -> str:
    """生成场景头（L1），每场景一次。"""
    h = scene_header(scene_id)
    if not h:
        return ""
    lines = [
        f"【场景】{h.get('name', scene_id)}",
        f"时间：{h.get('time', '')}｜光线：{h.get('lighting', '')}｜色调：{h.get('tone', '')}",
        f"空间：{format_spatial_compact(scene_id)}",
    ]
    return "\n".join(lines)






# ---------------- 英文精简（三层架构 L0/L1/L2 双语） ----------------

def style_lock_en(aspect: str = "9:16") -> str:
    key = f"en_{aspect.replace(':', '_')}"
    return _global().get("style_lock", {}).get(key, "")


def negative_baseline_en() -> str:
    words = _global().get("negative_baseline", {}).get("en", [])
    return "no " + ", ".join(words) if words else ""


def negative_style_en() -> str:
    words = _global().get("negative_style", {}).get("en", [])
    return "no " + ", ".join(words) if words else ""


def negative_platform_en() -> str:
    return _global().get("negative_platform", {}).get("en", "")


def format_spatial_compact_en(scene_id: str, overrides: dict | None = None) -> str:
    """紧凑空间描述（英文）：方位词+竖线分隔。"""
    h = scene_header(scene_id)
    spatial = h.get("spatial_en", {}).copy()
    if overrides:
        spatial.update(overrides)
    parts = []
    for key in ("front_center", "mid_sides", "back_shadow", "back_high", "environment", "camera_side", "axis_lock"):
        v = spatial.get(key, "")
        if v:
            parts.append(v)
    return " | ".join(parts)


def build_global_header_en(aspect: str = "9:16", characters_en: str = "") -> str:
    """全局头英文（L0），每部剧一次。"""
    lines = [
        "【Global Lock】",
        f"Style: {style_lock_en(aspect)}",
    ]
    if characters_en:
        lines.append(f"Characters: {characters_en}")
    lines.append(f"Style negative: {negative_style_en()}")
    return "\n".join(lines)


def build_scene_header_block_en(scene_id: str) -> str:
    """场景头英文（L1），每场景一次。"""
    h = scene_header(scene_id)
    if not h:
        return ""
    lines = [
        f"【Scene】{h.get('name_en', scene_id)}",
        f"Time: {h.get('time_en', '')} | Light: {h.get('lighting_en', '')} | Tone: {h.get('tone_en', '')}",
        f"Space: {format_spatial_compact_en(scene_id)}",
    ]
    return "\n".join(lines)


_CHAR_EN = {
    "罗伊娜": "Rowena", "伊索尔德": "Isolde", "珍妮芙": "Genevieve",
    "白狼": "the White Wolf", "主礼人": "the officiant",
}

_STATE_ZH_EN = [
    ("已起身退避", "has stepped back"),
    ("蹲下靠近", "crouching close"),
    ("蜷缩", "curled up"),
    ("已卧下", "lying down"),
    ("已倒地", "collapsed"),
    ("跪姿", "kneeling"),
    ("已离场", "has left the scene"),
    ("位于人群阴影（初始窥视）", "in the crowd shadow (initial peering)"),
]


def _state_en(zh_note: str) -> str:
    out = zh_note
    # 角色名 → 英文（后补空格：中文状态词紧贴角色名，英文需要空格）
    for cz, ce in _CHAR_EN.items():
        if cz in out:
            out = out.replace(cz, ce + " ")
    # 带角色占位的状态模式
    out = re.sub(r"位于\s*(\w+)\s*身后（已冲出人群）", lambda m: f"behind {m.group(1)} (has charged out of the crowd)", out)
    out = re.sub(r"立于\s*(\w+)\s*身侧", lambda m: f"standing beside {m.group(1)}", out)
    # 简单状态词
    for zh, en in _STATE_ZH_EN:
        if zh in out:
            out = out.replace(zh, en)
    return re.sub(r"\s+", " ", out).strip()


def segment_state_notes_en(scene, seg) -> str:
    """英文通用段级状态备注（非仪式场景）。"""
    zh = segment_state_notes(scene, seg)
    if not zh:
        return ""
    return "; ".join(_state_en(p) for p in zh.split("；") if p.strip())


def split_blocking_en(blocking_en: str) -> tuple[str, str]:
    """英文站位行拆分：'This segment:' 之后归状态，之前归空间。"""
    if not blocking_en:
        return "", ""
    low = blocking_en.lower()
    if "this segment:" in low:
        head, tail = blocking_en.split("this segment:", 1)
        return head.strip("; "), "This segment:" + tail.strip("; ")
    return blocking_en, ""


def _camera_chain(beats, start_sec: float = 0.0) -> str:
    """镜头链（中文）：每镜 序号 机位·运镜（起止秒）。"""
    shots = []
    t = start_sec
    for i, b in enumerate(beats):
        dur = _beat_dur(b)
        pos = getattr(b, "camera_pos", "") or "—"
        subj = getattr(b, "subject", "") or ""
        if subj:
            pos = re.sub(r"[她他它]", subj, pos)  # 代词消解：该镜主体的她/他/它 → 角色名（防"看她回头"指代不明）
        mov = getattr(b, "movement", "") or "—"
        mark = _MARKS[i] if i < len(_MARKS) else f"[{i + 1}]"
        shots.append(f"{mark} {pos}·{mov}（{t:.0f}-{t + dur:.0f}s）")
        t += dur
    return "→".join(shots)


def _extract_dialogue_compact(seg) -> str:
    """段内对白（中文精简）。"""
    dlg_parts = []
    for b in seg.beats:
        if b.dialogue and "：" in b.dialogue:
            parts = b.dialogue.split("：", 1)
            speaker = parts[0].split("（")[0].strip()
            emotion = parts[0].split("（")[1].rstrip("）") if "（" in parts[0] else ""
            line = parts[1].strip()
            dlg_parts.append(f"{speaker}（{emotion}）：{line}" if emotion else f"{speaker}：{line}")
    return "；".join(dlg_parts)


def _extract_sound_compact(seg) -> str:
    """段内声音（中文精简，最多3节拍）。"""
    sounds = [b.sound for b in seg.beats if b.sound]
    return "→".join(sounds[:3])


def _extract_speed_compact(seg) -> dict:
    """速度标记（中英）：升格/实时 + 负面描述（机制化，按范围分级）。"""
    from app.storyboard.speed_control import (decide_speed, negative_en as _neg_en,
                                              negative_zh as _neg_zh, render_en as _ren_en,
                                              render_zh as _ren_zh)
    for b in seg.beats:
        spd = decide_speed(b.action or "", b.emotion or "")
        if spd.get("mode") == "slow_mo":
            nz = _neg_zh(spd)
            ne = _neg_en(spd)
            return {"speed": _ren_zh(spd), "negative": nz.split("；", 1)[1] if "；" in nz else "",
                    "speed_en": _ren_en(spd), "negative_en": ne.split("; ", 1)[1] if "; " in ne else ""}
    return {"speed": "实时", "negative": "", "speed_en": "real time (1x), no slow motion", "negative_en": ""}


_MOOD_EN = {"庄重": "solemn", "郑重": "solemn", "怨毒": "venomous", "狠厉": "fierce and ruthless", "虚弱": "feeble",
            "戒备": "on guard", "紧张": "tense", "温柔": "gentle", "绝望": "despairing",
            "混乱": "chaotic", "震惊": "shocked", "冷静": "calm", "中性": "neutral",
            "恶毒": "malicious", "冷酷": "cold", "悲伤": "sorrowful", "愤怒": "angry", "害怕": "afraid",
            "狂乱": "frantic", "崩溃": "broken", "痛苦": "anguished", "决然": "determined", "疼痛": "painful",
            "急切": "urgent", "压抑": "oppressed", "惊惧": "terrified"}


def _mood_en(emotion: str) -> str:
    return _MOOD_EN.get(emotion or "", emotion or "neutral")


def _speaker_en(sp: str) -> str:
    """说话人（中文）→ 英文：角色名子串匹配 + OS/贵族N 后缀。"""
    sp = sp.strip()
    for cz, ce in _CHAR_EN.items():
        if cz in sp:
            sp = sp.replace(cz, ce)
            break
    m = re.match(r"贵族(\d+)", sp)
    if m:
        return f"Noble {m.group(1)}"
    if sp.endswith("OS"):
        return sp[:-2].strip() + " (OS)"
    return sp


def _dialogue_en(zh_dlg: str) -> str:
    """对白（中文精简行）→ 英文（角色名/情绪映射，台词保留原文）。"""
    if not zh_dlg:
        return ""
    out = []
    for seg in zh_dlg.split("；"):
        if "：" not in seg:
            out.append(seg)
            continue
        speaker, line = seg.split("：", 1)
        emo = ""
        if "（" in speaker:
            sp = speaker.split("（")
            speaker, emo = sp[0], sp[1].rstrip("）")
        en_speaker = _speaker_en(speaker)
        if emo:
            en_emos = [e for w in emo.split("、") if (e := _MOOD_EN.get(w.strip(), ""))]
            out.append(f"{en_speaker} ({', '.join(en_emos)}): {line.strip()}" if en_emos else f"{en_speaker}: {line.strip()}")
        else:
            out.append(f"{en_speaker}: {line.strip()}")
    return "; ".join(out)


def build_compact_zh_from_scene(scene, seg, scene_id: str) -> str:
    """从场景段组装中文精简段（三层架构 L2），供双语导出复用。"""
    camera = _camera_chain(seg.beats, seg.start_sec)
    action_chain = format_action_chain(seg.beats, seg.start_sec, participants=getattr(scene, "participants", []))
    dialogue = _extract_dialogue_compact(seg)
    sound = _extract_sound_compact(seg)
    speed_info = _extract_speed_compact(seg)
    emotion = seg.beats[0].emotion if seg.beats else ""
    block = segment_spatial_block(scene, seg)
    blocking_zh = block["blocking_zh"] if block else ""
    forbidden_zh = block["forbidden_zh"] if block else ""
    # 角色状态恒进画面行（状态机/推理/校验只为画面详尽服务）
    state_notes = segment_state_notes(scene, seg)
    negative_extra = [speed_info["negative"]] if speed_info["negative"] else []
    return build_compact_segment(
        scene_id=scene_id, seg_index=seg.seg_index, duration=seg.duration,
        time_range=f"{seg.start_sec:.0f}-{seg.end_sec:.0f}s",
        camera=camera, action_chain=action_chain, dialogue=dialogue, sound=sound,
        speed=speed_info["speed"], emotion=emotion, blocking_zh=blocking_zh,
        forbidden_zh=forbidden_zh, state_notes=state_notes, negative_extra=negative_extra,
    )


def _en_roles(text: str) -> str:
    """英文站位/状态/负面里的中文角色名 → 英文（blocking_en 模板填充的是中文名）。"""
    if not text:
        return text
    for cz, ce in _CHAR_EN.items():
        text = text.replace(cz, ce)
    return text


def build_compact_segment_en(
    seg_index: int,
    duration: float,
    time_range: str,
    camera_en: str,
    scene_en: str,
    dialogue_en: str = "",
    sound_en: str = "",
    speed_en: str = "",
    mood_en: str = "",
    blocking_en: str = "",
    forbidden_en: str = "",
    state_notes_en: str = "",
    negative_extra_en: list[str] | None = None,
) -> str:
    """生成英文精简段（三层架构 L2，对应 build_compact_segment）。"""
    blocking_en = _en_roles(blocking_en)
    forbidden_en = _en_roles(forbidden_en)
    state_notes_en = _en_roles(state_notes_en)
    spatial_en, _ = split_blocking_en(blocking_en)
    scene_line_en = scene_en
    if state_notes_en:
        scene_line_en = f"{scene_en}; full-segment character state: {state_notes_en}"
    lines = [
        f"【English Version 段{seg_index}】(continuous · approx {duration:.0f}s)",
        f"Camera: {camera_en}",
        f"Scene: {scene_line_en}",
    ]
    if dialogue_en:
        lines.append(f"Dialogue: {dialogue_en}")
    if sound_en:
        lines.append(f"Sound: {sound_en}")
    if speed_en:
        lines.append(f"Speed: {speed_en}")
    if mood_en:
        lines.append(f"Mood: {mood_en}")
    if spatial_en:
        lines.append(f"[Space] {spatial_en}")
    neg = [negative_baseline_en()]
    if forbidden_en:
        neg.append("; " + forbidden_en)
    if negative_extra_en:
        neg.append("; " + ", ".join(negative_extra_en))
    neg.append("; " + negative_platform_en())
    lines.append(f"[Constraints] {''.join(neg)}")
    return "\n".join(lines)
