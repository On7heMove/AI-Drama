"""产线二向量分镜生成器：剧本 → 每场景 20维向量导演决策 → 分镜提示词。

对齐 docs/20维向量接入产线二.md（分层架构）：
20维向量 = 场景级导演决策层（LLM 出向量 → 本地渲染参数 → 技能依据）
本地引擎 = 镜头级执行校验层（blocking/时长/合规/QA，见 storyboard_qa）

输出结构与现有 build_storyboard_prompts 一致（vids：image_prompts），可被 validate_video_segments 校验。
"""
from __future__ import annotations

from app.production.llm_client import DeepSeekClient
from app.promptgen.schemas import ParsedScene, ParsedScript
from app.storyboard.vector_pipeline import render_scene_prompt


def scene_context(s: ParsedScene) -> str:
    """ParsedScene → 场景上下文包（供 LLM 出向量）。"""
    parts = [f"场景：{s.location or ''} {s.time or ''} {s.emotion or ''}".strip()]
    if s.participants:
        parts.append(f"人物：{'、'.join(s.participants)}")
    if s.action_blocks:
        parts.append("动作：" + "；".join(a for a in s.action_blocks if a))
    for d in s.dialogues:
        spk = d.get("speaker", "")
        line = str(d.get("line", ""))
        if line:
            parts.append(f"对白（{spk}）：{line}")
    if s.vo:
        parts.append(f"旁白：{s.vo}")
    if s.sound_effects:
        parts.append(f"音效：{s.sound_effects}")
    # 2026-08-26 主体内容：beats（动作/对白/旁白 有序事件流）才是画面真正的主体；
    # 旧字段 action_blocks/dialogues 常为空（内容实际在 segments[].beats），补入上下文
    _content: list[str] = []
    for _b in s.beats:
        if not isinstance(_b, dict):
            continue
        _t = _b.get("type")
        if _t == "dialogue":
            _spk = str(_b.get("speaker") or "").strip()
            _line = str(_b.get("line") or "").strip()
            if _line:
                _content.append(f"{_spk}：{_line}" if _spk else _line)
        else:
            _txt = str(_b.get("text") or "").strip()
            if _txt:
                _content.append(_txt)
    if _content:
        parts.append("内容：" + "；".join(_content)[:600])
    return "\n".join(p for p in parts if p)


async def build_vector_storyboard_prompts(
    script: ParsedScript, *, client: DeepSeekClient | None = None, aspect: str = "9:16"
) -> list[dict]:
    """剧本 → 每场景 20维向量分镜提示词（vids 结构，与本地引擎输出同构）。"""
    client = client or DeepSeekClient()
    vids: list[dict] = []
    for episode in script.episodes:
        ep = episode.ep or 1
        for scene in episode.scenes:
            ctx = scene_context(scene)
            if not ctx:
                continue
            r = await render_scene_prompt(ctx, client=client, scene_id=scene.scene_id or f"scene{ep}")
            vids.append({
                "ep": ep,
                "scene_id": scene.scene_id or f"scene{ep}",
                "scene_type": scene.emotion or "dialogue",
                "image_prompts": [r["prompt"]],
                "english_prompts": [],
                "vector": {"branch": r["render"]["branch"], "skills": r["skills"]["hit_skills"],
                           "confidence": r["profile"]["confidence"]},
            })
    return vids
