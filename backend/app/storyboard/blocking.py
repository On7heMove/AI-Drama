"""人物站位推理（2026-08-14）：从剧本原文推断 谁左谁右/朝向/视线，输出场景级站位表。

已确认规则：
- 站位表 = 场景级（每景一张，镜头继承）
- 左右偏好 = 主动方/视角人物 居左（锚点）
- 多人布位 = 由剧情推理；复杂布局可后期用故事板/显式 blocking 覆盖
- 推理边界 = 仅 对话/对峙/群像 需要；动作/空镜 用默认防越轴句
- 人工覆盖优先 = 显式 blocking > 推理 > 默认
"""
from __future__ import annotations

from dataclasses import dataclass, field

_REQUIRED_SCENE_TYPES = {"dialogue", "suspense", "reveal"}
_REQUIRED_KEYWORDS = ("说", "道", "问", "答", "谈", "吵", "质问", "谈判", "对峙", "商量",
                      "解释", "坦白", "威胁", "请求", "告诉", "秘密", "耳语", "私语",
                      "演讲", "发言", "告白", "争吵", "训话", "汇报")
_SUPPORT_WORDS = ("帮", "扶", "支持", "站到", "护卫", "同盟", "搀")
_OPPOSE_WORDS = ("围", "押", "逼", "怒视", "对峙", "包围")


@dataclass
class BlockingEntry:
    name: str
    side: str = "center"     # left/right/left_back/right_back/back/front/center
    facing: str = "toward"   # left/right/toward/away/profile
    gaze: str = ""           # 视线目标
    note: str = ""


_SIDE_ZH = {"left": "画面左", "right": "画面右", "left_back": "左后方", "right_back": "右后方",
            "back": "后方", "front": "前景", "center": "居中"}


@dataclass
class BlockingTable:
    scene_id: str = ""
    active: str = ""                       # 主动方/视角人物（锚点，居左）
    axis: tuple[str, str] = ()             # (主动方, 主要对手)
    entries: list[BlockingEntry] = field(default_factory=list)
    required: bool = True

    def to_prompt(self) -> str:
        if not self.required or not self.active:
            return "默认：保持 180° 轴线；人物相对站位恒定，禁止左右互换、禁止越轴跳切"
        a = self.active
        b = self.axis[1] if len(self.axis) > 1 and self.axis[1] else ""
        if not b:
            # 单主体场景（无对手）：不存在左右相对关系，不设轴线（2026-08-20 用户指示：轴线非必须项）
            parts = [f"{a}为画面主体（单人场景，不设轴线）"]
            for e in self.entries:
                if e.name == a:
                    continue
                parts.append(f"{e.name}布于{_SIDE_ZH.get(e.side, e.side)}")
            parts.append("禁止左右互换、禁止越轴跳切")
            return "；".join(parts)
        parts = [f"{a}在画面左、{b}在画面右，面对面", f"轴线={a}→{b}", f"{a}看右、{b}看左"]
        for e in self.entries:
            if e.name in (a, b):
                continue
            parts.append(f"{e.name}布于{_SIDE_ZH.get(e.side, e.side)}")
        parts.append("禁止左右互换、禁止越轴跳切")
        return "；".join(parts)


def _participants(scene) -> list[str]:
    parts = list(getattr(scene, "participants", None) or [])
    for d in getattr(scene, "dialogues", []) or []:
        if d.speaker and d.speaker not in parts:
            parts.append(d.speaker)
    # 去重：保留首次出现顺序，避免站位表重复人物（如 伊索尔德布于后方×3）
    return list(dict.fromkeys(parts))


def _needs_blocking(scene) -> bool:
    st = getattr(scene, "scene_type", "") or ""
    if st in _REQUIRED_SCENE_TYPES:
        return True
    dlg = getattr(scene, "dialogues", []) or []
    if dlg:
        return True  # 有对白=对话/对峙/群像，需要站位推理
    text = " ".join(getattr(scene, "action_blocks", []) or [])
    return any(k in text for k in _REQUIRED_KEYWORDS)


def _detect_active(scene, viewpoint: str | None) -> str | None:
    parts = _participants(scene)
    if not parts:
        return None
    if viewpoint and viewpoint in parts:
        return viewpoint
    for d in getattr(scene, "dialogues", []) or []:
        if d.speaker:
            return d.speaker
    for blk in getattr(scene, "action_blocks", []) or []:
        for p in parts:
            if p and p in blk:
                return p
    return parts[0]


def _detect_counterpart(scene, active: str) -> str | None:
    parts = [p for p in _participants(scene) if p != active]
    if not parts:
        return None
    # 优先取与 active 交替说话、且台词最多的对手；简化：第一个其他参与者
    return parts[0]


def infer_blocking(scene, viewpoint: str | None = None) -> BlockingTable:
    """场景级站位推理：返回 BlockingTable（required=False 表示无需推理，走默认防越轴句）。"""
    sid = getattr(scene, "scene_id", "") or ""
    if not _needs_blocking(scene):
        return BlockingTable(scene_id=sid, required=False)
    active = _detect_active(scene, viewpoint)
    if active is None:
        return BlockingTable(scene_id=sid, required=False)
    counterpart = _detect_counterpart(scene, active)
    entries = [BlockingEntry(name=active, side="left", facing="right", gaze=counterpart or "")]
    if counterpart:
        entries.append(BlockingEntry(name=counterpart, side="right", facing="left", gaze=active))
    text = " ".join(getattr(scene, "action_blocks", []) or [])
    for p in _participants(scene):
        if p in (active, counterpart):
            continue
        if any(k in text for k in _OPPOSE_WORDS):
            side = "right_back"
        elif any(k in text for k in _SUPPORT_WORDS):
            side = "left_back"
        else:
            side = "back"
        entries.append(BlockingEntry(name=p, side=side, facing="toward"))
    return BlockingTable(scene_id=sid, active=active, axis=(active, counterpart or ""),
                         entries=entries, required=True)
