"""80 集分集大纲生成：按 20 集一批分块生成，带前批摘要做连续性上下文。"""
from __future__ import annotations

from app.config import settings
from app.production.knowledge_inject import knowledge_constraints
from app.production.llm_client import DeepSeekClient
from app.production.prompts import load_prompt
from app.production.schemas import EpisodeBeat, Outline, StoryLine
from app.production.skill_inject import skill_constraints
from app.prompt_factory import build_user

SYSTEM = (
    "你是短剧分集大纲师。你的任务：把故事线扩展为 80 集横屏 16:9 短剧的分集大纲，"
    "每集必须预埋：开场 30 秒钩、结尾钩子、爆点（或与相邻集构成每两集一个强爆点）、情绪曲线（起→升→钩）、推进的线。"
    "只输出 JSON，不要 Markdown、不要注释。"
)

CHUNK = 20


def _storyline_text(story: StoryLine) -> str:
    lines_txt = "\n".join(
        f"- [{ln.kind}] {ln.name}（载体:{ln.carrier or '待定'}，起点:第{ln.start_ep}集，"
        f"埋点:{ln.seed_eps or '待定'}，收束:{ln.resolve_eps or '待定'}）{ln.summary}"
        for ln in story.lines
    )
    chars_txt = "\n".join(
        f"- {c.name}（{c.role}）目标:{c.goal}，缺陷:{c.flaw}，里程碑:{c.milestones or '待定'}，结局:{c.ending}"
        for c in story.characters
    )
    peaks = "；".join(f"第{p.get('ep')}集-{p.get('type')}" for p in story.emotional_peaks) or "待大纲预埋"
    return (
        f"题材：{story.brief.genre}\n主题：{story.theme}\n前提：{story.premise}\n"
        f"世界观约束：{'；'.join(story.world_rules) or '现实默认'}\n"
        f"四线：\n{lines_txt}\n人物弧光：\n{chars_txt}\n情绪大顶点：{peaks}\n"
        f"梗概：{story.brief.synopsis}"
    )


async def generate_outline(story: StoryLine, client: DeepSeekClient | None = None,
                       bible: object | None = None) -> Outline:
    client = client or DeepSeekClient()
    template = load_prompt("outline")
    beats: list[EpisodeBeat] = []
    for start in range(1, 81, CHUNK):
        end = min(start + CHUNK - 1, 80)
        prev_txt = _prev_context(beats)
        from app.production.story_bible import bible_design_constraints_text

        user = build_user("outline", 
            template,
            STORYLINE=_storyline_text(story),
            RANGE=f"{start}到{end}",
            PREV=prev_txt,
            DESIGN=bible_design_constraints_text(bible) if bible is not None else "（无）",
        )
        _method = knowledge_constraints("outline")
        if _method:
            user += "\n\n## 编剧方法论约束（知识库注入）\n" + _method
        _skill = skill_constraints("outline", genre=story.brief.genre)
        if _skill:
            user += "\n\n## 方法论技能约束（cangjie 蒸馏注入）\n" + _skill
        data = await client.chat_json(SYSTEM, user, max_tokens=settings.llm_max_tokens_outline)
        chunk_beats: list[EpisodeBeat] = []
        for b in data.get("beats", []):
            b["ep"] = int(start + len(chunk_beats))
            chunk_beats.append(EpisodeBeat.model_validate(b))
        beats.extend(chunk_beats)
    beats = _normalize(beats)
    return Outline(story_line=story, beats=beats)


def _prev_context(beats: list[EpisodeBeat]) -> str:
    if not beats:
        return "（本批为开头，无前情）"
    tail = beats[-3:]
    return "\n".join(
        f"第{b.ep}集《{b.title}》摘要：{b.explosion or b.hook_end}（结尾钩：{b.hook_end}）" for b in tail
    )


def _normalize(beats: list[EpisodeBeat]) -> list[EpisodeBeat]:
    """本地裁决兜底：补齐/截断到 80 集，保证 ep 连续。"""
    beats = beats[:80]
    for i, b in enumerate(beats):
        b.ep = i + 1
    while len(beats) < 80:
        n = len(beats) + 1
        beats.append(EpisodeBeat(ep=n, title=f"第{n}集", hook_end="", explosion=""))
    return beats