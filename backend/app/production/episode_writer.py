"""分集剧本生成：携带 状态快照 + 伏笔清单 + 前情摘要，产出 3 分钟分集剧本 + 结构化事件。

状态推进采用"模型提议"：LLM 返回 state_update（角色状态/未收伏笔），
本地质检门再对事件做确定性校验（复用 DetectionEngine），不合格回炉。
"""
from __future__ import annotations

import json

from app.config import settings
from app.production.knowledge_inject import knowledge_constraints
from app.production.llm_client import DeepSeekClient
from app.production.prompts import load_prompt
from app.production.schemas import EpisodeBeat, EpisodeScript, SceneScript, StoryLine
from app.production.skill_inject import skill_constraints
from app.prompt_factory import build_user

SYSTEM = (
    "你是横屏 16:9 短剧分集编剧。每集 3 分钟（约 700-900 字），横屏 16:9。"
    "严格按每集结构骨架落位：开场钩→世界观/前情交代→冲突推进→爆点→结尾钩；"
    "严格按大纲节拍输出：开场 30 秒钩、结尾钩子、爆点、情绪递进。"
    "只输出 JSON，不要 Markdown、不要注释、不要任何多余文字。"
)

EVENT_TYPES = (
    "死亡,复活,倒地,起身,被压制,挣脱,受伤,治疗,昏迷,苏醒,说话,打斗,行动,持械,拾取,时间推进"
)


class ScriptState:
    """分集生成的滚动状态：角色当前状态 + 未回收伏笔 + 前情摘要（窗口 6 集）。"""

    def __init__(self, story: StoryLine) -> None:
        self.story = story
        self.character_states: dict[str, str] = {}
        self.open_threads: list[str] = []
        self.summaries: list[tuple[int, str]] = []
        self.resolved: list[str] = []
        self.emotion_vectors: dict[tuple[str, str], dict[str, int]] = {}   # (A,B) -> {dim:int}

    def to_prompt(self) -> str:
        chars = "\n".join(f"- {k}：{v}" for k, v in self.character_states.items()) or "（初始，尚未出场）"
        threads = "\n".join(f"- {t}" for t in self.open_threads) or "（无）"
        prev = "\n".join(f"第{ep}集：{s}" for ep, s in self.summaries[-6:]) or "（本集为开局）"
        vecs = "\n".join(
            f"- {a}→{b}: {v}" for (a, b), v in self.emotion_vectors.items()
        ) or "（初始，未建立）"
        return (f"角色当前状态：\n{chars}\n未回收伏笔/悬念：\n{threads}\n"
                f"情绪向量：\n{vecs}\n前情摘要：\n{prev}")

    def update(self, ep: int, data: dict) -> None:
        su = data.get("state_update") or {}
        for k, v in (su.get("character_states") or {}).items():
            self.character_states[k] = str(v)
        self.open_threads = list(su.get("open_threads") or [])
        self.resolved.extend(su.get("resolved") or [])
        for pair, v in (su.get("emotion_vectors") or {}).items():
            a, _, b = str(pair).partition("→")
            self.emotion_vectors[(a.strip(), b.strip())] = {dim: int(x) for dim, x in (v or {}).items()}
        summary = str(data.get("summary") or data.get("hook_end") or "")
        if summary:
            self.summaries.append((ep, summary))
            self.summaries = self.summaries[-6:]


async def generate_episode(
    beat: EpisodeBeat,
    story: StoryLine,
    state: ScriptState,
    client: DeepSeekClient,
    ep_idx: int,
    bible: object | None = None,
    foreshadows: list[str] | None = None,
) -> EpisodeScript:
    template = load_prompt("episode")
    beat_json = json.dumps(beat.model_dump(), ensure_ascii=False)
    lines_txt = "\n".join(
        f"- [{ln.kind}] {ln.name}：{ln.summary}" for ln in story.lines
    )
    from app.production.story_bible import bible_design_constraints_text

    user = build_user("episode", 
        template, _foreshadows=foreshadows,
        EP=str(ep_idx),
        TITLE=beat.title or f"第{ep_idx}集",
        BEAT=beat_json,
        STORYLINES=lines_txt,
        STATE=state.to_prompt(),
        EVENT_TYPES=EVENT_TYPES,
        DESIGN=bible_design_constraints_text(bible) if bible is not None else "（无）",
    )
    _method = knowledge_constraints("episode")
    if _method:
        user += "\n\n## 编剧方法论约束（知识库注入）\n" + _method
    _skill = skill_constraints("episode", genre=story.brief.genre)
    if _skill:
        user += "\n\n## 方法论技能约束（cangjie 蒸馏注入）\n" + _skill
    data = await client.chat_json(SYSTEM, user, max_tokens=settings.llm_max_tokens_episode)
    script = EpisodeScript(
        ep=ep_idx,
        title=str(data.get("title") or beat.title or f"第{ep_idx}集"),
        hook_open=str(data.get("hook_open") or beat.hook_open or ""),
        hook_end=str(data.get("hook_end") or beat.hook_end or ""),
        explosion=str(data.get("explosion") or beat.explosion or ""),
        scenes=[SceneScript.model_validate(s) for s in data.get("scenes") or []],
        events=[e for e in (data.get("events") or []) if isinstance(e, dict)],
    )
    state.update(ep_idx, data)
    return script