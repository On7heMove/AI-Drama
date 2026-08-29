"""摄影技术参数推断（本地映射，确定性）：cinema_spec 配置包 + 既有引擎结构化键 → 技术参数。

复用声明（AGENTS.md §0.5.1）：
- scene_type → SceneClassifier（复用）
- scale 景别键 → shot_language.json shots 反查（复用，不另建景别表）
- time/interior → ParsedScene（复用）
- emotion → emotion_infer 输出（复用）
- 光源词 → 剧情原文关键词（复用 emotion_infer.json triggers 机制）
镜头/调度/构图/转场仍由 shot_selector/shot_language 提供，本模块只做参数层，不重复。
所有输出落在 cinema_spec.runtime_bounds 值域内，非法回退 fallback（模型提议本地裁决的兜底）。

用法：
    lp = infer_lighting(scene_type="action", time="夜", interior="外", emotion="恐惧", source_text="霓虹灯")
    op = infer_optics("中景")
    lighting_suffix(lp)   -> "｜色温6500K｜照度250lux｜光比8:1｜方向顶侧冷光(月光)｜光质硬｜阴影浓｜特殊：冷调月光"
    lens_suffix(op)       -> "｜35mm T4"
    tech_spec_line(time="夜", aspect="9:16") -> "【技术规格】画幅9:16｜8K拍摄4K成片｜24fps｜180°快门｜ISO 3200｜LogC｜..."
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json

from app.paths import data_root
from app.storyboard.loader import load_shot_language

CONFIG_PATH = data_root() / "config" / "storyboard" / "cinema_spec.json"

# 时间/内外归一化（ParsedScene.time/interior 可能为 晨/日/昏/夜、内/外 或变体）
_TIME_MAP = {
    "晨": "晨", "清晨": "晨", "黎明": "晨", "拂晓": "晨", "凌晨": "晨",
    "日": "日", "白天": "日", "白昼": "日", "正午": "日", "中午": "日",
    "昏": "昏", "黄昏": "昏", "傍晚": "昏", "日落": "昏", "夕阳": "昏",
    "夜": "夜", "夜晚": "夜", "晚上": "夜", "夜里": "夜", "深夜": "夜",
}
_INTERIOR_MAP = {
    "内": "内", "室内": "内", "内景": "内", "屋内": "内", "房内": "内",
    "外": "外", "室外": "外", "外景": "外",
}
# 光比/焦距/T值输出取整用的边界档（配置包区间多为标准档，见 cinema_spec.source.basis）
_T_STOPS = [1.8, 2.0, 2.8, 4.0, 5.6, 8.0]


@dataclass
class OpticsParams:
    """单镜光学参数（按景别本地映射）。"""
    focal_mm: int
    tstop: float
    depth_of_field: str


@dataclass
class LightingParams:
    """场景灯光参数（按 scene_type×time×interior×emotion×光源词 本地映射）。"""
    color_temp_k: int
    illuminance_lux: int
    ratio: float
    direction: str
    quality: str
    shadow: str
    special: str = ""
    conservative: bool = False  # True=既有光线已含具体光效且未命中触发器：只追加 光比/阴影，避免与剧情光效矛盾


@lru_cache(maxsize=1)
def load_cinema_spec() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["data"]


@lru_cache(maxsize=1)
def _scale_key_map() -> dict[str, str]:
    """中文景别 → 英文键（复用 shot_language.json shots，不另建景别表）。"""
    shots = load_shot_language().get("shots", {})
    return {v.get("scale", ""): k for k, v in shots.items() if v.get("scale")}


def _normalize_time(t: str) -> str:
    if not t:
        return "未知"
    return _TIME_MAP.get(t.strip(), "未知")


def _normalize_interior(i: str) -> str:
    if not i:
        return "未知"
    return _INTERIOR_MAP.get(i.strip(), "未知")


def _scale_key(scale_zh: str) -> str:
    if not scale_zh:
        return ""
    return _scale_key_map().get(scale_zh.strip(), "")


def _mid(values: list | tuple) -> float:
    return (float(values[0]) + float(values[1])) / 2.0


def _clamp(value: float, lo: float, hi: float, fallback: float) -> float:
    if not (lo <= value <= hi):
        return fallback
    return value


def _pick_tstop(lo: float, hi: float) -> float:
    """T 值取区间上限（标准档，对齐《初入江湖》特写T2.8/中景T4/全景T4-5.6/远景T5.6-8）。"""
    v = hi
    return min(_T_STOPS, key=lambda x: abs(x - v))


def infer_optics(scale_zh: str = "") -> OpticsParams:
    """按景别（中文，来自镜头行）映射 焦距/T值/景深。未知景别回退 fallback（35mm/T2.8）。"""
    spec = load_cinema_spec()
    bounds = spec["runtime_bounds"]
    fb = bounds["fallback"]
    key = _scale_key(scale_zh)
    focal_range = spec["optics"]["scale_to_focal_mm"].get(key)
    tstop_range = spec["optics"]["scale_to_tstop"].get(key)
    if not focal_range or not tstop_range:
        return OpticsParams(
            focal_mm=int(_clamp(fb["focal_mm"], *bounds["focal_mm"], fb["focal_mm"])),
            tstop=float(_clamp(fb["tstop"], *bounds["tstop"], fb["tstop"])),
            depth_of_field="中",
        )
    focal = int(focal_range[0])  # 取广角端（全景叙事）
    tstop = _pick_tstop(float(tstop_range[0]), float(tstop_range[1]))
    dof_sets = spec["optics"]["depth_of_field"]
    dof = ("浅" if key in dof_sets.get("shallow", [])
           else "深" if key in dof_sets.get("deep", []) else "中")
    return OpticsParams(
        focal_mm=int(_clamp(focal, *bounds["focal_mm"], fb["focal_mm"])),
        tstop=float(_clamp(tstop, *bounds["tstop"], fb["tstop"])),
        depth_of_field=dof,
    )


def infer_lighting(
    *,
    scene_type: str = "",
    time: str = "",
    interior: str = "",
    emotion: str = "",
    source_text: str = "",
    existing_lighting: str = "",
) -> LightingParams:
    """场景灯光本地映射：time×interior 基线 → scene_type 光比 → emotion 覆盖 → 光源词覆盖。

    existing_lighting：既有引擎/LLM 已产出的【光线】文本（复用既有成果，纳入光源词匹配）。
    匹配规则：① 触发器命中 → 覆盖基础光；② 未命中但既有光线已含具体光源词 →
    保守模式（只追加 光比/阴影，不追加色温/照度/方向/光质），杜绝与剧情光效矛盾。
    全部输出经 runtime_bounds 值域裁决，非法回退 fallback（模型提议本地裁决的兜底）。
    """
    spec = load_cinema_spec()
    bounds = spec["runtime_bounds"]
    fb = bounds["fallback"]
    base = spec["lighting"]["time_interior_to_baseline"].get(
        f"{_normalize_time(time)}+{_normalize_interior(interior)}",
        spec["lighting"]["time_interior_to_baseline"]["未知"],
    )
    # 场景类型 → 光比（取上限：更强对比，戏剧可读）
    ratio = spec["lighting"]["scene_type_ratio"].get(scene_type or "unknown", [3, 3])[1]
    # 情绪 → 光比/光质覆盖（写回 base，触发器后覆盖仍生效）
    adj = spec["lighting"]["emotion_ratio_adjust"].get(emotion or "未知")
    if adj:
        ratio = adj["ratio"][1]
        base["quality"] = adj.get("quality") or base["quality"]
    # 剧情光源词 → 覆盖基础光（第一条命中，顺序=配置包 source_triggers 顺序）
    # 匹配文本 = 剧本原文 + 既有引擎产出的【光线】文本（复用既有成果）
    special = ""
    conservative = False
    src = " ".join(x for x in (source_text or "", existing_lighting or "") if x)
    _hit = False
    for _name, trig in spec["lighting"]["source_triggers"].items():
        if _name == "note":
            continue
        if any(kw in src for kw in trig.get("keywords", [])):
            lt = trig["light"]
            base = {**base, **{k: v for k, v in lt.items() if k in ("color_temp_k", "illuminance_lux", "quality")}}
            special = lt.get("special", "")
            _hit = True
            break
    if not _hit and existing_lighting and _has_concrete_light_word(existing_lighting):
        # 既有光线已含具体光效（如 抽象光效/裂缝/强光），未命中触发器 → 保守模式：
        # 只追加 光比/阴影，不追加基线色温/照度/方向/光质（避免与剧情光效矛盾）
        conservative = True
    color_temp = int(_mid(base["color_temp_k"]))
    illum = int(_mid(base["illuminance_lux"]))
    ratio = float(ratio)
    return LightingParams(
        color_temp_k=int(_clamp(color_temp, *bounds["color_temp_k"], fb["color_temp_k"])),
        illuminance_lux=int(_clamp(illum, *bounds["illuminance_lux"], fb["illuminance_lux"])),
        ratio=float(_clamp(ratio, *bounds["ratio"], fb["ratio"])),
        direction=base.get("direction", fb["direction"]),
        quality=base.get("quality") or fb["quality"],
        shadow=base.get("shadow", fb["shadow"]),
        special=special,
        conservative=conservative,
    )


_GENERIC_LIGHT_WORDS = (
    "环境光", "自然光", "常规", "日常", "基础", "中性光", "顶光", "侧光", "逆光",
    "主光", "补光", "背景光", "暗光", "弱光", "轮廓光", "辅助光",
)


def _has_concrete_light_word(text: str) -> bool:
    """既有光线文本是否含具体光源/光效词（触发保守模式，排除通用布光词）。"""
    t = text or ""
    for g in _GENERIC_LIGHT_WORDS:
        t = t.replace(g, "")
    return any(kw in t for kw in ("光", "灯", "焰", "闪", "裂缝", "辉", "晕"))


def lighting_suffix(p: LightingParams) -> str:
    """【光线】行追加段（参数细化到标准深度）。保守模式只追加 光比/阴影。"""
    if p.conservative:
        return f"｜光比{p.ratio:g}:1｜阴影{p.shadow}"
    parts = [
        f"色温{p.color_temp_k}K",
        f"照度{p.illuminance_lux}lux",
        f"光比{p.ratio:g}:1",
        f"方向{p.direction}",
        f"光质{p.quality}",
        f"阴影{p.shadow}",
    ]
    if p.special:
        parts.append(f"特殊：{p.special}")
    return "｜" + "｜".join(parts)


def lens_suffix(p: OpticsParams) -> str:
    """镜头链每镜行追加段（镜头→参数 1:1 绑定）。"""
    return f"｜{p.focal_mm}mm T{p.tstop:g}｜景深{p.depth_of_field}"


def tech_spec_line(*, time: str = "", interior: str = "", aspect: str = "9:16") -> str:
    """段级【技术规格】行（2026-08-21 用户规则：技术参数对生成无效且自相矛盾——只保留一句话）。
    原参数族（画幅/分辨率/帧率/ISO/Log/胶片/编码/镜头组）降级为配置数据（cinema_spec 保留），交付不输出。"""
    return "【技术规格】电影感布光，浅景深，真实自然质感"