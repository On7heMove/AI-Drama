"""对白镜调度长效机制（2026-08-14）：说台词时画面必须有调度，拒绝静态PPT。

- 有对白的镜头：机位不得停在"固定/静态"——按情绪给有动机的运镜（缓推/急推/手持微晃…）
- 有对白的镜头：调度必须含说话人动作/微动作（身体语言），缺失则按情绪补
- 联动：台词=画面内有声表演，机位随情绪推近；镜头之间靠 正反打/反应/视线匹配 衔接
配置：config/storyboard/dialogue_staging.json
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

_PATH = data_root() / "config" / "storyboard" / "dialogue_staging.json"

_STATIC = ("固定", "固定机位", "locked", "static")


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


def speaker_action_keywords() -> tuple[str, ...]:
    return tuple(_data().get("speaker_action_keywords", []))


def has_speaker_action(staging: str) -> bool:
    """调度文本是否已含说话人动作/微动作信号（说话/逼近/目光/喉结/呼吸…）。"""
    if not staging:
        return False
    return any(k in staging for k in speaker_action_keywords())


def is_static(movement: str) -> bool:
    return not movement or any(s in movement for s in _STATIC)


def micro_action_zh(emotion: str) -> str:
    return _data().get("micro_actions", {}).get(emotion, "")


def movement_by_emotion(emotion: str) -> str:
    m = _data().get("movement_by_emotion", {}).get(emotion, "")
    return m or _data().get("default_dialogue_movement", "缓推")


def augment_staging_zh(staging: str, emotion: str) -> str:
    """对白镜调度补全：缺说话人动作 → 按情绪补微动作（幂等）。"""
    if staging and has_speaker_action(staging):
        return staging
    micro = micro_action_zh(emotion)
    if not micro:
        micro = "目光随之移动、身体微微前倾"  # 默认微动作兜底：对白镜必有【调度】（拒绝静态PPT）
    return f"{staging}；说话时{micro}" if staging else f"说话时{micro}"


def upgrade_movement_zh(movement: str, emotion: str) -> str:
    """对白镜机位不得静态：固定/空 → 按情绪给运镜（幂等）。"""
    if is_static(movement):
        return movement_by_emotion(emotion)
    return movement


def enrich_dialogue_shot(shot, beat, emotion: str) -> None:
    """就地补全对白镜的 调度(说话人动作) 与 机位运动(有动机运镜)。shot 可空。"""
    if shot is None or not getattr(beat, "dialogue", ""):
        return
    shot.staging = augment_staging_zh(getattr(shot, "staging", "") or "", beat.emotion or emotion)
    shot.movement = upgrade_movement_zh(getattr(shot, "movement", "") or "", beat.emotion or emotion)
