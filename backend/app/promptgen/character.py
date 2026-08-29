"""人物形象提示词生成器：ParsedCharacter → 文生图正/负向词 + SD 平台合规校验。

- 正向词：性别/年龄/外貌/服装/气质 特征化描述（忠实原文，不编造）
- 负向词：按题材自适应（negative_selector.character_negative(genre)），精简 + 题材差异项
- 合规：sd_manual.audit 检测敏感词/真人脸依赖
"""
from __future__ import annotations

from app.promptgen.schemas import CharacterPrompt, ParsedCharacter, ParsedScript
from app.storyboard import sd_manual
from app.storyboard.negative_selector import character_negative as _character_negative


def _positive_zh(c: ParsedCharacter) -> str:
    parts = []
    if c.gender:
        parts.append(c.gender)
    if c.age:
        parts.append(c.age)
    if c.appearance:
        parts.append(c.appearance)
    if c.outfit:
        parts.append(f"身穿{c.outfit}")
    if c.temperament:
        parts.append(f"气质{c.temperament}")
    return "、".join(parts) if parts else "（原文未写形象细节）"


def _negative_zh(genre: str = "") -> str:
    return _character_negative(genre)


def build_character_prompts(script: ParsedScript) -> list[CharacterPrompt]:
    out: list[CharacterPrompt] = []
    for c in script.characters:
        positive = _positive_zh(c)
        text = f"{c.name}：{positive}"
        warnings = sd_manual.audit(text)
        out.append(
            CharacterPrompt(
                character=c.name,
                role=c.role,
                positive=positive,
                negative=_negative_zh(script.genre),
                audit_warnings=warnings,
            )
        )
    return out
