"""真人实拍风格锁定：风格行 + AI感负面（配置 config/storyboard/realism_style.json）。"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "realism_style.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


def _aspect_key(prefix: str, aspect: str) -> str:
    """画幅 → 配置键名：9:16/16:9 用下划线（style_lock_9_16），其余保留冒号（style_lock_2.39:1）。"""
    if aspect in ("9:16", "16:9"):
        return f"{prefix}{aspect.replace(':', '_')}"
    return f"{prefix}{aspect}"


def style_lock_zh(aspect: str) -> str:
    """按画幅取中文风格锁定（支持 8 种：9:16/16:9/2.39:1/2.35:1/21:9/1.85:1/4:3/1:1）。"""
    key = _aspect_key("style_lock_", aspect) if aspect in _ASPECTS else "style_lock_9_16"
    return _data().get(key, "") or _data().get("style_lock_9_16", "")


def style_lock_en(aspect: str) -> str:
    """按画幅取英文风格锁定（支持 8 种）。"""
    key = _aspect_key("style_lock_en_", aspect) if aspect in _ASPECTS else "style_lock_en_9_16"
    return _data().get(key, "") or _data().get("style_lock_en_9_16", "")


# 支持的全部画幅（与 cinema_spec.aspect_ratio.options 一致）
_ASPECTS = {"9:16", "16:9", "2.39:1", "2.35:1", "21:9", "1.85:1", "4:3", "1:1"}


def negative_zh() -> str:
    neg = _data().get("negative_zh", [])
    return ("NOT " + "、".join(neg)) if neg else ""


def negative_en() -> str:
    neg = _data().get("negative_en", [])
    return ("; " + ", ".join("no " + x for x in neg)) if neg else ""
