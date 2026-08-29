"""场景全景图提示词生成器：ParsedScene → 文生图正/负向词 + 合规校验。

负向词按题材自适应（negative_selector.scene_negative(genre)），精简 + 题材差异项。
"""
from __future__ import annotations

from app.promptgen.schemas import ParsedScene, ParsedScript, ScenePrompt
from app.storyboard import sd_manual
from app.storyboard.negative_selector import scene_negative as _scene_negative


def _positive_zh(s: ParsedScene) -> str:
    parts = []
    if s.era:
        parts.append(s.era)
    if s.interior:
        parts.append(f"{s.interior}景")
    if s.time:
        parts.append(s.time)
    if s.lighting:
        parts.append(f"光线：{s.lighting}")
    if s.atmosphere:
        parts.append(f"氛围：{s.atmosphere}")
    return "，".join(parts) if parts else "（原文未写场景细节）"


def _negative_zh(genre: str = "") -> str:
    return _scene_negative(genre)


def build_scene_prompts(script: ParsedScript) -> list[ScenePrompt]:
    out: list[ScenePrompt] = []
    for ep in script.episodes:
        for s in ep.scenes:
            positive = _positive_zh(s)
            text = f"{s.location}：{positive}"
            warnings = sd_manual.audit(text)
            out.append(
                ScenePrompt(
                    scene_id=s.scene_id or f"e{ep.ep}_s{len(out)+1}",
                    location=s.location or "未命名场景",
                    positive=positive,
                    negative=_negative_zh(script.genre),
                    audit_warnings=warnings,
                )
            )
    return out
