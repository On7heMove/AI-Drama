"""分镜模块数据契约：场景输入 -> 镜头方案（ShotPlan）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SceneInput(BaseModel):
    scene_id: str = ""
    scene_type: str = ""          # 分类器输出或人工标注；留空则自动分类
    emotion: str = ""             # 情绪标签（愤怒/悲伤/喜悦/紧张…）
    info_goal: str = ""           # 信息目标（可选）
    participants: list[str] = Field(default_factory=list)
    location: str = ""
    summary: str = ""             # 场景描述/动作线（供分类与内容填充）
    duration_hint_sec: float | None = None  # 场景目标时长（可选，v0.1 未启用）


class Shot(BaseModel):
    index: int
    scale: str = ""               # 景别（中文）
    angle: str = ""               # 机位角度
    movement: str = ""            # 运动（中文）
    stability: str = ""           # 稳定性
    camera_pos: str = ""          # 机位（摄影机位置/朝向）
    lighting: str = ""            # 光线（物理光源/色温/暗光基础）
    blocking: str = ""            # 站位/轴线（人物相对位置 + 180°轴线侧）
    duration_sec: float
    content: str = ""             # 画面内容（主体+动作+环境）
    emotion: str = ""
    staging: str = ""             # 场面调度提示（形态/位置/轴线，v0.2 起）
    sound: str = ""               # 声音轨提示（人声/音效/音乐/环境，v0.5 起）
    tone: str = ""                # 色调提示（色相/明度/饱和度/冷暖，v0.6 起）
    edit: str = ""                # 剪辑/转场提示（剪切动机/转场类型，v0.7 起）
    motivation: str = ""          # 镜头动机（本镜向观众传达什么信息/情绪/衔接理由，v0.13 起）
    motivation_en: str = ""       # 镜头动机（英文版，v0.13 起）
    emotion_inferred: str = ""     # 情绪推理（后台数据，不进提示词，v0.13.2 起）
    note: str = ""


class ShotPlan(BaseModel):
    scene_id: str
    scene_type: str
    pattern_key: str = "default"  # 命中的方案名（default / 变体名）
    shots: list[Shot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
