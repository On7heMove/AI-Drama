"""分镜导演管线（后台默认逻辑，2026-08-18 收敛落盘）。

把此前逐次打补丁的机制统一为一条默认启用的管线，运行在统一 ShotContext 上：
- P1 空间状态：人物状态向量 + 关系约束（BEHIND_CLOSE/EAR_WHISPER/CONTACT…）
- P2 情绪推理：emotion_infer → 状态.情绪（后台）
- P3 镜头动机：shot_motivation → 动机+正当理由（后台，护城河）
- P5 动作形态：action_detail（关系驱动）→ 物理链/反应链（可见）
- P6 对白剪辑：长对白扩展 + J/L-Cut（可见）
- P7 合规包：sd_manual 负面（可见）

原则：每阶段是纯函数 ctx->ctx；默认全开、无开关；可见/后台分离；
新增机制=新增一个 stage，而不是在导出函数里再打一个洞。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.storyboard import emotion_infer, shot_motivation


# ---------------- P1 空间状态模型（源自已验证原型） ----------------
@dataclass
class CharState:
    cid: str
    zone: str = "mid"          # front/mid/back
    side: str = "center"       # left/right/center
    facing: str = ""           # front/back/toward:<id>
    posture: str = "standing"  # standing/kneeling/rigid/collapsed/leaning
    prop: str = ""             # 手持道具
    prop_state: str = ""       # 道具状态（如 剑身没入X）
    injury: str = ""           # 伤口部位
    blood: str = ""            # 出血状态
    mobility: str = "free"     # free/受限/僵直
    gaze: str = ""

    def clone(self) -> "CharState":
        return CharState(**{k: v for k, v in self.__dict__.items()})


@dataclass
class Relation:
    rtype: str                 # BEHIND_CLOSE / FACE_TO_FACE / EAR_WHISPER / CONTACT
    actor: str
    target: str = ""
    detail: str = ""


def _parse_behind_close(context: str) -> Relation | None:
    m = re.search(r"([\u4e00-\u9fff]{1,4})冲到([\u4e00-\u9fff]{2,4})身后", context)
    if m:
        return Relation("BEHIND_CLOSE", m.group(1), m.group(2), "A在B身后，面向B背部")
    return None


def build_states(scene, seg) -> dict[str, CharState]:
    parts = list(dict.fromkeys(getattr(scene, "participants", []) or []))
    return {p: CharState(p) for p in parts}


def derive_relations(context: str, states: dict[str, CharState]) -> list[Relation]:
    rs: list[Relation] = []
    bc = _parse_behind_close(context)
    if bc:
        rs.append(bc)
    m2 = re.search(r"([\u4e00-\u9fff]{1,4})凑近([\u4e00-\u9fff]{2,4})耳旁", context)
    if m2:
        rs.append(Relation("EAR_WHISPER", m2.group(1), m2.group(2), "A嘴->B耳"))
    if "贯穿" in context or "刺入" in context:
        sword = next((s for s in states.values() if s.prop), None)
        victim = next((s for s in states.values() if s.injury), None)
        if sword and victim:
            rs.append(Relation("CONTACT", sword.cid, victim.cid, sword.prop + "->伤口"))
    return rs


# ---------------- ShotContext：统一状态载体 ----------------
@dataclass
class ShotContext:
    scene: object = None
    seg: object = None
    next_seg: object = None
    states: dict = field(default_factory=dict)
    relations: list = field(default_factory=list)
    emotion: str = ""                 # 输入情绪或推理（后台）
    emotion_inferred: str = ""        # 推理情绪（后台）
    motivation: object = None         # 镜头动机（后台）
    justification_zh: str = ""        # 动机形式学（后台）
    cam_zh: str = ""                  # 镜头·连续（可见）
    visual_zh: str = ""               # 画面·连续动作（可见）
    staging_extra: str = ""           # 调度扩展（可见）
    edit_zh: str = ""                 # 剪辑（可见，含 J/L-Cut）
    expanded: bool = False            # 是否长对白扩展
    blocking_zh: str = ""             # 层级站位（可见，仪式类）
    forbidden_zh: str = ""            # 绝对禁止清单（可见）
    character_zh: str = ""          # 角色形象（可见，跨段一致）
    character_en: str = ""          # 角色形象·英文（可见）


def _seg() -> object:
    from app.production import segment_export as _s
    return _s


# ---------------- 各阶段（纯函数 ctx -> ctx） ----------------
def stage_spatial(ctx: ShotContext) -> ShotContext:
    ctx.states = build_states(ctx.scene, ctx.seg)
    joined = "；".join((b.action or "") for b in ctx.seg.beats)
    ctx.relations = derive_relations(joined, ctx.states)
    return ctx


def stage_emotion(ctx: ShotContext) -> ShotContext:
    seg = ctx.seg
    joined = "；".join((b.action or "") for b in seg.beats)
    has_dlg = any(b.dialogue for b in seg.beats)
    e = emotion_infer.infer_emotion(joined, "", getattr(ctx.scene, "scene_type", "") or "")
    if not has_dlg and not ctx.emotion:
        ctx.emotion_inferred = e.emotion
        ctx.emotion = e.emotion
    return ctx


def stage_motivation(ctx: ShotContext) -> ShotContext:
    seg = ctx.seg
    joined = "；".join((b.action or "") for b in seg.beats)
    m = shot_motivation.derive_motivation(
        summary=getattr(ctx.scene, "summary", "") or "",
        action=joined, emotion=ctx.emotion or "",
        scene_type=getattr(ctx.scene, "scene_type", "") or "",
        has_dialogue=any(b.dialogue for b in seg.beats),
        index=0, total=1, location=getattr(ctx.scene, "location", "") or "")
    ctx.motivation = m
    ctx.justification_zh = m.justification_zh
    return ctx


def stage_visual(ctx: ShotContext) -> ShotContext:
    """P5 动作形态（action_detail 补全）+ P6 长对白扩展：委托 segment_export 既有逻辑。"""
    s = _seg()
    exp = s._expand_long_dialogue(ctx.scene, ctx.seg)
    ctx.expanded = bool(exp)
    ctx.staging_extra = exp["staging_extra_zh"] if exp else ""
    dur = ctx.seg.duration
    if exp:
        ctx.visual_zh = exp["visual_zh"]
        ctx.cam_zh = exp["cam_zh"]
    else:
        ctx.visual_zh = f"{s.actions_zh(ctx.seg, ctx.scene)}｜地点：{ctx.scene.location}｜{ctx.scene.time}"
        ctx.cam_zh = (f"机位：{s.camera_chain_zh(ctx.seg)}｜景别：{s._chain(ctx.seg, 'scale')}｜角度：{s._chain(ctx.seg, 'angle')}"
                      f"｜运动：{s._chain(ctx.seg, 'movement')}（段内单次连续运镜，约{dur:.0f}s）")
    return ctx


def stage_edit(ctx: ShotContext) -> ShotContext:
    """P6 剪辑 + J/L-Cut（长对白扩展段 L-Cut 已内嵌画面，不再重复）。"""
    s = _seg()
    ctx.edit_zh = s._cut_zh(ctx.seg, ctx.next_seg)
    if not s._expand_long_dialogue(ctx.scene, ctx.seg):
        m = shot_motivation.derive_motivation(
            summary=getattr(ctx.scene, "summary", "") or "",
            action="；".join((b.action or "") for b in ctx.seg.beats),
            emotion=ctx.emotion or "", scene_type=getattr(ctx.scene, "scene_type", "") or "",
            has_dialogue=any(b.dialogue for b in ctx.seg.beats),
            index=0, total=1, location=getattr(ctx.scene, "location", "") or "")
        if m.jl_cut:
            ctx.edit_zh += "；" + m.jl_cut
    return ctx



def stage_spatial_layout(ctx: ShotContext) -> ShotContext:
    """P1 空间层级（仪式/典礼类）：场景级空间因果链 + 角色状态快照（跨段连续，复用前期状态机）。"""
    from app.storyboard import spatial_layout as _sl
    from app.storyboard.beat_events import plan_states
    states = plan_states(ctx.scene).get(ctx.seg.seg_index, None)
    block = _sl.build_spatial_block_scene(ctx.scene, ctx.seg, states=states)
    if block:
        ctx.blocking_zh = block["blocking_zh"]
        ctx.forbidden_zh = block["forbidden_zh"]
        if ctx.forbidden_zh:
            ctx.staging_extra += ("；" if ctx.staging_extra else "") + ctx.forbidden_zh
    return ctx
def stage_character(ctx: ShotContext) -> ShotContext:
    """角色形象锁定：按参与者匹配形象卡，输出跨段一致的【人物形象】（可见，供SD生成+人工验证）。"""
    from app.storyboard.character_profile import profiles_zh, profiles_en
    names = list(dict.fromkeys(getattr(ctx.scene, "participants", []) or []))
    ctx.character_zh = profiles_zh(names)
    ctx.character_en = profiles_en(names)
    return ctx


def stage_negative(ctx: ShotContext) -> ShotContext:
    """P7 合规包：SD 手册负面词（委托导出拼接，统一入口）。"""
    return ctx


_STAGES = (stage_spatial, stage_spatial_layout, stage_emotion, stage_motivation, stage_visual, stage_edit, stage_character, stage_negative)


def run_segment_pipeline(scene, seg, next_seg=None) -> ShotContext:
    ctx = ShotContext(scene=scene, seg=seg, next_seg=next_seg)
    for stage in _STAGES:
        ctx = stage(ctx)
    return ctx