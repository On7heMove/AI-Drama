"""分镜提示词达标率校验（golden 标准版）：按标准维度对最终中文版提示词打分。"""
from __future__ import annotations

import re

from app.storyboard.duration_rule import dialogue_seconds, parse_prompt_duration
from app.storyboard.motion_guard import audit_motion
from app.storyboard.sd_manual import find_blocked_terms as _find_blocked_terms
from app.storyboard.sd_manual import has_realperson_face_reference as _has_realperson_face
from app.storyboard.spatial_layout import audit_spatial as _audit_spatial
from app.storyboard.visual_guard import is_skeleton_en
from app.storyboard.visual_guard import find_metaphors, find_physics_violations, find_sound_in_visual

DIMENSIONS = ("机位", "光线", "站位轴线", "时间轴", "硬切", "声音", "负面", "速度", "画面纯视觉", "物理", "无比喻", "无敏感词", "空间合规", "镜头活性", "英文完整", "时长合理", "对白调度")


def score_prompt_block(scene_id: str, idx: int, zh: str, is_last: bool = False) -> dict:
    lines = zh.splitlines()
    join = "\n".join(lines)
    visual_lines = [ln for ln in lines if ln.startswith("【画面】")]
    dims = {
        "机位": ("机位：" in join and "机位：—" not in join),
        "光线": "【光线】" in join,
        "站位轴线": "【站位·轴线】" in join,
        "时间轴": bool(re.search(r"【\d+-\d+秒】", join)),
        "硬切": "【硬切】" in join or is_last,  # 场景末镜无需硬切
        "声音": "【声音】" in join,
        "负面": "【负面】" in join or "NOT" in join,
        "速度": "【速度】" in join,
        "画面纯视觉": not any(find_sound_in_visual(ln) for ln in visual_lines),
        "物理": not any(find_physics_violations(ln) for ln in visual_lines),
        "无比喻": not any(find_metaphors(ln) for ln in visual_lines),
        "无敏感词": _blocked_terms_ok(join),
        "空间合规": not _audit_spatial(join),
        "镜头活性": not audit_motion(zh),
        "时长合理": _dialogue_time_ok(zh),
        "对白调度": _dialogue_staging_ok(zh),
    }
    return dims


def _blocked_terms_ok(zh: str) -> bool:
    """SD 审核避坑：提示词不含名人/IP 硬拦截词与真人脸依赖。"""
    if _find_blocked_terms(zh):
        return False
    if _has_realperson_face(zh):
        return False
    return True


def _dialogue_staging_ok(zh: str) -> bool:
    """对白调度：有对白必须含说话人动作/微动作；静态机位必须靠表演撑住。"""
    from app.storyboard.dialogue_staging import has_speaker_action as _has_action
    if "【对白】" not in zh:
        return True
    cam = next((l for l in zh.splitlines() if l.startswith("【镜头】")), "")
    staging = next((l for l in zh.splitlines() if l.startswith("【调度】")), "")
    visual = "\n".join(l for l in zh.splitlines() if l.startswith("【画面】"))
    if not staging:
        return False
    if any(s in cam for s in ("固定", "固定机位", "locked", "static")):
        return _has_action(staging) or _has_action(visual)
    return True


def _dialogue_time_ok(zh: str) -> bool:
    need = 0.0
    for ln in zh.splitlines():
        if ln.startswith("【对白】"):
            need += dialogue_seconds(ln[len("【对白】"):].strip())
        if ln.startswith("【声音】") and "旁白·原文" in ln:
            import re
            m = re.search(r"画外音（旁白·原文）：([^；]+)", ln)
            if m:
                from app.storyboard.duration_rule import vo_seconds
                need += vo_seconds(m.group(1))
    if need <= 0:
        return True
    return parse_prompt_duration(zh) >= need - 0.01


def score_plans(plans) -> dict:
    total = 0
    ok = {d: 0 for d in DIMENSIONS}
    for sp in plans:
        shot_zh = getattr(sp, "image_prompts", []) or []
        shot_en = getattr(sp, "english_prompts", []) or []
        for i, zh in enumerate(shot_zh, 1):
            total += 1
            for k, v in score_prompt_block(sp.scene_id, i, zh, is_last=(i == len(shot_zh))).items():
                if v:
                    ok[k] += 1
            if i - 1 < len(shot_en):
                scene = next((l for l in shot_en[i - 1].splitlines() if l.startswith("Scene:")), "")
                if scene and not is_skeleton_en(scene.replace("Scene:", "", 1)):
                    ok["英文完整"] += 1
    per_dim = {k: {"pass": ok[k], "total": total,
                   "rate": round(ok[k] / total, 3) if total else 1.0} for k in DIMENSIONS}
    return {"shots": total,
            "per_dim": per_dim,
            "overall": round(sum(ok.values()) / (total * len(DIMENSIONS)), 3) if total else 1.0}
