"""走向驱动的段提示词渲染（从走向派生一切元素，接入正式渲染的试点路径）。

画面/机位/速度/情绪/空间都从 SegmentTrajectory 派生；对白/声音沿用 beat 原文。
"""
from __future__ import annotations

from app.storyboard.trajectory import build_trajectory

_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def _camera_from_lock(spatial_lock: str) -> str:
    """从走向的空间锁定派生机位描述。"""
    if not spatial_lock:
        return "跟随走向"
    if "正后方" in spatial_lock:
        return "罗伊娜正后方（观众只见她的后脑勺与背部，全程不出现正脸）"
    return spatial_lock


def _mood_from_arc(arc: str) -> str:
    """情绪弧线末端 → 段情绪。"""
    if not arc:
        return "中性"
    tail = arc.split("→")[-1].strip()
    map_en = {"肃穆": "庄重", "期待": "期待", "痛苦/难以置信": "痛苦", "惊变": "震惊", "柔和": "温柔"}
    return map_en.get(tail, tail)


def _extract_dialogue(seg) -> str:
    dlg_parts = []
    for b in seg.beats:
        if b.dialogue and "：" in b.dialogue:
            parts = b.dialogue.split("：", 1)
            speaker = parts[0].split("（")[0].strip()
            emotion = parts[0].split("（")[1].rstrip("）") if "（" in parts[0] else ""
            line = parts[1].strip()
            dlg_parts.append(f"{speaker}（{emotion}）：{line}" if emotion else f"{speaker}：{line}")
    return "；".join(dlg_parts)


def _extract_sound(seg) -> str:
    sounds = [b.sound for b in seg.beats if b.sound]
    return "→".join(sounds[:3])


def build_segment_prompt(scene, seg, aspect: str = "9:16") -> str:
    """从走向渲染完整段提示词（精简格式，单一事实源=走向）。

    长对白段（单拍≥8s含对白）复用 L-Cut 机制（说话→反应→收束三拍，话语声延续入反应画面）。
    """
    from app.production.segment_export import _expand_long_dialogue
    traj = build_trajectory(scene, seg)
    exp = _expand_long_dialogue(scene, seg)
    if exp:
        # L-Cut：说话→反应(L-Cut)→收束 三拍，机位链
        camera = exp["cam_zh"]
        scene_line = exp["visual_zh"].split("｜地点")[0]
    else:
        camera = _camera_from_lock(traj.spatial_lock)
        visuals = [f"{_MARKS[i]} {bt.visual}" for i, bt in enumerate(traj.beats)]
        scene_line = " → ".join(visuals)
    # 机制栈：风格锁定 + 人物形象卡（realism_style / character_profile）
    from app.storyboard.realism_style import style_lock_zh as _style_zh
    from app.storyboard.character_profile import profiles_zh as _profiles_zh
    from app.storyboard.beat_events import plan_states as _plan_states
    from app.storyboard.spatial_layout import build_spatial_block_scene as _spatial_block
    style = _style_zh("9:16")
    chars = _profiles_zh(list(dict.fromkeys(getattr(scene, "participants", []) or [])))
    # 机制栈：五层站位（spatial_layout）+ 走向空间锁定
    _st = _plan_states(scene).get(seg.seg_index, {})
    _blk = _spatial_block(scene, seg, states=_st)
    spatial = _blk["blocking_zh"] if _blk else traj.spatial_lock
    if traj.spatial_lock and spatial != traj.spatial_lock:
        spatial = f"{spatial}；{traj.spatial_lock}"
    slow = [bt for bt in traj.beats if "升格" in bt.pace]
    if slow:
        speed = f"升格为主：{slow[0].event}瞬间{slow[0].pace}，其余实时"
    else:
        speed = "实时"
    mood = _mood_from_arc(traj.beats[-1].emotion_arc) if traj.beats else "中性"
    dialogue = _extract_dialogue(seg)
    sound = _extract_sound(seg)

    lines = [
        f"【段{seg.seg_index}】{seg.duration:.0f}s｜{seg.start_sec:.0f}-{seg.end_sec:.0f}s",
        f"风格：{style}",
        f"人物：{chars}",
        "",
        f"镜头：{camera}",
        f"画面：{scene_line}",
    ]
    if dialogue:
        lines.append(f"对白：{dialogue}")
    if sound:
        lines.append(f"声音：{sound}")
    lines.append(f"速度：{speed}")
    lines.append(f"情绪：{mood}")
    lines.append("")
    lines.append(f"[空间] {spatial}")
    lines.append("[负面] NOT 变形、手部畸形、音画不同步、多余角色入画；无字幕、无水印、无片头Logo")
    return "\n".join(lines)
