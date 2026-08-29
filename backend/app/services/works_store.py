"""作品存储服务：文件系统持久化（runtime_dir()/works/{work_id}/）。

结构：
  meta.json       # {id,title,genre,created_at,updated_at,status}
  script.txt      # 剧本文本
  prompts/{type}.json      # 当前提示词 {items:[...], version:N, updated_at}
  versions/{type}/{n}.json # 历史快照（编辑保存时自动版本）
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app.paths import runtime_dir

WORKS_ROOT = runtime_dir() / "works"
_TYPES = ("plot", "characters", "scenes", "video")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _safe_id(title: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff-]+", "", title)[:24] or "work"
    return f"{base}_{int(time.time())}"


def _work_dir(work_id: str) -> Path:
    return WORKS_ROOT / work_id


# ---------- 作品 CRUD ----------

def list_works() -> list[dict]:
    if not WORKS_ROOT.exists():
        return []
    out = []
    for d in sorted(WORKS_ROOT.iterdir(), key=lambda p: p.name):
        meta = d / "meta.json"
        if meta.exists():
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    return out


def get_work(work_id: str) -> dict | None:
    meta = _work_dir(work_id) / "meta.json"
    if not meta.exists():
        return None
    return json.loads(meta.read_text(encoding="utf-8"))


def create_work(title: str, genre: str = "", aspect: str = "9:16", brief: dict | None = None) -> dict:
    work_id = _safe_id(title)
    d = _work_dir(work_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompts").mkdir(exist_ok=True)
    now = _now()
    meta = {"id": work_id, "title": title, "genre": genre, "aspect": aspect,
            "status": "draft", "created_at": now, "updated_at": now}
    if brief:
        meta["brief"] = brief
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def delete_work(work_id: str) -> bool:
    d = _work_dir(work_id)
    if not (d / "meta.json").exists():
        return False
    import shutil
    shutil.rmtree(d)
    return True


def update_meta(work_id: str, **kw) -> dict | None:
    meta = get_work(work_id)
    if meta is None:
        return None
    meta.update(kw)
    meta["updated_at"] = _now()
    ( _work_dir(work_id) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ---------- 剧本 ----------

def save_script(work_id: str, text: str) -> dict | None:
    meta = get_work(work_id)
    if meta is None:
        return None
    (_work_dir(work_id) / "script.txt").write_text(text, encoding="utf-8")
    return update_meta(work_id, status="script_ready")


def get_script(work_id: str) -> str:
    p = _work_dir(work_id) / "script.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- 提示词 ----------

def _type_path(work_id: str, ptype: str) -> Path:
    return _work_dir(work_id) / "prompts" / f"{ptype}.json"


def save_prompts(work_id: str, ptype: str, items: list) -> dict | None:
    if ptype not in _TYPES:
        raise ValueError(f"未知提示词类型: {ptype}")
    meta = get_work(work_id)
    if meta is None:
        return None
    path = _type_path(work_id, ptype)
    data = {"type": ptype, "items": items, "version": 1, "updated_at": _now()}
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = int(old.get("version", 0)) + 1
        _save_version(work_id, ptype, old)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    update_meta(work_id, status="generated")
    return data


def get_prompts(work_id: str, ptype: str) -> dict | None:
    path = _type_path(work_id, ptype)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def get_all_prompts(work_id: str) -> dict:
    out = {}
    for t in _TYPES:
        d = get_prompts(work_id, t)
        if d:
            out[t] = d
    return out


def _versions_dir(work_id: str, ptype: str) -> Path:
    d = _work_dir(work_id) / "versions" / ptype
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_version(work_id: str, ptype: str, data: dict) -> None:
    n = int(data.get("version", 0))
    (_versions_dir(work_id, ptype) / f"v{n}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_versions(work_id: str, ptype: str) -> list[dict]:
    d = _work_dir(work_id) / "versions" / ptype
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("v*.json"), key=lambda p: p.name):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def restore_version(work_id: str, ptype: str, version: int) -> dict | None:
    f = _work_dir(work_id) / "versions" / ptype / f"v{version}.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    return save_prompts(work_id, ptype, data.get("items", []))
