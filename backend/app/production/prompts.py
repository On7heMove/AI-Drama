"""生产级提示词/配置加载：config/production/ 下的模板与 JSON（只读、带缓存）。"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

CONFIG_DIR = data_root() / "config" / "production"
PROMPTS_DIR = CONFIG_DIR / "prompts"


@lru_cache(maxsize=1)
def load_json(name: str) -> dict:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as f:
        obj = json.load(f)
    return obj["data"] if isinstance(obj, dict) and "data" in obj else obj


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    with (PROMPTS_DIR / f"{name}.md").open("r", encoding="utf-8") as f:
        return f.read()


def fill(template: str, **kw: str) -> str:
    """用 {{KEY}} 占位符填充模板（避免与 JSON 花括号冲突）。"""
    for k, v in kw.items():
        template = template.replace("{{" + k.upper() + "}}", v)
    return template


def get_text(data: dict, key: str, default: str = "") -> str:
    """把列表/字典配置渲染为给 LLM 的文本摘要。"""
    v = data.get(key, default)
    if isinstance(v, list):
        return "\n".join(f"- {x}" for x in v if x)
    if isinstance(v, dict):
        return "\n".join(f"- {k}：{v}" for k, v in v.items())
    return str(v)