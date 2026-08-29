"""负面提示词自适应选择器（本地确定性，2026-08-20）：

按 题材(genre) × 场景类型(scene_type) × 景别(scale) 从 genre_negative.json 配置包拼装精简负面词，
替代原先固定长串（_NEGATIVE_BASE + realism_style 全量 + sd_manual 全量）。

结构（复用既有轮子，不重造）：
- 人物/场景基底：读 config/storyboard/genre_negative.json（网络调研版数据）。
- 视频镜头负面：speed_control 慢动作负面 + realism_style 风格负面（精简引用）+ genre_negative 场景类型/景别差异项。
- 平台硬约束（无字幕/水印/片头Logo）：复用 sd_manual.negative_zh()。
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root
from app.storyboard import sd_manual
from app.storyboard import realism_style
from app.storyboard.speed_control import negative_zh as _speed_neg_zh

_PATH = data_root() / "config" / "storyboard" / "genre_negative.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


def _norm_genre(genre: str) -> str:
    """题材归一：'现代/都市' → '现代'；'古装/古代' → '古装'；'悬疑/惊悚/犯罪' → '悬疑'；其余原样。"""
    g = (genre or "").strip()
    for key, aliases in {
        "现代": ["现代", "都市", "当代"],
        "古装": ["古装", "古代", "历史", "宫廷", "宫斗"],
        "仙侠": ["仙侠", "玄幻", "神话", "修真"],
        "悬疑": ["悬疑", "惊悚", "犯罪", "推理"],
        "科幻": ["科幻", "未来", "赛博", "星际"],
        "穿越": ["穿越", "重生", "快穿"],
    }.items():
        if any(a in g for a in aliases):
            return key
    return g


def _genre_items(genre: str, kind: str) -> list[str]:
    g = _norm_genre(genre)
    if not g:
        return []
    prof = _data().get("genre", {}).get(g, {})
    return list(prof.get(kind, []) or [])


def character_negative(genre: str = "") -> str:
    """人物形象负面：基底 + 题材人物差异项 + 平台硬约束（无字幕/水印/Logo）。"""
    base = list(_data().get("base_character", []))
    items = base + _genre_items(genre, "character")
    items.append(sd_manual.negative_zh())
    return _join(items)


def scene_negative(genre: str = "") -> str:
    """场景全景负面：基底 + 题材场景差异项 + 平台硬约束。"""
    base = list(_data().get("base_scene", []))
    items = base + _genre_items(genre, "scene")
    items.append(sd_manual.negative_zh())
    return _join(items)


def _scene_type_neg(scene_type: str) -> list[str]:
    return list(_data().get("scene_type", {}).get(scene_type or "", []) or [])


def _scale_neg(scale: str) -> list[str]:
    """按景别关键词匹配差异项（特写/中景/远景…）。"""
    table = _data().get("scale", {})
    s = scale or ""
    for key in ("大特写", "大远景", "特写", "中近景", "中景", "全景", "远景", "空镜", "主观视角"):
        if key in s:
            return list(table.get(key, []) or [])
    return []


def shot_negative(scene_type: str = "", scale: str = "", speed: dict | None = None,
                  genre: str = "") -> str:
    """视频镜头负面：速度负面(speed_control) + 风格负面(realism_style 精简) + 场景类型差异 + 景别差异 + 平台硬约束。

    genre 参数保留签名兼容，但视频负面聚焦场景/镜头，不叠加题材场景差异（防冗长）；
    题材差异由 character_negative/scene_negative 承担。差异项按 scene_type×scale 命中才追加。
    """
    blocks: list[str] = []
    # 速度负面：复用既有轮子（含慢动作/变形/手部畸形等）
    spd_neg = _speed_neg_zh(speed or {"mode": "real_time", "scope": "", "seconds": None})
    if spd_neg:
        blocks.append(spd_neg)
    # 风格负面：realism_style 精简（只取前 3 项核心 AI 感，避免全量冗长）
    style = realism_style.negative_zh()
    if style:
        style_items = [x.strip() for x in style.replace("NOT ", "", 1).split("、") if x.strip()]
        if style_items:
            blocks.append("NOT " + "、".join(style_items[:3]))
    # 场景类型差异 + 景别差异（视频负面聚焦场景+镜头，不叠加题材场景差异——题材由人物/场景提示词承担）
    extra = _scene_type_neg(scene_type) + _scale_neg(scale)
    # 跨块去重：speed 基底已含 变形/手部畸形/音画不同步/多余角色入画，差异项重复词剔除
    seen = set()
    for b in blocks:
        seen.update(x.strip() for x in b.replace("NOT ", "", 1).split("、") if x.strip())
    extra = [x for x in extra if x not in seen and not any(s in x for s in seen)
             and not any(x in s for s in seen)]  # 2026-08-21 包含去重："角色穿模"含已见"穿模"→剔除
    if extra:
        blocks.append("、".join(extra))
    # 平台硬约束
    blocks.append(sd_manual.negative_zh())
    return _join_blocks(blocks)


def _join(items: list[str]) -> str:
    """顿号拼接并去重（保留首次出现顺序）。"""
    seen: list[str] = []
    for it in items:
        for x in (it or "").split("、"):
            x = x.strip()
            if x and x not in seen:
                seen.append(x)
    return "、".join(seen)


def _join_blocks(blocks: list[str]) -> str:
    """负面块拼接：块间用 '；' 分隔（防 '慢动作NOT 动画感' 粘连），并去重整块。"""
    seen: list[str] = []
    for b in blocks:
        b = b.strip()
        if b and b not in seen:
            seen.append(b)
    return "；".join(seen)
