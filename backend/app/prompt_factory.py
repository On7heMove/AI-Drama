"""统一 Prompt 入口（§17）：所有环节构造 prompt 走同一入口，按 stage 注入 cangjie skill + 缺省负面提示词。

- STAGE_REGISTRY：一处声明每个 stage 注哪些规则集（冲突/钩子/节拍/弧线/阈值）。
- inject_skill(stage)：从 config/story/*.json 读规则，压缩成"精准约束摘要"（每类取前 N 条，带来源 id）。
- default_negatives()：防幻觉缺省负面提示词。
- build_user(stage, template, **vars)：fill 模板 + 注入 skill + 负面提示词，返回最终 user prompt。

复用声明：复用 app.production.prompts（load_prompt/fill）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.production.prompts import fill

CONFIG_STORY = Path(__file__).resolve().parents[2] / "config" / "story"
RULE_FILES = {
    "conflict_types": "conflict_types.json",
    "hook_types": "hook_types.json",
    "beat_structures": "beat_structures.json",
    "arc_shapes": "arc_shapes.json",
    "thresholds": "thresholds.json",
    # 8-28 cangjie 蒸馏库（save-the-cat + mckee，33 条）：生成链主注入
    "skills_v2": "skills_v2.json",
}

# stage 注册表：每个 stage 注入哪些规则集（顺序=展示顺序）
STAGE_REGISTRY: dict[str, dict] = {
    # 8-28 蒸馏（skills_v2）为各生成环节主注入；旧 beat_structures 不再主注入（保留文件）
    "build_spine": {"rules": ["skills_v2", "conflict_types", "arc_shapes"], "negatives": True, "max_items": 18},
    "storyline": {"rules": ["skills_v2", "hook_types"], "negatives": True, "max_items": 18},
    "outline": {"rules": ["skills_v2", "hook_types", "conflict_types"], "negatives": True, "max_items": 20},
    "episode": {"rules": ["skills_v2", "hook_types", "thresholds"], "negatives": True, "max_items": 20},
    "quality": {"rules": [], "negatives": False, "max_items": 0},
    "l2l3": {"rules": ["skills_v2"], "negatives": True, "max_items": 18},
}

_CACHE: dict[str, list[dict]] = {}


def _load_rules(kind: str) -> list[dict]:
    if kind not in _CACHE:
        p = CONFIG_STORY / RULE_FILES[kind]
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            _CACHE[kind] = data if isinstance(data, list) else []
        else:
            _CACHE[kind] = []
    return _CACHE[kind]


def _name(item: dict) -> str:
    return str(item.get("name") or item.get("id") or "")


def _desc(item: dict) -> str:
    return str(item.get("desc") or "")


def inject_skill(stage: str, max_items: int | None = None) -> str:
    """按 stage 从 config/story 注入 cangjie 规则摘要（精准约束，非原文）。"""
    cfg = STAGE_REGISTRY.get(stage, {"rules": [], "negatives": False, "max_items": 0})
    max_items = max_items if max_items is not None else int(cfg.get("max_items", 0))
    lines: list[str] = ["## 编剧方法论约束（cangjie skill）"]
    count = 0
    for kind in cfg.get("rules", []):
        items = _load_rules(kind)
        if not items:
            continue
        lines.append(f"### {kind}")
        for it in items:
            if max_items and count >= max_items:
                break
            src = it.get("source", "")
            lines.append(f"- {_name(it)}（{src}）：{_desc(it)}")
            count += 1
        if max_items and count >= max_items:
            break
    if count == 0:
        return ""
    return "\n".join(lines)


def default_negatives() -> str:
    """防幻觉缺省负面提示词。"""
    return (
        "## 负面提示词（防幻觉）\n"
        "- 只以梗概为唯一事实源；梗概未明确的事实一律不得当作已发生，禁止把推断当成既定事实。\n"
        "- 不得把梗概未提及的角色/事件/地点卷入既定事件，不得自行添加梗概之外的站点、冲突或关系。\n"
        "- 梗概未定的关键事实（姓名/动机/归属等）一律写入 open_questions，不得自行坐实。"
    )


def build_user(stage: str, template: str, _foreshadows: list[str] | None = None, **vars) -> str:
    """唯一入口：模板 fill + 按 stage 注入 skill + 缺省负面提示词 + 未回收伏笔（可选）。"""
    user = fill(template, **vars)
    cfg = STAGE_REGISTRY.get(stage, {})
    skill = inject_skill(stage)
    if skill:
        user += "\n\n" + skill
    if cfg.get("negatives", False):
        user += "\n\n" + default_negatives()
    if _foreshadows:
        user += "\n\n## 未回收伏笔（必须推进或兑现，不得遗忘）\n" + "\n".join(f"- {t}" for t in _foreshadows)
    return user


def skill_summary(stage: str) -> str:
    """只返回该 stage 注入的规则来源摘要（供审计）。"""
    items = inject_skill(stage)
    if not items:
        return ""
    kinds = [k for k in STAGE_REGISTRY.get(stage, {}).get("rules", []) if _load_rules(k)]
    return f"{stage}: {kinds} (注入 {items.count(chr(10))} 条)"
