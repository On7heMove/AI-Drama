"""角色形象卡（2026-08-18，配置 config/storyboard/character_profile.json）。

场景级角色形象锁定：从剧本人物设定提炼，跨段一致注入提示词【人物形象】行。
原则：忠实原文、只锁剧本已设定细节，不编造；性别/发型/体态/衣着/气质为可验证锚点。
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "character_profile.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


def profile_zh(name: str) -> str:
    return _data().get("profiles", {}).get(name, {}).get("zh", "")


def profile_en(name: str) -> str:
    return _data().get("profiles", {}).get(name, {}).get("en", "")


def profiles_zh(names: list[str]) -> str:
    """按参与者顺序输出形象行（中文），未知角色跳过。"""
    parts = [profile_zh(n) for n in (names or []) if profile_zh(n)]
    return "；".join(parts)


def profiles_en(names: list[str]) -> str:
    parts = [profile_en(n) for n in (names or []) if profile_en(n)]
    return "; ".join(parts)
