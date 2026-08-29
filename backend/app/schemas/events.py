# -*- coding: utf-8 -*-
"""app.schemas.events：剧本事件结构（backend 合并补齐）。"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    ACQUISITION_ACTION = "acquisition_action"
    CLAIM = "claim"
    STATE_CHANGE = "state_change"
    ACTION = "action"
    CONFLICT = "conflict"
    DECISION = "decision"
    CLUE_REVEAL = "clue_reveal"
    RELATIONSHIP_CHANGE = "relationship_change"
    EMOTION_CHANGE = "emotion_change"
    SCENE_SHIFT = "scene_shift"
    PLAN_FORMATION = "plan_formation"
    PLAN_EXECUTION = "plan_execution"
    PLAN_COMPLETION = "plan_completion"
    CRISIS = "crisis"
    RESOLUTION = "resolution"
    FALL = "fall"
    FIGHT = "fight"
    INJURY = "injury"
    RISE = "rise"
    SPEECH = "speech"
    UNCONSCIOUS = "unconscious"
    WIELD = "wield"


@dataclass(frozen=True)
class TimeRef:
    anchor: Optional[str] = None
    order: Optional[float] = None


@dataclass(frozen=True)
class Event:
    event_id: str
    chapter: str
    seq: int
    type: EventType
    actor: str
    target: Optional[str] = None
    detail: str = ""
    citation: str = ""
    time: Optional[TimeRef] = None
