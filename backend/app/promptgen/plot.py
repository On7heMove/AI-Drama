"""剧情提示词生成器：ParsedEpisode → 每集剧情提示词（爆点/钩子/情绪递进，双语）。

LLM 已提取每集 explosion/hook/emotional_curve（见 parse）；本模块负责本地模板组装 + 可选英文。
"""
from __future__ import annotations

from app.promptgen.schemas import ParsedScript, PlotPrompt

_TEMPLATE_ZH = (
    "【第{ep}集 · {title}】\n"
    "爆点：{explosion}\n"
    "开场钩：{hook_open}\n"
    "结尾钩：{hook_end}\n"
    "情绪曲线：{curve}"
)

_TEMPLATE_EN = (
    "Episode {ep} · {title}\n"
    "Climax: {explosion}\n"
    "Opening hook: {hook_open}\n"
    "Ending hook: {hook_end}\n"
    "Emotional arc: {curve}"
)


def _curve_zh(curve: list[str]) -> str:
    return " → ".join(curve) if curve else "起 → 升 → 钩"


def build_plot_prompts(script: ParsedScript, *, en: bool = False) -> list[PlotPrompt]:
    """本地确定性组装剧情提示词（不调 LLM；en=True 生成英文版）。"""
    out: list[PlotPrompt] = []
    for ep in script.episodes:
        out.append(
            PlotPrompt(
                ep=ep.ep,
                title=ep.title,
                explosion=ep.explosion,
                hook_open=ep.hook_open,
                hook_end=ep.hook_end,
                emotional_curve=list(ep.emotional_curve),
                prompt_zh=_TEMPLATE_ZH.format(
                    ep=ep.ep,
                    title=ep.title or "未命名",
                    explosion=ep.explosion or "（未提取）",
                    hook_open=ep.hook_open or "（未提取）",
                    hook_end=ep.hook_end or "（未提取）",
                    curve=_curve_zh(ep.emotional_curve),
                ),
                prompt_en=(
                    _TEMPLATE_EN.format(
                        ep=ep.ep,
                        title=ep.title or "Untitled",
                        explosion=ep.explosion or "(not extracted)",
                        hook_open=ep.hook_open or "(not extracted)",
                        hook_end=ep.hook_end or "(not extracted)",
                        curve=" → ".join(ep.emotional_curve) or "rise → climax → hook",
                    )
                    if en
                    else ""
                ),
            )
        )
    return out
