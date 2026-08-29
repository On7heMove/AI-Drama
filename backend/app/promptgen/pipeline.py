"""四类提示词编排：一键生成剧情/人物形象/场景全景/故事板。"""
from __future__ import annotations

from app.production.llm_client import DeepSeekClient, ensure_llm_ready
from app.promptgen.character import build_character_prompts
from app.promptgen.parse import parse_script
from app.promptgen.plot import build_plot_prompts
from app.promptgen.scene import build_scene_prompts
from app.promptgen.schemas import ParsedScript, PromptSet
from app.promptgen.storyboard import build_storyboard_prompts  # 视频提示词生成器
from app.promptgen.vector_storyboard import build_vector_storyboard_prompts

TYPES = ("plot", "characters", "scenes", "video")


async def generate_prompt_set(
    text: str,
    *,
    title: str = "",
    genre: str = "",
    client: DeepSeekClient | None = None,
    storyboard_aspect: str = "9:16",
    vector_director: bool = False,  # 20维向量导演决策路径（对齐原则轴Schema）
    types: tuple[str, ...] = TYPES,
    max_episodes: int = 80,
) -> PromptSet:
    """端到端：剧本文本 → 指定类型提示词。LLM 只做一次解析提取；组装/合规全本地。

    types 取值：plot / characters / scenes / video（视频提示词，默认全部）。
    """
    bad = [t for t in types if t not in TYPES]
    if bad:
        raise ValueError(f"未知提示词类型: {bad}")
    # LLM 链路前置校验（2026-08-21）：没配 Key / Key 不可用 → 立即报错阻断，
    # 而不是等任务在 LLM 调用时才失败（前端可即时看到明确错误）。
    # 仅当调用方未显式传入 client（走默认 DeepSeekClient）时校验——显式传 client 说明
    # LLM 通道已由调用方负责（stub 测试/复用场景），不重复校验。
    if client is None:
        await ensure_llm_ready()
    script: ParsedScript = await parse_script(
        text, title=title, genre=genre, client=client, max_episodes=max_episodes
    )
    # 文本类型本地裁决器（#5，parse 后、生成前）：LLM 对 对白/VO/旁白 分类的确定性校验，error 阻断
    from app.storyboard.classification_gate import validate_classification
    _cls_issues = validate_classification(script)
    _cls_errors = [i for i in _cls_issues if i["severity"] == "error"]
    if _cls_errors:
        _detail = "；".join(f"{i['scene_id']} {i['evidence']}" for i in _cls_errors[:5])
        raise ValueError(f"文本类型分类校验未通过（{len(_cls_errors)} 处 error）：{_detail}")
    result = PromptSet(title=script.title or title, genre=script.genre or genre)
    if "plot" in types:
        result.plot = build_plot_prompts(script, en=True)
    if "characters" in types:
        result.characters = build_character_prompts(script)
    if "scenes" in types:
        result.scenes = build_scene_prompts(script)
    if "video" in types:
        if vector_director:
            result.video = await build_vector_storyboard_prompts(script, client=client, aspect=storyboard_aspect)
        else:
            result.video = build_storyboard_prompts(script, aspect=storyboard_aspect)
    return result
