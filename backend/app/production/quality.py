"""质检：爆点/钩子/情绪/四线（确定性）+ 逻辑（复用 DetectionEngine）+ 反转（LLM 可选）。

严重度映射：engine 的 error→fatal（硬错误，必回炉）；strong→error；suggestion→suggestion。
"""
from __future__ import annotations

from app.config import settings
from app.engine.detector import DetectionEngine
from app.production.bible_check import check_bible_constraints
from app.production.prompts import load_json, load_prompt
from app.production.schemas import EpisodeScript, Outline, QualityItem, QualityReport
from app.prompt_factory import build_user
from app.quality.emotion_pacing import pacing_from_text
from app.schemas.events import Event, EventType, TimeRef
from app.schemas.issues import Severity
from app.storyboard.schemas import SceneInput
from app.storyboard.shot_selector import ShotSelector

EXPLOSION_TYPES = {
    "反转", "强冲突", "情绪顶点", "信息炸弹", "身份揭露", "威胁升级", "关系破裂",
}

KNOWN_EVENT_TYPES = {t.value for t in EventType}


def check_structure(outline: Outline, episodes: list[EpisodeScript], rubric: dict) -> list[QualityItem]:
    """确定性结构检查：结尾钩子/开场钩/爆点密度/情绪曲线/四线推进。"""
    items: list[QualityItem] = []
    beats = {b.ep: b for b in outline.beats}
    eps = {e.ep: e for e in episodes}

    for ep in sorted(set(beats) | set(eps)):
        beat = beats.get(ep)
        script = eps.get(ep)
        if not script:
            items.append(QualityItem(ep=ep, dimension="structure", passed=False, severity="fatal",
                                     evidence="未生成该集", suggestion="补生成该集"))
            continue
        if not (script.hook_end or (beat and beat.hook_end)):
            items.append(QualityItem(ep=ep, dimension="cliffhanger", passed=False, severity="fatal",
                                     evidence=f"第{ep}集结尾钩子为空", suggestion="补一个结尾钩子（悬念/选择/威胁/反转预告）"))
        if not (script.hook_open or (beat and beat.hook_open)):
            items.append(QualityItem(ep=ep, dimension="open_hook", passed=False, severity="error",
                                     evidence=f"第{ep}集开场钩为空", suggestion="补开场 30 秒钩"))
        expl = script.explosion or (beat.explosion if beat else "") or ""
        if not expl:
            items.append(QualityItem(ep=ep, dimension="explosion", passed=False, severity="error",
                                     evidence=f"第{ep}集无爆点", suggestion="预埋爆点（反转/强冲突/情绪顶点/信息炸弹/身份揭露/威胁升级/关系破裂）"))

    # 爆点密度：任意连续两集至少一个爆点（硬指标）
    ep_nums = sorted(set(beats) | set(eps))
    for i in range(len(ep_nums) - 1):
        a, b = ep_nums[i], ep_nums[i + 1]
        if a + 1 != b:
            continue
        ha = (eps.get(a).explosion if eps.get(a) else "") or (beats[a].explosion if a in beats else "")
        hb = (eps.get(b).explosion if eps.get(b) else "") or (beats[b].explosion if b in beats else "")
        if not (ha or hb):
            items.append(QualityItem(ep=b, dimension="explosion_density", passed=False, severity="fatal",
                                     evidence=f"第{a}与第{b}集均无爆点", suggestion="至少每两集一个爆点"))

    # 情绪曲线：大纲每集至少 3 拍（起→升→钩）
    for b in outline.beats:
        if len(b.emotional_curve) < 3:
            items.append(QualityItem(ep=b.ep, dimension="emotion", passed=False, severity="error",
                                     evidence=f"第{b.ep}集情绪曲线仅 {len(b.emotional_curve)} 拍",
                                     suggestion="补足 起→升→钩 三拍情绪曲线"))

    # 四线推进：每 10 集至少一条线被推进
    if outline.beats:
        for block_start in range(1, 81, 10):
            block = [b for b in outline.beats if block_start <= b.ep < block_start + 10]
            if block and not any(b.lines_advanced for b in block):
                items.append(QualityItem(ep=block_start, dimension="four_lines", passed=False, severity="error",
                                         evidence=f"第{block_start}-{block_start + 9}集无任何线推进",
                                         suggestion="在该 10 集块内标注至少一条推进的线（主线/支线/明线/暗线）"))

    # 冲突层：每 10 集至少一个升级型爆点（强冲突/威胁升级/关系破裂/危机升级）
    upgrade_types = {"强冲突", "威胁升级", "关系破裂", "危机升级", "危机"}
    if outline.beats:
        for block_start in range(1, 81, 10):
            block = [b for b in outline.beats if block_start <= b.ep < block_start + 10]
            if block and not any(b.explosion_type in upgrade_types for b in block):
                items.append(QualityItem(ep=block_start, dimension="conflict_upgrade", passed=False, severity="error",
                                         evidence=f"第{block_start}-{block_start + 9}集无升级型爆点",
                                         suggestion="冲突需升级：每 10 集至少一个 强冲突/威胁升级/关系破裂/危机升级 类爆点"))

    # 信息层：每 10 集至少一个信息型爆点（反转/身份揭露/信息炸弹），软检
    info_types = {"反转", "身份揭露", "信息炸弹"}
    if outline.beats:
        for block_start in range(1, 81, 10):
            block = [b for b in outline.beats if block_start <= b.ep < block_start + 10]
            if block and not any(b.explosion_type in info_types for b in block):
                items.append(QualityItem(ep=block_start, dimension="info_reveal", passed=False, severity="suggestion",
                                         evidence=f"第{block_start}-{block_start + 9}集无信息型爆点",
                                         suggestion="信息投放：每 10 集可安排一个 反转/身份揭露/信息炸弹 类爆点（非强制）"))
    return items


def _to_events(episodes: list[EpisodeScript]) -> tuple[list[Event], list[QualityItem]]:
    """分集结构化事件 → engine Event（类型非法/字段缺失 → 记 error 项并跳过）。"""
    events: list[Event] = []
    items: list[QualityItem] = []
    for ep in episodes:
        for i, ev in enumerate(ep.events):
            etype = str(ev.get("type", ""))
            actor = str(ev.get("actor", ""))
            if etype not in KNOWN_EVENT_TYPES:
                items.append(QualityItem(ep=ep.ep, dimension="event_type", passed=False, severity="error",
                                         evidence=f"第{ep.ep}集事件类型非法：{etype}",
                                         suggestion=f"事件类型限：{sorted(KNOWN_EVENT_TYPES)}"))
                continue
            if not actor:
                items.append(QualityItem(ep=ep.ep, dimension="event_type", passed=False, severity="error",
                                         evidence=f"第{ep.ep}集事件缺 actor", suggestion="补 actor"))
                continue
            try:
                events.append(Event(
                    event_id=f"e{ep.ep}_{i}",
                    chapter=f"第{ep.ep}集",
                    seq=i,
                    type=EventType(etype),
                    actor=actor,
                    target=str(ev["target"]) if ev.get("target") else None,
                    detail=str(ev.get("detail", "")),
                    citation=str(ev.get("citation") or f"第{ep.ep}集"),
                    time=TimeRef(anchor=str(ev.get("anchor") or "") or None),
                ))
            except Exception:  # noqa: BLE001
                items.append(QualityItem(ep=ep.ep, dimension="event_type", passed=False, severity="error",
                                         evidence=f"第{ep.ep}集事件解析失败：{ev}", suggestion="按事件契约修正"))
    return events, items


def check_logic(episodes: list[EpisodeScript]) -> list[QualityItem]:
    """复用现有确定性逻辑引擎：生死/倒地起身/昏迷/物品/时间轴等连续性。"""
    events, items = _to_events(episodes)
    if not events:
        return items
    report = DetectionEngine().process(events)
    for v in report.violations:
        severity = "fatal" if v.severity == Severity.ERROR else ("error" if v.severity == Severity.STRONG else "suggestion")
        items.append(QualityItem(
            ep=_ep_of(v.location),
            dimension="logic",
            passed=False,
            severity=severity,
            evidence=f"[{v.rule_id}] {v.message} @ {v.location or v.evidence[:80]}",
            suggestion=v.suggestion,
        ))
    return items


FIGHT_KEYWORDS = (
    "打", "斗", "杀", "撞", "踢", "砍", "劈", "夺", "抓", "追", "逃", "袭", "挡", "摔",
    "压", "击", "闪", "扑", "对打", "交手", "缠斗", "格挡", "拳", "掌", "刀", "剑", "枪", "刺", "攻",
)


def check_scene_focus(episodes: list[EpisodeScript], threshold: float | None = None) -> list[QualityItem]:
    """单场景推进内容占比（分镜时长分配）：(打戏+对话) / 全部场景内容 >= 阈值。

    打戏与对话都是直接推进剧情的内容（作用一致，合并计为"推进内容"）；
    本检查防止"铺垫性镜头"（非打斗的动作/环境/气氛描写）占比过大，
    符合短剧快节奏、避免观众注意力流失。场景总时长由 storyboard 计算。
    """
    rubric = load_json("quality_rubric.json")
    th = threshold if threshold is not None else float(rubric.get("scene_focus", {}).get("threshold", 0.7))
    selector = ShotSelector()
    items: list[QualityItem] = []
    for ep in episodes:
        for i, scene in enumerate(ep.scenes, 1):
            fight = sum(len(a) for a in scene.action_blocks if any(k in a for k in FIGHT_KEYWORDS))
            all_action = sum(len(a) for a in scene.action_blocks)
            dlg = sum(len(d.line) + len(d.speaker) for d in scene.dialogues)
            total = all_action + dlg
            if total == 0:
                continue
            advance = fight + dlg          # 推进内容 = 打戏 + 对话
            advance_share = advance / total
            if advance_share >= th:
                continue
            padding_share = 1 - advance_share
            total_sec, scene_type = 0.0, ""
            try:
                summary = " ".join(
                    [scene.time, scene.location, *scene.action_blocks]
                    + [f"{d.speaker}：{d.line}" for d in scene.dialogues[:4]]
                )
                plan = selector.select(SceneInput(
                    scene_id=f"e{ep.ep}_s{i}", scene_type="",
                    participants=[d.speaker for d in scene.dialogues],
                    location=scene.location, summary=summary,
                ))
                total_sec = sum(s.duration_sec for s in plan.shots)
                scene_type = plan.scene_type
            except Exception:  # noqa: BLE001, S110
                pass
            items.append(QualityItem(
                ep=ep.ep, dimension="scene_focus", passed=False, severity="error",
                evidence=f"第{ep.ep}集场景{i}（{scene_type or '?'}，约{total_sec:.0f}s）"
                         f"推进内容（打戏+对话）≈{total_sec * advance_share:.0f}s({advance_share:.0%})，"
                         f"铺垫性镜头≈{total_sec * padding_share:.0f}s({padding_share:.0%})",
                suggestion=f"单场景推进内容（打戏+对话）需 ≥ {th:.0%}，铺垫性镜头占比过大（短剧快节奏）",
            ))
    return items


def _ep_of(location: str) -> int | None:
    import re

    m = re.search(r"(\d+)", location or "")
    return int(m.group(1)) if m else None


def check_episode_duration(episodes: list[EpisodeScript], rubric: dict) -> list[QualityItem]:
    """时长预算（#3/B3）：每集总时长≈目标（默认 180s）；每场 30-90s。防止整集只有几十秒。"""
    items: list[QualityItem] = []
    cfg = rubric.get("episode_duration", {})
    target = float(cfg.get("seconds", 180))
    tol = float(cfg.get("tolerance", 0.25))
    sev = cfg.get("severity", "error")
    scfg = rubric.get("scene_duration", {})
    sc_min, sc_max = float(scfg.get("min_sec", 30)), float(scfg.get("max_sec", 90))
    selector = ShotSelector()
    for ep in episodes:
        total = 0.0
        for i, scene in enumerate(ep.scenes, 1):
            st = 0.0
            if getattr(scene, "beats", None):
                st = sum(float(b.duration_sec or 0) for b in scene.beats)
            if st <= 0:
                try:
                    summary = " ".join(
                        [scene.time, scene.location, *scene.action_blocks]
                        + [f"{d.speaker}：{d.line}" for d in scene.dialogues[:4]]
                    )
                    plan = selector.select(SceneInput(
                        scene_id=f"e{ep.ep}_s{i}", scene_type="",
                        participants=[d.speaker for d in scene.dialogues],
                        location=scene.location, summary=summary,
                    ))
                    st = sum(s.duration_sec for s in plan.shots)
                except Exception:  # noqa: BLE001
                    st = 0.0
            total += st
            if st and not (sc_min <= st <= sc_max):
                items.append(QualityItem(
                    ep=ep.ep, dimension="scene_duration", passed=False, severity=sev,
                    evidence=f"第{ep.ep}集场景{i}时长≈{st:.0f}s",
                    suggestion=f"单场 {sc_min:.0f}-{sc_max:.0f}s（当前偏{'短' if st < sc_min else '长'}）",
                ))
        if total and abs(total - target) > target * tol:
            items.append(QualityItem(
                ep=ep.ep, dimension="episode_duration", passed=False, severity=sev,
                evidence=f"第{ep.ep}集总时长≈{total:.0f}s（目标 {target:.0f}s±{tol:.0%}）",
                suggestion="按时长预算：4-6 场 × 30-90s，单镜 5-15s",
            ))
    return items


def check_emotion_vectors(episodes: list[EpisodeScript], vectors: dict | None = None,
                         patterns: list[dict] | None = None,
                         severity: str = "fatal") -> list[QualityItem]:
    """情绪向量前置检查（#4）：高风险行为需匹配向量前置；无向量状态时不判。"""
    if not vectors:
        return []
    from app.library.vector_consistency import VectorLedger

    ledger = VectorLedger(vectors=vectors, patterns=patterns)
    items: list[QualityItem] = []
    for ep in episodes:
        for ev in ep.events:
            etype = str(ev.get("type", ""))
            actor = str(ev.get("actor", ""))
            target = ev.get("target")
            ledger.apply_event(etype, actor, target)
            for miss in ledger.check_behavior(etype, actor, target):
                items.append(QualityItem(
                    ep=ep.ep, dimension="emotion_vector", passed=False, severity=severity,
                    evidence=f"第{ep.ep}集 {miss}",
                    suggestion="补充提升向量的前置事件，或改写该行为",
                ))
    return items


_PACING_SUGGESTIONS = {
    "C1": "贴合期望情绪弧线 e*(t)：按大纲情绪目标收束本集节奏",
    "C2": "单边游程超限：连续同向情绪过长，插入反向情绪拍打断",
    "C3": "反转频率越界：情绪极性翻转过多或过少，调整节奏密度",
    "C4": "苦中作乐：谷值后缺少回升，补充转机/希望拍",
    "C5": "居安思危：峰值后缺少回落钩子，补一个下坠/威胁提示",
    "C6": "峰谷落差越界：放大或压缩单场情绪落差",
    "C7": "螺旋包络：峰/谷序列未整体上行，避免原地打转",
}


def check_emotion_pacing(episodes: list[EpisodeScript], expected_arc=None,
                         params: dict | None = None,
                         severity: str = "warning") -> list[QualityItem]:
    """情绪节奏门禁 C1-C7（观众状态数学建模 §4，后置质检）。

    把每集文本（scenes.action_blocks + dialogues.line + vo）拼成一段，
    pacing_from_text → 对未通过约束产出 QualityItem；
    文本空 / 词典缺失 / 弧线过短 → 不产出（返回 []）。
    金标校准前默认 severity=warning（软提示，不阻断产线）。
    """
    items: list[QualityItem] = []
    for ep in episodes:
        parts: list[str] = []
        for scene in ep.scenes:
            parts.extend(a for a in scene.action_blocks if a and a.strip())
            for d in scene.dialogues:
                if d.line and d.line.strip():
                    parts.append(d.line.strip())
                vo = getattr(d, "vo", "") or getattr(scene, "vo", "")
                if isinstance(vo, str) and vo.strip():
                    parts.append(vo.strip())
        text = "\n".join(parts).strip()
        if not text:
            continue
        for c in pacing_from_text(text, expected_arc, params):
            if c.get("passed", True):
                continue
            cid = str(c.get("id", "?"))
            items.append(QualityItem(
                ep=ep.ep,
                dimension="emotion_pacing",
                passed=False,
                severity=severity,
                evidence=f"第{ep.ep}集 {cid} 未通过：{c.get('evidence', '')}",
                suggestion=_PACING_SUGGESTIONS.get(cid, "调整该集情绪节奏以满足门禁 C1-C7"),
            ))
    return items


async def run_quality(
    outline: Outline,
    episodes: list[EpisodeScript],
    script_id: str = "",
    client: object | None = None,
    llm_semantic: bool = False,
    vectors: dict | None = None,
    bible: object | None = None,
    emotion_pacing: bool = False,
) -> QualityReport:
    rubric = load_json("quality_rubric.json")
    items = check_structure(outline, episodes, rubric)
    items.extend(check_logic(episodes))
    items.extend(check_scene_focus(episodes))
    items.extend(check_episode_duration(episodes, rubric))
    items.extend(check_emotion_vectors(
        episodes, vectors, severity=rubric.get("emotion_vector", {}).get("severity", "fatal")))
    if llm_semantic and client is not None:
        items.extend(await _semantic_check(outline, episodes, client))
    items.extend(check_bible_constraints(episodes, bible))
    if emotion_pacing:
        items.extend(check_emotion_pacing(episodes))
    report = QualityReport(script_id=script_id, items=items)
    return report


async def _semantic_check(outline: Outline, episodes: list[EpisodeScript], client: object) -> list[QualityItem]:
    """反转可回溯性等语义检查（LLM 候选，本地仅收集为建议；Phase 0 兜底：按大纲反转类型抽查）。"""
    items: list[QualityItem] = []
    template = load_prompt("quality")
    for b in outline.beats:
        if b.explosion_type != "反转":
            continue
        script = next((e for e in episodes if e.ep == b.ep), None)
        if not script:
            continue
        user = build_user("quality", template, EP=str(b.ep), TITLE=b.title, BEAT=b.explosion, SCRIPT=script.model_dump_json())
        try:
            data = await client.chat_json(  # type: ignore[attr-defined]
                "你是短剧质检评审。判断该集反转是否有前文铺垫且不违背前文事实。只输出 JSON。",
                user,
                max_tokens=settings.llm_max_tokens_quality,
            )
            ok = bool(data.get("traceable", True))
            if not ok:
                items.append(QualityItem(ep=b.ep, dimension="reversal", passed=False, severity="error",
                                         evidence=str(data.get("reason", "反转缺少铺垫")),
                                         suggestion="补充反转铺垫或调整反转"))
        except Exception:  # noqa: BLE001
            items.append(QualityItem(ep=b.ep, dimension="reversal", passed=False, severity="suggestion",
                                     evidence="语义检查失败（LLM 异常）", suggestion="人工复核该集反转"))
    return items