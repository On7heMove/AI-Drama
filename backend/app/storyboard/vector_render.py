"""场景向量 → 分镜提示词 完整映射机制（v0.1）。

一个入口：render_prompt(vector, affinity, confidence) → 分镜提示词参数包。
LLM 按契约返回每场景 20 维向量 JSON；本地负责裁决 + 渲染：
  ① 选分支：按 branch_priority 顺序，取第一个 trigger 全命中的镜头语法族
     （FPV 只是 aerial 分支之一，不是独立机制）
  ② 连续参数：FOV/时长/运镜/景别 由通用映射表补全（分支未指定时）
  ③ 一致性检查：aerial 高但开放度低 / chase 高但运动低 → 降置信
  ④ 输出：分支参数包（含负面清单）+ 置信度 + 依据

对齐 shot_motivation 模式：config 驱动、确定性、可单测。
"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "config" / "storyboard" / "vector_render.json"
)


def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def _val(dim: str, vector: dict, affinity: dict) -> float:
    return affinity.get(dim, vector.get(dim, 0.0))


def _match_trigger(trigger: dict, vector: dict, affinity: dict) -> bool:
    for dim, cond in trigger.items():
        v = _val(dim, vector, affinity)
        if "min" in cond and v < cond["min"]:
            return False
        if "max" in cond and v > cond["max"]:
            return False
    return True


def _select_branch(cfg: dict, vector: dict, affinity: dict) -> str:
    for bid in cfg["branch_priority"]:
        trig = cfg["branches"][bid].get("trigger", {})
        if _match_trigger(trig, vector, affinity):
            return bid
    return "default"


def _eval_when(when: str, vector: dict, affinity: dict) -> bool:
    when = when.strip()
    if when == "true":
        return True
    import re
    m = re.match(r"^(\w+)\s*(>=|<=|>|<)\s*([\d.]+)$", when)
    if not m:
        return False
    dim, op, num = m.group(1), m.group(2), float(m.group(3))
    v = _val(dim, vector, affinity)
    return {" >=": v >= num, "<=": v <= num, ">": v > num, "<": v < num}[op]


def _map_fov(cfg: dict, vector: dict, affinity: dict, branch_id: str) -> str:
    br = cfg["branches"][branch_id]
    if br.get("fov"):
        return br["fov"]
    for rule in cfg["fov_map"]:
        if _eval_when(rule["when"], vector, affinity):
            return rule["fov"]
    return "标准 FOV ≈50°"


def _map_bucket(rules: list[dict], value: float, default: str) -> str:
    for r in sorted(rules, key=lambda x: x["max"]):
        if value <= r["max"]:
            return r["value"]
    return default


# affinity 主导 → scene 兜底（LLM 契约未传 scene 时用）
_SCENE_FROM_AFFINITY = {
    "combat": "battle_firefight", "dialogue": "dialogue", "standoff": "confrontation",
    "monologue": "monologue", "chase": "chase", "intimacy": "intimacy",
    "ceremony": "ceremony", "empty": "establishing", "aerial": "aerial",
}


def _infer_scene(affinity: dict) -> str:
    top = max(affinity, key=affinity.get) if affinity else "establishing"
    return _SCENE_FROM_AFFINITY.get(top, "establishing")


def _calc_duration(cfg: dict, vector: dict, affinity: dict,
                   scene: str | None = None, platform: str = "short_vertical") -> str:
    """四变量精准时长（shot-duration-cut-rate）：T = 类型基准 × 信息密度 × 情绪 × 平台。
    生产粒度 0.1s；输出 '期望秒（±30%区间）'。scene 缺省时按 affinity 主导兜底。"""
    scene = scene or _infer_scene(affinity)
    gen = cfg.get("generic", {})
    base = gen.get("duration_base_by_scene", {}).get(scene, 3.0)

    # 信息密度：主体数/景别/新信息
    df = 1.0
    sc = vector.get("subject_count", 0.33)
    if sc >= 0.67:
        df *= 1.3                      # 多主体/群像
    elif sc <= 0.17:
        df *= 0.8                      # 空镜/单一主体
    if vector.get("spatial_openness", 0.5) >= 0.7:
        df *= 1.15                     # 大景别细节多
    if vector.get("novelty", 0.4) >= 0.7:
        df *= 1.1                      # 需细读新信息

    # 全景建场判定：大景别 + 多主体 = 信息超密集建场镜（如战场全景/千人场面）
    # 此类镜头信息吸收优先于节奏：用建场基准、爆发不压短
    wide = (vector.get("spatial_openness", 0.5) >= 0.7
            and vector.get("subject_count", 0.33) >= 0.67)
    if wide:
        base = max(base, 4.0)     # 建场全景基准至少 4.0s（覆盖 scene 短基准）
        df *= 1.2                 # 全景细节再上调
        ef = 1.0                  # 不因爆发压短
    else:
        # 情绪：综合强度 → 爆发压短 / 酝酿拉长
        e = (vector.get("tension", 0.5) + vector.get("arousal", 0.5)
             + vector.get("pacing_need", 0.5)) / 3.0
        ef = 0.7 if e >= 0.75 else (1.3 if e <= 0.3 else 1.0)

    pf = gen.get("duration_platform_factor", {}).get(platform, 0.85)
    t = base * df * ef * pf
    expect = round(t, 1)
    lo, hi = round(t * 0.7, 1), round(t * 1.3, 1)
    return f"{expect}s（{lo}-{hi}s）"


def render_prompt(vector: dict, affinity: dict, confidence: float = 0.8,
                    scene: str | None = None, platform: str = "short_vertical") -> dict:
    """完整机制入口：LLM 的 20 维 JSON → 分镜提示词参数包（本地裁决+渲染）。"""
    cfg = _load_config()
    issues: list[str] = []

    # 一致性检查（裁决层）
    if affinity.get("aerial", 0) >= 0.6 and vector.get("spatial_openness", 0.5) < 0.5:
        issues.append("aerial 高但 spatial_openness<0.5（缺飞行空间），降置信")
    if affinity.get("chase", 0) >= 0.6 and vector.get("motion_level", 0.5) < 0.4:
        issues.append("chase 高但 motion_level 低 → 疑似慢跟踪，降置信")

    branch_id = _select_branch(cfg, vector, affinity)
    branch = cfg["branches"][branch_id]
    params = dict(branch.get("params", {}))

    # 连续参数补全（分支未指定才用通用映射）
    params.setdefault("FOV/视场角", _map_fov(cfg, vector, affinity, branch_id))
    params.setdefault(
        "单镜时长",
        _calc_duration(cfg, vector, affinity, scene, platform),
    )
    params.setdefault(
        "运镜",
        _map_bucket(cfg["generic"]["movement_by_motion"], vector.get("motion_level", 0.5), "固定"),
    )
    params.setdefault(
        "景别分布",
        _map_bucket(cfg["generic"]["scale_by_subject"], vector.get("subject_count", 0.33), "中景为主"),
    )

    base_conf = 0.9 if not issues else 0.7
    conf = round(min(base_conf, confidence), 2)
    return {
        "branch": branch_id,
        "name_zh": branch["name_zh"],
        "params": params,
        "confidence": conf,
        "issues": issues,
        "inputs": {"vector": vector, "affinity": affinity,
                  "scene": scene or _infer_scene(affinity), "platform": platform},
    }
