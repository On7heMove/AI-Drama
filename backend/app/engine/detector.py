# -*- coding: utf-8 -*-
"""app.engine.detector：确定性剧本逻辑检测引擎（backend 合并补齐）。

对结构化事件流做连续性/一致性检测：
- 重复事件（同 chapter+seq）
- 同 actor 时间轴回退（chapter/seq 逆序）
- 需要 target 的事件缺 target
- 同 actor 同 chapter 事件过密（节奏风险）
返回 DetectionReport.violations（rule_id/severity/message/location/evidence/suggestion）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from app.schemas.events import Event, EventType
from app.schemas.issues import Severity

_TARGET_REQUIRED = {EventType.CLAIM, EventType.ACQUISITION_ACTION, EventType.RELATIONSHIP_CHANGE,
                    EventType.DECISION}


@dataclass
class Violation:
    rule_id: str
    severity: Severity
    message: str
    location: str = ""
    evidence: str = ""
    suggestion: str = ""


@dataclass
class DetectionReport:
    violations: list = field(default_factory=list)


def _chapter_no(chapter: str) -> Optional[int]:
    import re
    m = re.search(r"(\d+)", chapter or "")
    return int(m.group(1)) if m else None


class DetectionEngine:
    """确定性检测：不调用 LLM；任何不明确处倾向保守（suggestion 而非 error）。"""

    def process(self, events: list[Event]) -> DetectionReport:
        violations: list[Violation] = []
        seen: dict[str, str] = {}          # event_id -> location
        last_key: dict[str, tuple[int, int]] = {}   # actor -> (chapter_no, seq)
        density: dict[str, int] = {}       # (chapter, actor) -> count
        for e in events:
            loc = "%s #%s" % (e.chapter, e.seq)
            if e.event_id in seen:
                violations.append(Violation(
                    rule_id="dup_event", severity=Severity.ERROR, location=loc,
                    message="重复事件 event_id=%s" % e.event_id,
                    evidence="首次出现在 %s" % seen[e.event_id],
                    suggestion="修正事件 id 或合并重复事件"))
                continue
            seen[e.event_id] = loc

            if e.type in _TARGET_REQUIRED and not e.target:
                violations.append(Violation(
                    rule_id="missing_target", severity=Severity.STRONG, location=loc,
                    message="事件类型 %s 需要 target 但缺失" % e.type.value,
                    evidence=e.detail or e.citation, suggestion="补充 target 角色/对象"))

            cn = _chapter_no(e.chapter)
            if cn is not None and e.actor:
                key = (cn, e.seq)
                prev = last_key.get(e.actor)
                if prev is not None and key < prev:
                    violations.append(Violation(
                        rule_id="timeline_regression", severity=Severity.ERROR, location=loc,
                        message="%s 时间轴回退：%s -> %s" % (e.actor, prev, key),
                        evidence=e.detail or e.citation, suggestion="按时间顺序重排该 actor 事件"))
                last_key[e.actor] = max(prev, key) if prev else key
                den = (e.chapter, e.actor)
                density[den] = density.get(den, 0) + 1
                if density[den] > 6:
                    violations.append(Violation(
                        rule_id="actor_density", severity=Severity.SUGGESTION, location=loc,
                        message="%s 在第 %s 集事件过密（%d 条）" % (e.actor, e.chapter, density[den]),
                        evidence=e.citation, suggestion="拆分节奏或合并同类事件"))
        return DetectionReport(violations=violations)
