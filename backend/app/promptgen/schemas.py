"""提示词生成器数据契约：四类提示词的输入/输出结构。"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ---------- 输入：已解析的剧本 ----------

class ParsedCharacter(BaseModel):
    """从剧本提取的角色（人物形象提示词输入）。"""
    name: str
    role: str = ""                # 主角/反派/配角
    gender: str = ""
    age: str = ""
    appearance: str = ""          # 外貌（发型/脸型/体型）
    outfit: str = ""              # 服装
    temperament: str = ""         # 气质/性格
    source: str = ""              # 原文依据（忠实原文原则）


class ParsedScene(BaseModel):
    """从剧本提取的场景（场景全景提示词输入）。"""
    scene_id: str = ""
    location: str = ""
    interior: str = ""            # 内/外
    era: str = ""                 # 时代
    time: str = ""                # 日/夜/晨/昏
    lighting: str = ""            # 光线基准
    atmosphere: str = ""          # 氛围
    participants: list[str] = Field(default_factory=list)
    action_blocks: list[str] = Field(default_factory=list)  # 原文动作/情境描写（对齐 production.schemas.SceneScript.action_blocks，画面用）
    props: list[str] = Field(default_factory=list)  # 关联物品/道具，LLM 提取，2026-08-21 反应镜配套
    character_states: list[dict] = Field(default_factory=list)  # [{character, state}]，忠实原文，2026-08-21 问题3
    spatial: list[dict] = Field(default_factory=list)  # [{character, position, facing}]，忠实原文，2026-08-21 批次B
    lighting_arc: list[dict] = Field(default_factory=list)  # [{at, change}]，只收原文明确光线变化，2026-08-21 批次B
    dialogues: list[dict] = Field(default_factory=list)  # [{speaker, line}] 画面内对白（参与站位/对白行）
    vo: str = ""                  # 旁白/画外音原文（不进站位/对白，进【旁白】行，时长按 vo_seconds 折算）
    sound_effects: str = ""       # 环境音/音效原文（本地从【音效】标记区提取；【声音】行只放必要环境音，2026-08-20）
    negative: list[str] = Field(default_factory=list)  # LLM 按题材+场景类型+内容给的本场景负面词，2026-08-21 攒批
    emotion: str = ""            # LLM 给的本场景情绪词（已知情绪表内），2026-08-21 攒批
    segments: list[dict] = Field(default_factory=list)  # LLM 按叙事切段，每段 {emotion, sound, beats:[...]}，2026-08-21 parse 重构批次1
    beats: list[dict] = Field(default_factory=list)  # 有序事件流（#A 2026-08-20）：动作/对白/旁白严格按原文出现顺序交错，[{"type":"action|dialogue|vo",...}]；供画面时序对齐


class ParsedEpisode(BaseModel):
    """一集的结构化内容（剧情提示词 + 故事板输入）。"""
    ep: int
    title: str = ""
    raw_text: str = ""
    explosion: str = ""           # 本集爆点
    hook_open: str = ""           # 开场钩
    hook_end: str = ""            # 结尾钩
    emotional_curve: list[str] = Field(default_factory=list)  # 起→升→钩
    scenes: list[ParsedScene] = Field(default_factory=list)


class ParsedScript(BaseModel):
    """整部剧本的解析结果。"""
    title: str = ""
    genre: str = ""
    episodes: list[ParsedEpisode] = Field(default_factory=list)
    characters: list[ParsedCharacter] = Field(default_factory=list)


# ---------- 输出：四类提示词 ----------

class PlotPrompt(BaseModel):
    """剧情提示词（每集）。"""
    ep: int
    title: str = ""
    explosion: str = ""
    hook_open: str = ""
    hook_end: str = ""
    emotional_curve: list[str] = Field(default_factory=list)
    prompt_zh: str = ""           # 组装后的剧情提示词（中文）
    prompt_en: str = ""           # 英文版


class CharacterPrompt(BaseModel):
    """人物形象提示词（每角色，文生图正/负向词）。"""
    character: str
    role: str = ""
    positive: str = ""            # 正向词（形象细节）
    negative: str = ""            # 负向词（防变形/瑕疵）
    audit_warnings: list[str] = Field(default_factory=list)  # sd_manual 合规告警


class ScenePrompt(BaseModel):
    """场景全景图提示词（每场景，正/负向词）。"""
    scene_id: str = ""
    location: str = ""
    positive: str = ""
    negative: str = ""
    audit_warnings: list[str] = Field(default_factory=list)


class PromptSet(BaseModel):
    """一个作品的四类提示词全集。"""
    title: str = ""
    genre: str = ""
    plot: list[PlotPrompt] = Field(default_factory=list)
    characters: list[CharacterPrompt] = Field(default_factory=list)
    scenes: list[ScenePrompt] = Field(default_factory=list)
    video: list[dict] = Field(default_factory=list)   # 视频提示词（逐镜中文，原 storyboard）
