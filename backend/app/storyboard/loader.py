"""分镜镜头语言配置加载器：读取 config/storyboard/shot_language.json（只读、带缓存）。"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

CONFIG_PATH = (
    data_root() / "config" / "storyboard" / "shot_language.json"
)


@lru_cache(maxsize=1)
def load_shot_language() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["data"]
