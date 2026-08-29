# -*- coding: utf-8 -*-
"""app.state.store：事件溯源状态机（backend 合并补齐）。

EventSourcingStore：apply(event) 更新角色状态并收集矛盾 violation；
snapshot(cid, scope="main") 取快照；snapshots()/violations() 供 main 使用；
apply_many(events) 批量。
"""
from __future__ import annotations
from typing import Optional

from app.schemas.events import Event, EventType
from app.state.character_state import (
    ActiveEffect, BodyDamage, CharacterState, Consciousness, Stance,
)
from app.schemas.issues import Severity


class Violation:
    def __init__(self, rule_id: str, severity: Severity, message: str,
                 location: str = "", evidence: str = "", suggestion: str = ""):
        self.rule_id = rule_id
        self.severity = severity
        self.message = message
        self.location = location
        self.evidence = evidence
        self.suggestion = suggestion

    def model_dump(self) -> dict:
        return {"rule_id": self.rule_id, "severity": self.severity.value,
                "message": self.message, "location": self.location,
                "evidence": self.evidence, "suggestion": self.suggestion}


class EventSourcingStore:
    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], CharacterState] = {}
        self._violations: list[Violation] = []

    def _state(self, cid: str, scope: str = "main") -> CharacterState:
        key = (cid, scope)
        if key not in self._snapshots:
            self._snapshots[key] = CharacterState(character=cid)
        return self._snapshots[key]

    def snapshot(self, cid: str, scope: str = "main") -> CharacterState:
        return self._state(cid, scope)

    def snapshots(self) -> dict[tuple[str, str], CharacterState]:
        return dict(self._snapshots)

    def violations(self) -> list[Violation]:
        return list(self._violations)

    def apply(self, ev: Event) -> None:
        if not ev.actor:
            return
        st = self._state(ev.actor)
        if not st.alive and ev.type in (EventType.ACTION, EventType.CLAIM):
            self._violations.append(Violation(
                rule_id="acting_while_down", severity=Severity.ERROR,
                location="%s #%s" % (ev.chapter, ev.seq),
                message="%s 已倒地/死亡仍执行 %s" % (ev.actor, ev.type.value),
                evidence=ev.detail or ev.citation,
                suggestion="修正事件顺序或恢复角色状态"))
        if ev.type == EventType.STATE_CHANGE:
            detail = ev.detail or ""
            if "死亡" in detail or "倒下" in detail and "挣扎" not in detail:
                st.alive = False
            if "昏迷" in detail:
                st.consciousness = Consciousness.UNCONSCIOUS
            if "跪下" in detail:
                st.stance = Stance.KNEELING
            if "束缚" in detail or "控制" in detail:
                st.stance = Stance.RESTRAINED
            if "重伤" in detail:
                st.body.torso = BodyDamage.SEVERE
            if detail:
                st.effects.append(ActiveEffect(type="state_change", note=detail[:60]))
        elif ev.type == EventType.ACQUISITION_ACTION:
            st.effects.append(ActiveEffect(type="acquisition", note=(ev.detail or ev.target or "")[:60]))
        elif ev.type == EventType.CLAIM:
            st.effects.append(ActiveEffect(type="claim", note=(ev.detail or ev.target or "")[:60]))
        if ev.time and ev.time.anchor:
            st.effects.append(ActiveEffect(type="time", note=ev.time.anchor))

    def apply_many(self, events: list[Event]) -> None:
        for ev in events:
            self.apply(ev)
