# -*- coding: utf-8 -*-
"""梗概逆推服务（独立业务线：梗概 + 必要信息 → 骨架 → 节拍 → 剧本 → 伏笔台账 → 质检）。

不依赖 works_store / promptgen（那是「剧本→提示词」业务线）；本模块是「梗概→剧本」线：
- 骨架：spine.build_spine（必要信息作为硬约束注入）
- 节拍：outline 模板单批（DESIGN=骨架原文守护）
- 剧本：episode 模板逐集（DESIGN=骨架原文守护 + 伏笔台账 ID 回引 + 容错清洗）
- 质检：production.quality.run_quality（本地规则）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings
from app.production import spine as spine_mod
from app.production.episode_writer import EVENT_TYPES, SYSTEM as EPISODE_SYSTEM, ScriptState
from app.production.llm_client import DeepSeekClient
from app.production.outline import SYSTEM as OUTLINE_SYSTEM, _storyline_text
from app.production.prompts import load_prompt
from app.production.schemas import (
    EpisodeBeat,
    EpisodeScript,
    SceneScript,
    StoryBrief,
    StoryLine,
)
from app.production.quality import run_quality
from app.production.spine_storyline import storyline_from_spine
from app.production.knowledge_inject import knowledge_constraints
from app.production.skill_inject import skill_constraints
from app.prompt_factory import build_user
from app.story.foreshadow_ledger import ForeshadowLedger

MAX_FORESHADOWS_IN_PROMPT = 12
MAX_STATE_THREADS = 8
EPISODE_MAX_TOKENS = 24000
OUTLINE_MAX_TOKENS = 24000
SPINE_MAX_TOKENS = 40000

# 必要信息 → build_spine 硬约束
BRIEF_LABELS = [
    ("logline", "一句话卖点"), ("era", "时代/年代"), ("background", "背景设定"),
    ("world_view", "世界观"), ("rules", "法则/规则"), ("style", "总体风格"),
    ("theme", "立意/主题"), ("conflict", "核心冲突"), ("protagonist", "主角欲望与阻力"),
    ("ending", "结局方向"), ("taboos", "不可改设定/禁忌"),
]

_REFS_RULE = (
    "\n## 伏笔回引规则（必须遵守）\n"
    "- 上文的「未回收伏笔」每条带 ID（如 [fs001]）。\n"
    "- state_update.open_threads：沿用注入伏笔的 ID 与原文，不得改写措辞；本集新埋的伏笔可新增（不带注入 ID）。\n"
    "- state_update.resolved：本集推进/兑现的伏笔，必须回引注入 ID（写 [fs001]）或原句首部；纯新埋线索不写进 resolved。\n"
    "- 新增剧情/新增伏笔一律不得改写上方「本作固定设定」里的原文（台词/遗言/事件/关系/钩子）；与固定设定冲突时，以固定设定原文为准。"
)


# ---------------------------------------------------------------- 骨架原文守护（DESIGN 注入）
def _skeleton_constraints(plan, ep_from: int | None = None, ep_to: int | None = None) -> str:
    """骨架原文守护。ep_from/ep_to 给出当前生成集窗口时，只注入相关节点（含前后 5 集缓冲），
    避免全 15 节点原文灌入每集导致 prompt 过长/推理缓慢。"""
    if ep_from is not None and ep_to is not None:
        window = [n for n in plan.nodes
                  if n.ep_end() >= ep_from - 5 and n.ep_start() <= ep_to + 5]
        nodes = window or plan.nodes
    else:
        nodes = plan.nodes
    L: list[str] = ["## 本作固定设定（骨架原文，不可违背，优先级最高；新增剧情不得改写以下任何原文）",
                    "### 大事件骨架（节点 -> 集数，仅相关节点）"]
    for n in sorted(nodes, key=lambda x: (x.ep_start(), x.node_id)):
        rng = f"ep{n.ep_start()}-{n.ep_end()}" if n.ep_start() != n.ep_end() else f"ep{n.ep_start()}"
        L.append(f"[{n.node_id}] {n.type}「{n.title}」（{rng}）\n  事件：{n.event}")
        if n.hook:
            L.append(f"  钩子：{n.hook}")
    if plan.casting:
        L.append("### 人物池")
        for ch in plan.casting:
            L.append(f"- {ch.name}（{ch.role}）：欲望={ch.desire}；阻力={ch.obstacle}；弧光={ch.arc}")
    if plan.open_questions:
        L.append("### 开放问题（骨架未定事实，禁止自行坐实）")
        for q in plan.open_questions:
            L.append(f"- {q}")
    return "\n".join(L)


# ---------------------------------------------------------------- 必要信息 → 约束/StoryLine 增强
def _brief_constraints(brief: dict) -> list[str]:
    out = []
    for k, label in BRIEF_LABELS:
        v = str(brief.get(k) or "").strip()
        if v:
            out.append(f"{label}：{v}")
    return out


def _enrich_story(story: StoryLine, brief: dict, synopsis: str, title: str, genre: str) -> StoryLine:
    if not story.brief or not story.brief.title:
        story.brief = StoryBrief(title=title, genre=genre, synopsis=synopsis,
                                 logline=str(brief.get("logline") or ""))
    story.theme = str(brief.get("theme") or story.theme or "")
    story.premise = str(brief.get("logline") or story.premise or "")
    rules: list[str] = []
    for key in ("world_view", "rules", "background", "taboos", "style"):
        v = str(brief.get(key) or "").strip()
        for line in [x.strip() for x in v.splitlines() if x.strip()]:
            rules.append(line)
    if rules:
        story.world_rules = list(dict.fromkeys(story.world_rules + rules))
    return story


# ---------------------------------------------------------------- 伏笔注入/回收
def _tagged(ledger: ForeshadowLedger) -> list[str]:
    return [f"[{it.id}] {it.text}" for it in ledger.open()[:MAX_FORESHADOWS_IN_PROMPT]]


def _strip_id(t: str) -> str:
    return re.sub(r"^\[?fs\d{3}\]?\s*", "", (t or "").strip())


def _payoff_by_ref(ledger: ForeshadowLedger, ep: int, resolved: list[str]) -> list[str]:
    paid: list[str] = []
    id_map = {it.id: it for it in ledger.items if it.status == "open"}
    for t in resolved:
        t = (t or "").strip()
        if not t:
            continue
        m = re.search(r"\[?(fs\d{3})\]?", t)
        if m and m.group(1) in id_map:
            it = id_map.pop(m.group(1))
            it.status = "payoff"
            it.payoff_ep = ep
            paid.append(it.id)
            continue
        for it in list(id_map.values()):
            if t == it.text or t in it.text or it.text in t:
                it.status = "payoff"
                it.payoff_ep = ep
                paid.append(it.id)
                id_map.pop(it.id, None)
                break
    return paid


def _sanitize_state_update(raw: dict) -> dict:
    su = raw.get("state_update") or {}
    ev = su.get("emotion_vectors") or {}
    if isinstance(ev, dict):
        cleaned = {}
        for pair, v in ev.items():
            if isinstance(v, dict):
                cleaned[pair] = {str(dim): int(x) for dim, x in v.items()
                                 if str(x).lstrip("-").isdigit()}
            elif isinstance(v, (int, str)) and str(v).lstrip("-").isdigit():
                cleaned[pair] = {"love": int(v)}
        su["emotion_vectors"] = cleaned
        raw["state_update"] = su
    return raw


# ---------------------------------------------------------------- 节拍生成（单批）
async def _generate_beats(plan, story: StoryLine, client: DeepSeekClient,
                          start: int, end: int, foreshadows: list[str]):
    template = load_prompt("outline")
    user = build_user("outline", template, _foreshadows=foreshadows,
                      STORYLINE=_storyline_text(story),
                      RANGE=f"{start}到{end}",
                      PREV="（本批为开头，无前情）",
                      DESIGN=_skeleton_constraints(plan, ep_from=start, ep_to=end))
    _method = knowledge_constraints("outline")
    if _method:
        user += "\n\n## 编剧方法论约束（知识库注入）\n" + _method
    _skill = skill_constraints("outline", genre=story.brief.genre)
    if _skill:
        user += "\n\n## 方法论技能约束（cangjie 蒸馏注入）\n" + _skill
    data = await client.chat_json(OUTLINE_SYSTEM, user, max_tokens=OUTLINE_MAX_TOKENS)
    beats: list[EpisodeBeat] = []
    for i, b in enumerate(data.get("beats", [])):
        b["ep"] = start + i
        beats.append(EpisodeBeat.model_validate(b))
    return beats, user, data


# ---------------------------------------------------------------- 单集剧本生成
async def _generate_episode_raw(plan, beat: EpisodeBeat, story: StoryLine, state: ScriptState,
                                client: DeepSeekClient, ep: int, foreshadows: list[str]):
    template = load_prompt("episode")
    beat_json = json.dumps(beat.model_dump(), ensure_ascii=False)
    lines_txt = "\n".join(f"- [{ln.kind}] {ln.name}：{ln.summary}" for ln in story.lines)
    user = build_user("episode", template, _foreshadows=foreshadows,
                      EP=str(ep), TITLE=beat.title or f"第{ep}集",
                      BEAT=beat_json, STORYLINES=lines_txt,
                      STATE=state.to_prompt(), EVENT_TYPES=EVENT_TYPES,
                      DESIGN=_skeleton_constraints(plan, ep_from=ep, ep_to=ep))
    _method = knowledge_constraints("episode")
    if _method:
        user += "\n\n## 编剧方法论约束（知识库注入）\n" + _method
    _skill = skill_constraints("episode", genre=story.brief.genre)
    if _skill:
        user += "\n\n## 方法论技能约束（cangjie 蒸馏注入）\n" + _skill
    if foreshadows:
        user += _REFS_RULE
    data = await client.chat_json(EPISODE_SYSTEM, user, max_tokens=EPISODE_MAX_TOKENS)
    data = _sanitize_state_update(data)
    for _sc in data.get("scenes") or []:
        for _b in _sc.get("beats") or []:
            for _k in ("subject", "action", "dialogue", "emotion", "focus", "motion",
                       "sound", "lighting", "blocking", "staging", "camera_pos",
                       "movement", "scale", "angle", "duration_sec"):
                if _b.get(_k) is None:
                    _b[_k] = 0.0 if _k == "duration_sec" else ""
        for _k in ("scene_id", "location", "time", "lighting", "blocking", "transition"):
            if _sc.get(_k) is None:
                _sc[_k] = ""
        for _d in _sc.get("dialogues") or []:
            for _k in ("speaker", "line", "emotion", "action"):
                if _d.get(_k) is None:
                    _d[_k] = ""
    script = EpisodeScript(
        ep=ep,
        title=str(data.get("title") or beat.title or f"第{ep}集"),
        hook_open=str(data.get("hook_open") or beat.hook_open or ""),
        hook_end=str(data.get("hook_end") or beat.hook_end or ""),
        explosion=str(data.get("explosion") or beat.explosion or ""),
        scenes=[SceneScript.model_validate(s) for s in data.get("scenes") or []],
        events=[e for e in (data.get("events") or []) if isinstance(e, dict)],
    )
    state.update(ep, data)
    state.open_threads = state.open_threads[:MAX_STATE_THREADS]
    return script, user, data


# ---------------------------------------------------------------- 主流程
async def run_inverse(
    *,
    title: str,
    genre: str,
    synopsis: str,
    brief: dict | None = None,
    eps_start: int = 1,
    eps_end: int = 8,
    client: DeepSeekClient | None = None,
    progress=None,  # Callable[[str], None]
) -> dict:
    brief = brief or {}
    client = client or DeepSeekClient()
    title = (title or "").strip() or "未命名·" + (synopsis or "")[:12].strip()
    if progress:
        progress("spine")

    constraints = _brief_constraints(brief)
    plan = await spine_mod.build_spine(
        synopsis, constraints, client, max_tokens=SPINE_MAX_TOKENS)
    spine_mod._run_closure(plan)
    spine_mod._check_spiral(plan)

    story = _enrich_story(
        storyline_from_spine(plan, StoryBrief(title=title, genre=genre, synopsis=synopsis)),
        brief, synopsis, title, genre)

    if progress:
        progress("outline")
    beats, outline_prompt, _outline_data = await _generate_beats(
        plan, story, client, eps_start, eps_end, _tagged(ForeshadowLedger()))

    ledger = ForeshadowLedger()
    state = ScriptState(story)
    # 初始角色状态：从骨架人物池 desire 引导（LLM 在首集自然落位）
    state.character_states = {}
    state.summaries = []

    episodes: list[EpisodeScript] = []
    episode_prompts: list[dict] = []
    state_updates: list[dict] = []
    for b in beats:
        ep = b.ep
        if progress:
            progress(f"episode_{ep}")
        script, user, raw = await _generate_episode_raw(
            plan, b, story, state, client, ep, _tagged(ledger))
        episodes.append(script)
        episode_prompts.append({"ep": ep, "user": user})
        su = raw.get("state_update") or {}
        resolved = list(su.get("resolved") or [])
        paid = _payoff_by_ref(ledger, ep, resolved)
        for t in list(su.get("open_threads") or []):
            ledger.add(ep, [_strip_id(t)])
        state_updates.append({
            "ep": ep, "open_threads": su.get("open_threads") or [],
            "resolved": resolved, "paid": paid, "summary": raw.get("summary") or "",
        })

    if progress:
        progress("quality")
    from app.production.schemas import Outline
    outline = Outline(story_line=story, beats=beats)
    report = await run_quality(outline, episodes, script_id=title or "inverse",
                               vectors=state.emotion_vectors)

    return {
        "title": title or plan.spine_title or "",
        "genre": genre,
        "eps_range": [eps_start, eps_end],
        "spine": {
            "spine_title": plan.spine_title or title,
            "nodes": [n.model_dump() for n in plan.nodes],
            "casting": [c.model_dump() for c in plan.casting],
            "open_questions": plan.open_questions,
            "spiral_issues": plan.spiral_issues,
        },
        "beats": [b.model_dump() for b in beats],
        "episodes": [e.model_dump() for e in episodes],
        "ledger": ledger.summary(),
        "state_updates": state_updates,
        "quality": report.summary(),
        "quality_items": [i.model_dump() for i in report.items],
        "prompts": {"outline": outline_prompt, "episode": episode_prompts},
        "model": client.model,
    }
