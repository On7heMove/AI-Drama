"""SD 使用手册规则包（2026-08-15 学习落盘，配置 config/storyboard/sd_manual.json）。

规则来源：即梦 Seedance 2.0 官方使用手册
https://bytedance.larkoffice.com/wiki/A5RHwWhoBiOnjukIIw6cu5ybnXQ
（浏览器通读全文 + Qwen-VL 学习 49 屏示例，含案例视频封面/提示词/评论区避坑）

落地内容：
- platform_limits：SD2.0 平台硬参数（生成时长 4-15s、图/视频输入限制、混合输入≤12 文件）
- face_upload_restriction：写实人脸素材不可上传 → 人物描述特征化、禁真实人名
- blocked_terms：审核易误杀词（hard=名人/IP 检测即告警；risk=普通词仅告警）
- subtitle_watermark_negative：显式 无字幕/无水印/无片头Logo 负面
- prompt_rules：手册提示词写作法（时间戳/引用/情绪标签/多主体绑定/运镜词表/结构化参数）
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "sd_manual.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


def platform_limits() -> dict:
    """SD2.0 平台硬参数（生成时长/图视频输入限制）。"""
    return _data().get("platform_limits", {})


def hard_max_sec() -> int:
    """SD2.0 单段生成时长硬上限（15s，对齐 scene_segment）。"""
    return platform_limits().get("generation_duration_sec", {}).get("hard_max_sec", 15)


def negative_zh() -> str:
    """手册审核避坑负面（中文）：2026-08-21 用户规则——"无字幕/水印/Logo"生成默认无，写了无效，删除。"""
    return ""


def negative_en() -> str:
    """手册审核避坑负面（英文）：2026-08-21 用户规则——无效项删除。"""
    return ""


def hard_blocked_terms() -> list[str]:
    """名人/IP 等硬拦截词：检测即告警并判合规失败。"""
    return _data().get("blocked_terms", {}).get("hard", [])


def risk_terms() -> list[str]:
    """平台易误杀普通词：仅告警，不判合规失败。"""
    return _data().get("blocked_terms", {}).get("risk", [])


def find_blocked_terms(text: str) -> list[dict]:
    """返回提示词中命中的敏感词（hard=需改写，risk=建议规避）。"""
    if not text:
        return []
    out: list[dict] = []
    for t in hard_blocked_terms():
        if t and t in text:
            out.append({"term": t, "severity": "hard", "fix": "改写为原创特征化描述，避免真实人名/IP"})
    for t in risk_terms():
        if t and t in text:
            out.append({"term": t, "severity": "risk", "fix": "平台易误杀词，建议规避或换词"})
    return out


def has_realperson_face_reference(text: str) -> bool:
    """检测提示词是否依赖真人脸/真实人物参考（平台禁止上传写实人脸素材）。"""
    if not text:
        return False
    marks = _data().get("face_upload_restriction", {}).get("prompt_impact_zh", "")
    keys = ("真人脸", "真人照片", "明星脸", "参考真人", "按真人", "真实人物照片", "公众人物")
    return any(k in text for k in keys)


def camera_vocab_zh() -> list[str]:
    """手册案例中的运镜/镜头词表（中文，供 shot_language 与提示词参考）。"""
    return _data().get("prompt_rules", {}).get("specific_camera_vocab_zh", [])


def camera_vocab_en() -> list[str]:
    """手册案例中的运镜/镜头词表（英文）。"""
    return _data().get("prompt_rules", {}).get("specific_camera_vocab_en", [])


def audit(text: str) -> list[str]:
    """综合审核避坑校验：敏感词（hard/risk）+ 真人脸依赖。"""
    out: list[str] = []
    for hit in find_blocked_terms(text):
        if hit["severity"] == "hard":
            out.append(f"敏感词(需改写)：{hit['term']}（{hit['fix']}）")
        else:
            out.append(f"易误杀词(建议规避)：{hit['term']}")
    if has_realperson_face_reference(text):
        out.append("真人脸依赖：平台禁止上传写实人脸素材，请改为特征化描述（年龄/发型/衣着/气质）")
    return out



def temperament_rules() -> list[dict]:
    """SD 官方「脾气」规则（BytePlus ModelArk 2222480，2026-08-18 落盘，pending_validation）。"""
    return _data().get("official_prompt_guide", {}).get("rules", [])


def temperament_zh(n_subjects: int = 1, has_dialogue: bool = False) -> str:
    """SD 官方「脾气」约束（中文，进提示词【SD脾气·约束】行）。"""
    parts = [
        "一镜一运镜：单镜只给一种运镜，不同时推拉摇移",
        "动作落到身体部位并标注幅度/速度/力度，优先慢而连贯的小动作，避免冲刺/大跳/剧烈翻滚",
        "动作之间补足惯性衔接",
        "情绪用具体身体细节表达，不用抽象情绪词",
    ]
    if n_subjects > 4:
        parts.append("同框重点人物不超过4人，超出者虚化降级、不给清晰正脸")
    if has_dialogue:
        parts.append("对白口型仅做示意、后期配音优先")
    return "；".join(parts)


def temperament_en(n_subjects: int = 1, has_dialogue: bool = False) -> str:
    """SD 官方「脾气」约束（英文版）。"""
    parts = [
        "one camera move per shot, never stack push/pull/pan/track in a single shot",
        "actions specify body part plus range/speed/force; prefer slow, gentle, continuous small motions; avoid sprinting, big jumps, violent rolls",
        "bridge actions with inertia and continuity",
        "show emotion via concrete physical detail, never abstract mood words",
    ]
    if n_subjects > 4:
        parts.append("cap co-framed key people at 4; de-emphasize extras, no clear frontal faces")
    if has_dialogue:
        parts.append("lip-sync schematic only; prefer post-dubbing")
    return "; ".join(parts)
