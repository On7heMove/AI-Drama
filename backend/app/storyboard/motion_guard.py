"""镜头活性长效机制（2026-08-14）：防 PPT 感。

- 静止镜头（固定机位）单镜时长 > static_max_sec → 告警（固定=靠表演撑住，长镜必须给运镜）
- 连续 N 镜固定机位 → 告警（节奏停滞）
- 配置：config/storyboard/motion_guard.json
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.paths import data_root

MOTION_PATH = data_root() / "config" / "storyboard" / "motion_guard.json"

_STATIC = ("固定", "固定机位", "locked", "static")


@lru_cache(maxsize=1)
def load_motion_guard() -> dict:
    if not MOTION_PATH.exists():
        return {"data": {}}
    try:
        return json.loads(MOTION_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"data": {}}


def parse_camera(zh: str) -> tuple[str, float]:
    """从中文提示词【镜头】行解析 (运动, 时长秒)。"""
    cam = next((l for l in zh.splitlines() if l.startswith("【镜头】")), "")
    m = re.search(r"｜([^｜（]*)（约(\d+(?:\.\d+)?)s", cam)
    if not m:
        return "", 0.0
    return m.group(1).strip(), float(m.group(2))


def audit_motion(zh: str) -> list[str]:
    """单镜镜头活性检查：静止 + 超长 → 告警；但含对白/旁白的静止镜由台词撑住，不算PPT。"""
    d = load_motion_guard().get("data", {})
    max_sec = float(d.get("static_max_sec", 3.5))
    mov, dur = parse_camera(zh)
    if any(s in mov for s in _STATIC) and dur > max_sec:
        if "【对白】" in zh or "旁白·原文" in zh:
            return []  # 有台词/旁白=画面内有声表演，长镜合理
        return [f"静止镜头 {dur:.0f}s > {max_sec:.0f}s（固定机位像PPT）→ 缩短到≤{max_sec:.0f}s 或给运镜（缓推/缓拉/跟拍/手持）或画面内运动"]
    return []


def audit_sequence(plans) -> list[str]:
    """连续固定镜检查（按场景）。"""
    d = load_motion_guard().get("data", {})
    max_consec = int(d.get("consecutive_static_max", 2))
    out = []
    for sp in plans:
        run = best = 0
        for zh in getattr(sp, "image_prompts", []) or []:
            mov, _ = parse_camera(zh)
            run = run + 1 if any(s in mov for s in _STATIC) else 0
            best = max(best, run)
        if best > max_consec:
            out.append(f"{sp.scene_id}: 连续 {best} 镜固定机位 > {max_consec}（节奏停滞/PPT感）")
    return out
