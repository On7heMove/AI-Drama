"""生产线数据契约：梗概 → 故事线 → 大纲 → 分集剧本 → 质检 → 分镜 → 存档。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class CopyrightStatus(str, Enum):
    ORIGINAL = "original"            # 原创
    LICENSED = "licensed"            # 已授权
    PUBLIC_DOMAIN = "public_domain"  # 公版
    UNKNOWN = "unknown"              # 未登记（A3 决策：以量为先，版权不作为阻塞项，仍留字段追溯）


class StoryBrief(BaseModel):
    """输入层：一篇剧本的选题与梗概。"""

    script_id: str = ""
    title: str = ""
    genre: str = "现代"              # 题材（A4 决策：不限，字段必填便于排产/标签）
    logline: str = ""                # 一句话卖点
    synopsis: str = ""               # 梗概（人工输入 / 素材库 / 爬虫提炼）
    source: str = "manual"           # manual / crawler / library
    copyright_status: CopyrightStatus = CopyrightStatus.UNKNOWN
    notes: str = ""


class Line(BaseModel):
    """一条线：主线/支线/明线/暗线，含埋点与收束集数。"""

    name: str
    kind: str                        # 主线 / 支线 / 明线 / 暗线
    carrier: str = ""                # 载体角色
    start_ep: int = 1
    seed_eps: list[int] = Field(default_factory=list)     # 埋点集数
    resolve_eps: list[int] = Field(default_factory=list)  # 收束集数
    summary: str = ""


class TerminalState(BaseModel):
    """终态声明（一等约束，§14）：弧光终点 + 校验锚点。"""

    character: str = ""
    source: str = "derived"            # user / derived
    type: str = "dual"                 # mission / growth / dual
    goal_state: dict = Field(default_factory=dict)      # 外部目标达成态
    flaw_closure: dict = Field(default_factory=dict)    # {flaw, from, to, theta}
    arc_endpoint: dict = Field(default_factory=dict)    # {shape, end_val_min}
    theme_response: str = ""           # 主题回应
    milestones: list[dict] = Field(default_factory=list)  # [{ep, m}]


class CharacterArc(BaseModel):
    """人物弧光：目标/缺陷/里程碑/结局。"""

    name: str
    role: str = ""                   # 主角 / 反派 / 配角
    goal: str = ""
    flaw: str = ""
    milestones: list[dict] = Field(default_factory=list)   # [{ep, milestone}]
    ending: str | TerminalState = ""   # 简写 str 或结构化 TerminalState


class StoryLine(BaseModel):
    """故事线：主题 + 前提 + 四线 + 人物弧光 + 情绪顶点。"""

    brief: StoryBrief = Field(default_factory=StoryBrief)
    theme: str = ""
    premise: str = ""                # 高概念/前提
    world_rules: list[str] = Field(default_factory=list)   # 世界观约束
    lines: list[Line] = Field(default_factory=list)
    characters: list[CharacterArc] = Field(default_factory=list)
    emotional_peaks: list[dict] = Field(default_factory=list)  # [{ep, type}]


class SceneBeat(BaseModel):
    """大纲里的一场：地点/人物/事件/叙事目的。"""

    scene_id: str = ""
    location: str = ""
    time: str = ""
    participants: list[str] = Field(default_factory=list)
    summary: str = ""
    purpose: str = ""


class EpisodeBeat(BaseModel):
    """分集节拍：爆点/钩子/情绪曲线/推进的线（"每集一个爆点"在此显式预埋）。"""

    ep: int
    title: str = ""
    hook_open: str = ""              # 开场 30 秒钩（硬检）
    hook_end: str = ""               # 结尾钩子（硬检）
    explosion: str = ""              # 本集爆点内容
    explosion_type: str = ""         # 反转/强冲突/情绪顶点/信息炸弹/身份揭露/威胁升级/关系破裂
    scenes: list[SceneBeat] = Field(default_factory=list)
    emotional_curve: list[str] = Field(default_factory=list)   # 3 拍：起→升→钩
    lines_advanced: list[str] = Field(default_factory=list)    # 本集推进的线名


class Outline(BaseModel):
    """80 集分集大纲。"""

    story_line: StoryLine = Field(default_factory=StoryLine)
    beats: list[EpisodeBeat] = Field(default_factory=list)

    def ep(self, n: int) -> EpisodeBeat | None:
        return next((b for b in self.beats if b.ep == n), None)


class DialogueLine(BaseModel):
    speaker: str = ""
    line: str = ""
    emotion: str = ""
    action: str = ""                 # 说话时的动作/状态


class SceneScript(BaseModel):
    scene_id: str = ""
    location: str = ""
    time: str = ""
    lighting: str = ""               # 场景光线基准（主光源方向/色温/暗光基础，全场景镜头继承）
    blocking: str = ""               # 站位/轴线基准（人物相对位置 + 180°轴线侧，全场景镜头继承）
    participants: list[str] = Field(default_factory=list)     # 场景出场人物（站位/轴线推理用）
    action_blocks: list[str] = Field(default_factory=list)   # 动作/情境描写
    dialogues: list[DialogueLine] = Field(default_factory=list)
    transition: str = ""             # 转场（切黑/闪回/硬切…）
    beats: list[ShotBeat] = Field(default_factory=list)      # 分镜内容节拍（#2）


class ShotBeat(BaseModel):
    """分镜内容节拍：一镜的主体/动作/对白/情绪/目标时长（解决逐镜重复）。"""

    subject: str = ""
    action: str = ""
    dialogue: str = ""
    emotion: str = ""
    duration_sec: float | None = None   # None=未显式指定（由 PacingEngine/兜底定时长）
    scale: str = ""       # 分镜设计：景别（留空=自动 select_beat）
    angle: str = ""       # 分镜设计：机位角度（留空=自动）
    movement: str = ""    # 分镜设计：运动（留空=自动）
    camera_pos: str = ""  # 分镜设计：机位（摄影机位置/朝向，如 门缝低机位/侧面过肩；留空=自动）
    staging: str = ""     # 分镜设计：调度/张力（留空=书知识自动）
    sound: str = ""       # 分镜设计：声音（与画面分离，纯声音设计；留空=书知识自动）
    lighting: str = ""    # 分镜设计：本镜光线（留空=继承场景光线基准 scene.lighting）
    blocking: str = ""    # 分镜设计：本镜站位/轴线（留空=继承场景 blocking）
    angle_idx: int | None = None  # 引擎驱动：动作块内角度组序号（shot_selector 按此取角度组第几镜；None=非动作块，不走角度组）
    dialogue_turn_idx: int = 0  # 引擎驱动：同一说话人第几次出现（shot_selector 按此轮换对话机位，正反打差异化）
    focus: str = ""  # 表现重点（主体动作/关系过程/细节/反应情绪/环境）；空=走既有 beat_angle_groups/dialogue_turn_cycle
    motion: str = ""  # LLM 运动倾向（跟拍/急推/缓推/固定/横移/手持/穿越机等）；空=本地裁决


class EpisodeScript(BaseModel):
    """一集成品剧本（3 分钟）+ 结构化事件（喂给逻辑质检门）。"""

    ep: int
    title: str = ""
    hook_open: str = ""
    hook_end: str = ""
    explosion: str = ""
    scenes: list[SceneScript] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)   # 结构化事件：{type,actor,target?,detail?,citation?}


class TranslatedFields(BaseModel):
    """LLM 英文分镜翻译契约（P1-10）：字段缺失用默认值，类型错误进入本地回退。

    build_en_video_prompt* 通过 fe.get(key) 读取；经本模型校验后所有键必为 str。
    """
    scene: str = ""
    staging: str = ""
    sound: str = ""
    tone: str = ""
    edit: str = ""
    camera: str = ""
    lighting: str = ""
    blocking: str = ""
    dialogue: str = ""


class ShotPrompt(BaseModel):
    """一镜的分镜提示词（复用 storyboard 模块渲染）+ 图片生成提示词列表（标准动作）。"""

    ep: int
    scene_id: str = ""
    scene_type: str = ""
    plan_text: str = ""
    image_prompts: list[str] = Field(default_factory=list)   # 每镜一条分镜提示词（中文版）
    english_prompts: list[str] = Field(default_factory=list)  # 每镜英文版提示词（双语两套，2026-08-13）


class QualityItem(BaseModel):
    ep: int | None = None
    dimension: str = ""              # cliffhanger / explosion / emotion / logic / reversal / four_lines / event_type
    passed: bool = True
    severity: str = "suggestion"     # fatal / error / suggestion
    evidence: str = ""
    suggestion: str = ""


class QualityReport(BaseModel):
    script_id: str = ""
    items: list[QualityItem] = Field(default_factory=list)
    retry_rounds: int = 0

    def summary(self) -> dict:
        # items 仅记录失败项：不把失败项数量冒充"全部检查项"，pass_rate 不再误导。
        fatal = [i for i in self.items if i.severity == "fatal"]
        error = [i for i in self.items if i.severity == "error"]
        suggestion = [i for i in self.items if i.severity == "suggestion"]
        gate_passed = not (fatal or error)
        return {
            "issue_count": len(self.items),
            "fatal": len(fatal),
            "error": len(error),
            "suggestion": len(suggestion),
            "gate_passed": gate_passed,
            "pass_rate": 1.0 if gate_passed else 0.0,  # 门禁语义：无 fatal/error 视为通过
            "retry_rounds": self.retry_rounds,
        }

    def fatal_for_ep(self, ep: int | None) -> list[QualityItem]:
        return [i for i in self.items if i.severity == "fatal" and i.ep == ep]


class TokenStats(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    est_cost_yuan: float = 0.0


class Acceptance(BaseModel):
    """验收（A2 决策）：每篇交付故事线梗概，由编剧抽检该篇 3-4 集。"""

    status: str = "pending"          # pending / spot_checked / passed / rejected
    reviewer: str = ""
    checked_eps: list[int] = Field(default_factory=list)
    notes: str = ""


class ArchiveManifest(BaseModel):
    script_id: str
    title: str = ""
    genre: str = ""
    created_at: str = ""
    model: str = ""
    tokens: TokenStats = Field(default_factory=TokenStats)
    quality: dict = Field(default_factory=dict)
    acceptance: Acceptance = Field(default_factory=Acceptance)


class WorldBible(BaseModel):
    """世界观圣经：硬规则/结构/禁忌/特殊设定。"""

    era: str = ""
    rules: list[str] = Field(default_factory=list)
    social_structure: str = ""
    taboos: list[str] = Field(default_factory=list)
    special_settings: list[str] = Field(default_factory=list)


class CharacterBible(BaseModel):
    """人物圣经：欲望/需求/阻力/缺陷/弧光/关系/情绪向量初值。"""

    name: str = ""
    role: str = ""
    desire: str = ""
    need: str = ""
    obstacle: str = ""
    flaw: str = ""
    arc: list[dict] = Field(default_factory=list)
    relations: list[dict] = Field(default_factory=list)
    emotion_vector: dict[str, int] = Field(default_factory=dict)  # 关键关系对 A→B 初值


class LineBible(BaseModel):
    """四类线（四"类"非四条）：主线×1，支/明/暗可多条。"""

    kind: str = ""          # 主线/支线/明线/暗线
    name: str = ""
    carrier: str = ""
    start_ep: int = 1
    seed_eps: list[int] = Field(default_factory=list)
    resolve_eps: list[int] = Field(default_factory=list)
    summary: str = ""


class ConvergencePoint(BaseModel):
    """汇聚点：多线在爆点/关键场景汇合。"""

    ep: int = 0
    lines: list[str] = Field(default_factory=list)
    trigger: str = ""


class DesignConstraints(BaseModel):
    """设计约束（作者明确指定不可改）：反派归属/盟友站点/意象功能/不可改设定/关键转折顺序。"""

    villain: str = ""
    ally_stations: list[str] = Field(default_factory=list)
    motif_functions: list[str] = Field(default_factory=list)
    unchangeable: list[str] = Field(default_factory=list)
    key_turns: list[dict] = Field(default_factory=list)


class EpisodeSpec(BaseModel):
    """分集规格：每集时长/场数/爆点密度。"""

    total_eps: int = 80
    minutes_per_ep: float = 2.5
    scenes_per_ep: str = "4-6"
    explosion_density: str = "每集或每两集一个爆点"


class StoryBible(BaseModel):
    """世界圣经（硬约束）：从梗概推理，生成一切环节以此为不可违背基准。"""

    title: str = ""
    genre: str = ""
    logline: str = ""
    theme: str = ""
    premise: str = ""
    world: WorldBible = Field(default_factory=WorldBible)
    characters: list[CharacterBible] = Field(default_factory=list)
    lines: list[LineBible] = Field(default_factory=list)
    convergence_points: list[ConvergencePoint] = Field(default_factory=list)
    motifs: list[str] = Field(default_factory=list)
    design_constraints: DesignConstraints = Field(default_factory=DesignConstraints)
    episode_spec: EpisodeSpec = Field(default_factory=EpisodeSpec)


class ScriptPackage(BaseModel):
    """一篇剧本的全部交付物。"""

    brief: StoryBrief = Field(default_factory=StoryBrief)
    story_line: StoryLine = Field(default_factory=StoryLine)
    outline: Outline = Field(default_factory=Outline)
    episodes: list[EpisodeScript] = Field(default_factory=list)
    shot_prompts: list[ShotPrompt] = Field(default_factory=list)
    quality_report: QualityReport = Field(default_factory=QualityReport)
    manifest: ArchiveManifest = Field(default_factory=ArchiveManifest)