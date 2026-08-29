"""快慢镜本地裁决（2026-08-14，2026-08-18 升格范围/时长分级机制化）：

- 升格范围分级（slow_motion_scopes）：impact_only 约1s / causal_chain 约3s（刺入瞬间完整因果链：
  剑入→没入→透出→血涌→僵直）/ sequence 约5s（完整动作序列）；其余实时
- 快动作（降格）：时间压缩/蒙太奇/匆忙追逐（可选）
- 默认实时 1x，并显式禁止慢动作（Neowow 原则：慢动作只在打击点，禁止全程慢动作）

段3升格版由手动重写（约3s完整因果链）改为机制化配置：命中 causal_chain 触发词即得 3s 升格。
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

SPEED_PATH = data_root() / "config" / "storyboard" / "speed_control.json"

# scope 优先级：causal_chain 最重（完整因果链），其次 impact_only，sequence 兜底
_SCOPE_ORDER = ("causal_chain", "impact_only", "sequence")


@lru_cache(maxsize=1)
def load_speed_control() -> dict:
    if not SPEED_PATH.exists():
        return {"data": {}}
    try:
        return json.loads(SPEED_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"data": {}}


def _scopes() -> dict:
    return load_speed_control().get("data", {}).get("slow_motion_scopes", {})


def _scope_seconds(scope_id: str, default: float = 1.0) -> float:
    return float(_scopes().get(scope_id, {}).get("seconds", default))


def _match_scope(action: str) -> str | None:
    """按 _SCOPE_ORDER 匹配动作文本中的升格触发词，返回 scope_id 或 None。"""
    if not action:
        return None
    for scope_id in _SCOPE_ORDER:
        triggers = _scopes().get(scope_id, {}).get("triggers", [])
        if any(k and k in action for k in triggers):
            return scope_id
    return None


def decide_speed(action: str = "", emotion: str = "", scene_type: str = "") -> dict:
    """返回 {mode, seconds, scope, reason}；mode: slow_mo / fast_mo / real_time。

    scope: impact_only / causal_chain / sequence / ""（非升格）。
    """
    d = load_speed_control().get("data", {})
    text = f"{action or ''} {emotion or ''}"
    if any(k in text for k in d.get("fast_motion_triggers", [])):
        return {"mode": "fast_mo", "seconds": None, "scope": "", "reason": "时间压缩/匆忙/追逐（降格）"}
    # 升格范围分级：causal_chain（刺入/贯穿类完整因果链，约3s）优先于 impact_only（约1s）
    scope_id = _match_scope(action or "")
    if scope_id:
        sec = _scope_seconds(scope_id)
        return {"mode": "slow_mo", "seconds": sec, "scope": scope_id,
                "reason": f"关键打击/高光瞬间升格慢动作（{scope_id}，约{sec:.0f}s）"}
    # 普通触发（slow_motion_triggers）：命中且（高情绪 或 动作/情绪场景）→ 回落 impact_only
    is_impact = any(k in (action or "") for k in d.get("slow_motion_triggers", []))
    high_emo = any(e in (emotion or "") for e in d.get("slow_motion_emotions", []))
    if is_impact and (high_emo or scene_type in ("action", "emotion")):
        sec = _scope_seconds("impact_only")
        return {"mode": "slow_mo", "seconds": sec, "scope": "impact_only",
                "reason": "关键打击/高光瞬间升格慢动作（其余实时）"}
    return {"mode": "real_time", "seconds": None, "scope": "", "reason": "实时速度（1x）"}


def render_zh(spd: dict) -> str:
    m = spd.get("mode", "real_time")
    if m == "slow_mo":
        scope = spd.get("scope", "impact_only")
        sec = spd.get("seconds", _scope_seconds(scope))
        label = _scopes().get(scope, {}).get("zh", "关键打击/高光瞬间")
        if scope == "causal_chain":
            return f"升格为主：{label}完整升格约{sec:.0f}s+运动模糊+血雾慢速弥漫，其余实时"
        if scope == "sequence":
            return f"{label}升格慢动作（约{sec:.0f}s）+运动模糊，其余实时"
        return f"关键打击/高光瞬间升格慢动作（约{sec:.0f}s）+运动模糊，其余实时"
    if m == "fast_mo":
        return "快动作（时间压缩感）"
    return "实时速度（1x）"


def render_en(spd: dict) -> str:
    m = spd.get("mode", "real_time")
    if m == "slow_mo":
        scope = spd.get("scope", "impact_only")
        sec = spd.get("seconds", _scope_seconds(scope))
        label = _scopes().get(scope, {}).get("en", "the impact/highlight moment")
        if scope == "causal_chain":
            prefix = "the " if label.startswith("the ") else ""
            core = label[4:] if prefix else label
            return f"slow motion through {prefix}{core} (about {sec:.0f}s) with motion blur and slow-drifting blood mist, then real time"
        if scope == "sequence":
            return f"slow motion for the {label} (about {sec:.0f}s) with motion blur, then real time"
        return f"slow motion only at the impact/highlight moment (about {sec:.0f}s) with motion blur, then real time"
    if m == "fast_mo":
        return "fast motion (time-lapse feel)"
    return "real time (1x), no slow motion"


def negative_zh(spd: dict) -> str:
    base = "NOT 变形、手部畸形、多余肢体、穿模、模糊"  # 2026-08-21 用户规则：删音画不同步/多余角色入画
    if spd.get("mode") == "slow_mo":
        scope = spd.get("scope", "impact_only")
        neg = _scopes().get(scope, {}).get("negative_zh", "")
        return base + f"；{neg}" if neg else base + f"；慢动作仅限打击瞬间（约{spd.get('seconds', 1.0):.0f}s），其余实时"
    return base + "、慢动作"


def negative_en(spd: dict) -> str:
    base = "no deformation, no malformed hands, no extra limbs, no clipping, no blur"  # 2026-08-21 用户规则
    if spd.get("mode") == "slow_mo":
        scope = spd.get("scope", "impact_only")
        neg = _scopes().get(scope, {}).get("negative_en", "")
        return base + "; " + neg if neg else base + f"; slow motion only at the impact moment (about {spd.get('seconds', 1.0):.0f}s), then real time"
    return base + ", no slow motion"


# ---- 升格瞬间联动：声音（音效骤起/撤乐留噪）+ 调度（打击点特写） ----
def augment_sound_zh(sound: str, spd: dict) -> str:
    if spd.get("mode") != "slow_mo":
        return sound
    base = sound or "环境音+对白优先；音乐按情绪起伏"
    if "撤乐" in base or "留噪" in base:
        return base + "；打击瞬间音效骤起强调"
    return base + "；打击瞬间音效强调，音乐可撤乐留噪"


def augment_staging_zh(staging: str, spd: dict) -> str:
    if spd.get("mode") != "slow_mo" or not staging:
        return staging
    if "升格" in staging:
        return staging + "；打击瞬间特写强调"
    return staging + "；打击点特写+升格强调"


def augment_sound_en(sound: str, spd: dict) -> str:
    if spd.get("mode") != "slow_mo":
        return sound
    base = sound or "ambient + dialogue priority; music follows the mood"
    if "drop" in base or "leave" in base:
        return base + "; SFX swell sharply at the impact moment"
    return base + "; SFX emphasis at the impact moment, music can drop to leave noise"


def augment_staging_en(staging: str, spd: dict) -> str:
    if spd.get("mode") != "slow_mo" or not staging:
        return staging
    if "slow motion" in staging.lower() or "升格" in staging:
        return staging + "; impact close-up emphasized"
    return staging + "; impact close-up with slow-motion emphasis"
