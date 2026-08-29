# -*- coding: utf-8 -*-
"""app.state.character_state：角色状态模型（backend 合并补齐）。

BodyDamage 六部位伤害枚举；Consciousness/Stance 状态枚举；ActiveEffect 效果台账；
CharacterState 聚合（pydantic，支持 model_copy(deep=True)/model_dump）。
"""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class BodyDamage(str, Enum):
    NONE = "none"
    LIGHT = "light"
    SEVERE = "severe"
    FATAL = "fatal"


class Consciousness(str, Enum):
    CONSCIOUS = "conscious"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"


class Stance(str, Enum):
    STANDING = "standing"
    KNEELING = "kneeling"
    PRONE = "prone"
    RESTRAINED = "restrained"


class ActiveEffect(BaseModel):
    type: str = ""
    note: str = ""


class BodyState(BaseModel):
    head: BodyDamage = BodyDamage.NONE
    left_arm: BodyDamage = BodyDamage.NONE
    right_arm: BodyDamage = BodyDamage.NONE
    torso: BodyDamage = BodyDamage.NONE
    left_leg: BodyDamage = BodyDamage.NONE
    right_leg: BodyDamage = BodyDamage.NONE


class CharacterState(BaseModel):
    character: str = ""
    alive: bool = True
    consciousness: Consciousness = Consciousness.CONSCIOUS
    stance: Stance = Stance.STANDING
    body: BodyState = Field(default_factory=BodyState)
    effects: list[ActiveEffect] = Field(default_factory=list)
