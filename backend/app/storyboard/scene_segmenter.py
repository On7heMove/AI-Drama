"""场景连续分段（2026-08-14）：同一场景的提示词连续，尽量一段提示词生成约15s视频。

- 场景优先：分段只在场景内进行，节拍不跨段、段不跨场景；场景边界=天然段界
- 15s次之：目标段长≈target_sec（默认15s），允许 [min_sec, max_sec]；
  为避免过碎，短段可吸收下一节拍到 hard_max_sec；超长节拍单独成段
- 段内连续：段内节拍按 动作/视线/机位 连续衔接（段内无硬切）；段与段之间给承接说明
配置：config/storyboard/scene_segment.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.paths import data_root
from app.storyboard.duration_rule import effective_duration

_PATH = data_root() / "config" / "storyboard" / "scene_segment.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class Segment:
    """场景内的一段连续视频（≈target_sec）。"""

    seg_index: int
    beats: list
    start_sec: float = 0.0
    end_sec: float = 0.0

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


def beat_duration(beat) -> float:
    """单节拍有效时长 = 视觉 + 对白（英文只算英文台词）+ 旁白。"""
    return effective_duration(beat.duration_sec or 3.0, getattr(beat, "dialogue", "") or "",
                              getattr(beat, "sound", "") or "")


def _make(segs: list[Segment], beats: list, start: float) -> Segment:
    dur = sum(beat_duration(b) for b in beats)
    return Segment(seg_index=len(segs) + 1, beats=list(beats), start_sec=start, end_sec=start + dur)


def segment_scene(scene, start_sec: float = 0.0) -> list[Segment]:
    """把一个场景的节拍按 ~15s 连续分段（贪心；场景优先，短段可吸收到 hard_max）。"""
    d = _data()
    target = float(d.get("target_sec", 15.0))
    max_sec = float(d.get("max_sec", 18.0))
    hard_max = float(d.get("hard_max_sec", 22.0))
    beats = list(getattr(scene, "beats", []) or [])
    segs: list[Segment] = []
    cur: list = []
    cur_dur = 0.0
    t = start_sec
    n = len(beats)
    for i, b in enumerate(beats):
        bd = beat_duration(b)
        is_last = i == n - 1
        if bd > hard_max:
            if cur:
                segs.append(_make(segs, cur, t))
                t += cur_dur
                cur, cur_dur = [], 0.0
            segs.append(_make(segs, [b], t))
            t += bd
            continue
        if cur:
            if cur_dur + bd > hard_max:
                segs.append(_make(segs, cur, t))
                t += cur_dur
                cur, cur_dur = [], 0.0
            elif cur_dur >= target and cur_dur + bd > max_sec and not is_last:
                # 场景末尾的最后一段可吸收到 hard_max（场景优先，避免碎段）
                segs.append(_make(segs, cur, t))
                t += cur_dur
                cur, cur_dur = [], 0.0
        cur.append(b)
        cur_dur += bd
    if cur:
        segs.append(_make(segs, cur, t))
    return segs


def audit_segments(scenes) -> list[str]:
    """段级校验：段长尽量落在目标窗口；过短/过长给提示（场景优先下允许硬上限内超窗）。"""
    issues: list[str] = []
    d = _data()
    target = float(d.get("target_sec", 15.0))
    min_sec = float(d.get("min_sec", 8.0))
    hard_max = float(d.get("hard_max_sec", 22.0))
    for sc in scenes:
        for seg in segment_scene(sc):
            if seg.duration > hard_max:
                issues.append(f"{sc.scene_id}/段{seg.seg_index}: 段长{seg.duration:.0f}s 超硬上限{hard_max:.0f}s")
            elif seg.duration < min_sec:
                issues.append(f"{sc.scene_id}/段{seg.seg_index}: 段长{seg.duration:.0f}s < {min_sec:.0f}s（过碎，可并入相邻段）")
    return issues
