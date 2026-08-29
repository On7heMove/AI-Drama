"""20维向量 → 分镜提示词 完整链路（产线二提示词生成接入点）。

场景上下文包 → profile_scene(LLM 出 20 维向量+校验) → render_from_profile(本地渲染参数包)
→ apply_skills(25 技能契约建议) → 组装标准标签提示词。

对齐 docs/20维向量接入产线二.md（原则轴 Schema 运行态）：
LLM 只出向量；本地校验 + 渲染 + 技能 + 组装；护城河在本地。
"""
from __future__ import annotations

import re

from app.storyboard.skill_contract import apply_skills
from app.storyboard.vector_profiler import profile_scene, render_from_profile

STYLE_LOCK = "真人风，真实自然的人物与表演，日常真实生活质感，竖屏9:16"
NEGATIVE_BASE = "NOT 塑料质感、廉价、轻飘飘、CG感、穿模、光污染"


def assemble_prompt(profile: dict, render: dict, skills: dict, scene_ctx: str = "") -> str:
    """向量/渲染参数/技能建议 → 标准标签提示词（scene_ctx=场景主体内容，注入【画面内容】行）。"""
    p = render.get("params", {})
    lines = [f"【风格锁定】{STYLE_LOCK}"]
    emo = str((profile or {}).get("emotion", "") or "").strip()
    if emo and emo != "中性":
        lines.append(f"【情绪】{emo}")
    light = str((profile or {}).get("lighting", "") or "").strip()
    if light:
        lines.append(f"【光线】{light}")
    # 2026-08-26 主体描写：场景上下文（人物/动作/对白/旁白/音效）注入提示词，
    # 否则只有镜头语法（景别/机位/运镜），画面无主体。
    if scene_ctx and str(scene_ctx).strip():
        lines.append(f"【画面内容】{str(scene_ctx).strip()}")
    lines.append(f"【画面】{_join(p.get('景别分布'))}｜机位：{_join(p.get('机位角度'))}｜运镜：{_join(p.get('运镜'))}")
    if p.get("构图"):
        lines.append(f"【构图】{_join(p['构图'])}")
    if not light and p.get("色调光线"):
        lines.append(f"【光线】{_join(p['色调光线'])}")
    if p.get("声音"):
        lines.append(f"【声音】{_join(p['声音'])}")
    if p.get("单镜时长") or p.get("剪辑"):
        lines.append(f"【节奏】时长：{_join(p.get('单镜时长'))}｜剪辑：{_join(p.get('剪辑'))}")
    if p.get("FOV/视场角"):
        lines.append(f"【镜头】FOV：{_join(p['FOV/视场角'])}")
    # 技能建议
    adv = skills.get("advices") or []
    if adv:
        lines.append("【技能依据】" + "；".join(f"{a['slug']}（{a['source_book']}）" for a in adv[:4]))
    # 负面：渲染负面 + 基础
    neg_parts = [NEGATIVE_BASE]
    for extra in (str((profile or {}).get("negative", "") or "").strip(), str(p.get("负面") or "")):
        for token in re.split(r"[、,，]", extra):
            token = token.strip()
            if token and token not in neg_parts:
                neg_parts.append(token)
    lines.append("【负面】" + "、".join(neg_parts))
    return "\n".join(lines)


def _join(x) -> str:
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return "、".join(str(i) for i in x if i)
    if isinstance(x, dict):
        return "、".join(f"{k}={v}" for k, v in x.items())
    return str(x)


async def render_scene_prompt(scene_ctx: str, client=None, scene_id: str = "scene") -> dict:
    """场景 → 完整分镜提示词（20维向量链路）。"""
    profile = await profile_scene(scene_ctx, client=client, scene_id=scene_id)
    render = render_from_profile(profile)
    skills = apply_skills(scene_ctx, profile)
    prompt = assemble_prompt(profile, render, skills, scene_ctx)
    return {
        "scene_id": scene_id,
        "profile": profile,
        "render": render,
        "skills": skills,
        "prompt": prompt,
    }
