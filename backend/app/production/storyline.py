"""故事线生成：梗概 → 故事线（主题/前提/主线支线明线暗线/人物弧光/情绪顶点）。"""
from __future__ import annotations

from app.config import settings
from app.production.knowledge_inject import knowledge_constraints
from app.production.llm_client import DeepSeekClient
from app.production.prompts import get_text, load_json, load_prompt
from app.production.schemas import Line, StoryBrief, StoryLine
from app.production.skill_inject import skill_constraints
from app.prompt_factory import build_user

SYSTEM = (
    "你是资深横屏 16:9 短剧总编剧。你的任务：根据故事梗概与题材，产出可执行 80 集横屏 16:9 短剧的故事线。"
    "只输出 JSON，不要 Markdown、不要注释、不要任何多余文字。"
)


def _lib_texts(genre: str) -> tuple[str, str, str]:
    chars = load_json("character_library.json")
    mats = load_json("material_library.json")
    themes = load_json("themes.json")
    char_t = get_text(chars, "archetypes")
    mat_t = get_text(mats, "devices") + "\n" + get_text(mats, "reversals")
    theme_t = get_text(themes, "pool")
    genre_note = get_text(themes, "genre_notes", "") if isinstance(themes.get("genre_notes"), str) else ""
    if genre and genre in (themes.get("genre_notes") or {}):
        genre_note = str(themes["genre_notes"][genre])
    return char_t, mat_t, theme_t + ("\n" + genre_note if genre_note else "")


async def generate_story_line(
    brief: StoryBrief,
    client: DeepSeekClient | None = None,
    genre: str | None = None,
    bible: object | None = None,
) -> StoryLine:
    client = client or DeepSeekClient()
    eff_genre = brief.genre if not genre else genre
    char_t, mat_t, theme_t = _lib_texts(eff_genre)
    try:
        from app.library.retriever import retrieve_text, search_patterns
        lib_txt = retrieve_text(genre=eff_genre, limit=10)
        if lib_txt:
            char_t = char_t + "\n### 素材库卡片（按需检索，生抽老抽）\n" + lib_txt
        formula_txt = search_patterns("formula", genre=eff_genre, limit=3)
        if formula_txt:
            theme_t = theme_t + "\n### 题材公式（跨书套路参考）\n" + formula_txt
    except Exception:  # noqa: BLE001, S110
        pass
    template = load_prompt("storyline")
    user = build_user("storyline", 
        template,
        GENRE=brief.genre,
        TITLE=brief.title or "（待定）",
        LOGLINE=brief.logline or "（未提供）",
        SYNOPSIS=brief.synopsis,
        CHARACTER_LIBRARY=char_t,
        MATERIAL_LIBRARY=mat_t,
        THEME_POOL=theme_t,
    )
    _method = knowledge_constraints("storyline")
    if _method:
        user += "\n\n## 编剧方法论约束（知识库注入）\n" + _method
    _skill = skill_constraints("storyline", genre=eff_genre)
    if _skill:
        user += "\n\n## 方法论技能约束（cangjie 蒸馏注入）\n" + _skill
    if bible is not None:
        from app.production.story_bible import bible_to_text

        _bt = bible_to_text(bible)
        if _bt:
            user += "\n\n## 世界圣经（硬约束，不可违背）\n" + _bt
    data = await client.chat_json(SYSTEM, user, max_tokens=settings.llm_max_tokens_storyline)
    story = StoryLine.model_validate(data)
    story.brief = brief
    _normalize(story)
    return story


def _normalize(story: StoryLine) -> None:
    """本地裁决兜底：保证四线结构完整、情绪顶点有序、主线存在。"""
    if not story.lines:
        story.lines = [
            Line(name="主线", kind="主线", start_ep=1, summary=story.premise or story.theme)
        ]
    kinds = {ln.kind for ln in story.lines}
    for kind in ("主线", "支线", "明线", "暗线"):
        if kind not in kinds:
            story.lines.append(Line(name=kind, kind=kind, start_ep=1, summary="（待分集大纲细化）"))
    story.emotional_peaks = sorted(story.emotional_peaks, key=lambda p: int(p.get("ep", 0)))
    if not story.characters:
        story.characters = []