"""视听语言技能规则接入（产线二）：从 production_rules.json 读 cangjie 蒸馏的 221 个 skill，
对分镜/提示词文本做关键词路由，输出对应 skill 的规则提示（note 级，不阻断产线）。

复用声明：复用 config/skills/production_rules.json（技能注册的机器可读形态）；
不重复造轴线引擎（blocking.py 已处理 180° 轴线），本模块只做「技能知识提示」层。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.paths import data_root

RULES_PATH = data_root() / "config" / "skills" / "production_rules.json"

# 关键词路由阈值：至少命中几个 trigger 词才提示（防误报）
MIN_HITS = 1


@lru_cache(maxsize=1)
def _rules() -> dict:
    if not RULES_PATH.exists():
        return {}
    try:
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _trigger_keywords(trigger: str) -> list[str]:
    """从 trigger/description 提取关键词（逗号/顿号分隔的短语，长度 2-12）。"""
    # 提取"关键 trigger："后的部分优先
    m = re.search(r"关键 trigger[：:]\s*(.+)$", trigger)
    seg = m.group(1) if m else trigger
    seg = seg.split("。")[0]
    words = [w.strip() for w in re.split(r"[，,、；;/]", seg) if 2 <= len(w.strip()) <= 12]
    return words


@lru_cache(maxsize=1)
def _skill_index() -> list[dict]:
    """预计算：{stage, id, source_book, keywords} 索引。"""
    out = []
    for sk in _rules().get("skills", []):
        kws = _trigger_keywords(str(sk.get("trigger", "")))
        if not kws:
            continue
        out.append({
            "id": sk.get("id", ""),
            "source_book": sk.get("source_book", ""),
            "stage": sk.get("stage", ""),
            "stage_cn": sk.get("stage_cn", ""),
            "keywords": kws,
        })
    return out


def note_for(text: str, stage: str = "") -> list[dict]:
    """对分镜/提示词文本返回命中的技能规则提示（note 级）。

    返回 [{skill, source_book, stage, hits}]；hits 为命中的关键词。
    命中规则：同一 skill 至少 MIN_HITS 个 trigger 关键词出现。
    """
    if not text:
        return []
    out = []
    for sk in _skill_index():
        if stage and sk["stage"] != stage:
            continue
        hits = [k for k in sk["keywords"] if k in text]
        if len(hits) >= MIN_HITS:
            out.append({
                "skill": sk["id"],
                "source_book": sk["source_book"],
                "stage": sk["stage_cn"],
                "hits": hits[:5],
            })
    return out


def rule_notes(vids: list[dict]) -> list[dict]:
    """对段级分镜结构返回所有技能提示：[{scene_id, dimension, evidence}]（note 级）。

    去重：同一 scene_id + 同一技能只提示 1 次（防刷屏）；命中词合并展示。
    """
    issues = []
    seen: set[tuple[str, str]] = set()
    for it in vids or []:
        sid = it.get("scene_id", "")
        for zh in it.get("image_prompts", []) or []:
            for n in note_for(zh):
                key = (sid, n["skill"])
                if key in seen:
                    continue
                seen.add(key)
                issues.append({
                    "scene_id": sid,
                    "dimension": "skill_rule",
                    "evidence": f"[{n['stage']}·{n['source_book']}] {n['skill']}（命中: {'、'.join(n['hits'])}）",
                })
    return issues
