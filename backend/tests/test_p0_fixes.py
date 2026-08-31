# -*- coding: utf-8 -*-
"""P0 修复回归测试（2026-08-31）：自动回炉(P0-3)、并发画幅(P0-5)、时长统一(P0-6)。
全部离线，不调用真实模型/网络/密钥。
"""
from __future__ import annotations
import asyncio
from types import SimpleNamespace

import pytest

from app.inverse.service import MAX_EPISODE_RETRIES, format_quality_feedback, run_inverse
from app.production.schemas import EpisodeScript, QualityItem
from app.production import storyboard_export
from app.production.storyboard_export import build_shot_prompts, effective_shot_duration


# ---------------------------------------------------------------- P0-6 时长统一
class _Shot:
    duration_sec = 3.0
    sound = ""


def test_effective_shot_duration_visual_only():
    assert effective_shot_duration(_Shot()) == pytest.approx(3.0)
    assert effective_shot_duration(None) == pytest.approx(3.0)


def test_effective_shot_duration_adds_dialogue():
    beat = SimpleNamespace(duration_sec=2.0, dialogue="陈思扬：We need to leave now")
    total = effective_shot_duration(_Shot(), beat)
    assert total > 2.0  # 总时长 = 视觉 + 对白折算
    # 节拍未显式给时长 → 回退镜头时长
    beat2 = SimpleNamespace(duration_sec=None, dialogue="")
    assert effective_shot_duration(_Shot(), beat2) == pytest.approx(3.0)


def test_build_video_prompt_from_beat_displays_total_duration():
    from app.production.storyboard_export import build_video_prompt_from_beat
    shot = SimpleNamespace(
        duration_sec=3.0, sound="", scale="近景", angle="平视", movement="固定机位",
        stability="稳定", staging="", tone="", lighting="", edit="", blocking="",
        camera_pos="", content="", emotion="", block=None,
    )
    scene = SimpleNamespace(
        action_blocks=[], dialogues=[], location="避难所", time="夜", blocking="",
        participants=[],
    )
    beat = SimpleNamespace(
        duration_sec=2.0, dialogue="陈思扬：We must act now", action="推门",
        emotion="紧张", subject="陈思扬", scale="", angle="", movement="",
        staging="", camera_pos="", sound="", lighting="", blocking="",
    )
    total = effective_shot_duration(shot, beat)
    out = build_video_prompt_from_beat(scene, beat, shot, emotion="紧张",
                                       scene_type="", genre="", aspect="16:9", viewpoint=None)
    assert f"约{total:.0f}s" in out  # 展示口径与 effective_shot_duration 一致（含对白）


# ---------------------------------------------------------------- P0-5 并发画幅
def test_no_module_global_aspect_viewpoint():
    assert not hasattr(storyboard_export, "_CURRENT_ASPECT")
    assert not hasattr(storyboard_export, "_CURRENT_VIEWPOINT")


def test_style_lock_aspect_isolation():
    from app.production.storyboard_export import _style_lock, _style_lock_en
    a9 = _style_lock("9:16")
    a16 = _style_lock("16:9")
    assert a9 != a16
    assert "9:16" in a9 and "16:9" in a16
    # 连续调用互不污染
    assert _style_lock("9:16") == a9
    assert _style_lock("16:9") == a16
    assert _style_lock_en("9:16") != _style_lock_en("16:9")


def test_build_video_prompt_aspect_isolated():
    from app.production.storyboard_export import build_video_prompt
    shot = SimpleNamespace(
        duration_sec=3.0, sound="", scale="近景", angle="平视", movement="固定机位",
        stability="稳定", staging="", tone="", lighting="", edit="", blocking="",
        camera_pos="", content="男人缓缓抬头", emotion="",
    )
    scene = SimpleNamespace(
        action_blocks=["男人缓缓抬头"], dialogues=[], location="避难所", time="夜",
        blocking="",
    )
    p9 = build_video_prompt(scene, shot, emotion="紧张", scene_type="", genre="",
                            aspect="9:16", viewpoint=None)
    p16 = build_video_prompt(scene, shot, emotion="紧张", scene_type="", genre="",
                             aspect="16:9", viewpoint=None)
    assert "9:16" in p9 and "16:9" not in p9.split("【风格锁定】")[1].split("\n")[0]
    assert "16:9" in p16
    assert p9 != p16


# ---------------------------------------------------------------- P0-3 自动回炉
def test_format_quality_feedback_contains_evidence_and_suggestion():
    item = QualityItem(ep=1, dimension="cliffhanger", passed=False, severity="fatal",
                       evidence="第1集结尾钩子为空", suggestion="补一个结尾钩子")
    fb = format_quality_feedback([item])
    assert "上轮质检未通过项" in fb
    assert "cliffhanger/硬伤" in fb
    assert "第1集结尾钩子为空" in fb
    assert "补一个结尾钩子" in fb
    assert format_quality_feedback([]) == ""


class _RetryStub:
    """spine/beats 固定；episode：首轮坏（无钩/非法事件），次轮好（钩齐全、空场景）。"""
    model = "stub-llm"

    def __init__(self):
        self.calls = 0
        self.users: list[str] = []

    async def chat(self, system: str, user: str, **kw) -> str:
        return "stub screenplay text"

    async def chat_json(self, system: str, user: str, **kw) -> dict:
        self.calls += 1
        self.users.append(user)
        if self.calls == 1:
            return {
                "spine_title": "破芯·人间重燃",
                "nodes": [{"node_id": "n01", "type": "挫折", "title": "芯片失效",
                           "event": "彗星磁变", "function": "开局", "position": "开局",
                           "resolve_ep": 1}],
                "casting": [{"name": "陈思扬", "role": "主角", "goal": "守护", "flaw": "恐惧"}],
                "open_questions": [], "links": [],
            }
        if self.calls == 2:
            return {"beats": [{"ep": 1, "title": "第1集",
                               "hook_open": "", "hook_end": "", "explosion": "",
                               "explosion_type": "反转", "scenes": [],
                               "emotional_curve": ["起", "升", "钩"],
                               "lines_advanced": ["协作线"]}]}
        if self.calls == 3:  # 首轮：缺钩 + 非法事件 → 回炉
            return {
                "scenes": [],
                "events": [{"type": "飞升", "actor": "陈思扬", "detail": "", "citation": "第1集"}],
                "summary": "坏版本",
                "state_update": {"resolved": [], "open_threads": []},
            }
        return {  # 次轮：钩齐全、空场景、无非法事件 → 门禁通过
            "hook_open": "水危机",
            "hook_end": "残片未解",
            "explosion": "入口被冲击",
            "scenes": [],
            "events": [],
            "summary": "好版本",
            "state_update": {"resolved": [], "open_threads": ["管道未清"]},
        }


def test_run_inverse_auto_refine_loop():
    stub = _RetryStub()
    result = asyncio.run(run_inverse(
        title="破芯·人间重燃", genre="科幻",
        synopsis="芯片失效后人类重新摸索生存。",
        brief={"theme": "带感情的人才是人"},
        eps_start=1, eps_end=1, client=stub))
    assert stub.calls == 4  # spine + beats + 坏版 + 好版
    # 第二轮 prompt 注入质检反馈
    assert "上轮质检未通过项" in stub.users[3]
    assert "上轮质检未通过项" not in stub.users[2]
    retry = result["retry"]
    assert retry["total_retries"] == 1
    assert len(retry["episodes"]) == 1
    ep_retry = retry["episodes"][0]
    assert ep_retry["ep"] == 1 and ep_retry["retry_rounds"] == 1
    assert ep_retry["first_errors"] and not ep_retry["final_errors"]
    # 最终接受的是好版本
    assert result["episodes"][0]["hook_end"] == "残片未解"
    assert result["episodes"][0]["events"] == []
    assert result["state_updates"][0]["retry_rounds"] == 1
    assert result["quality"]["retry_rounds"] == 1



def test_en_fallback_movement_verb_does_not_crash():
    """P1-12 回归：英文本地兜底在移动动词下不再因 vc.family 缺失崩溃。"""
    from app.production.storyboard_export import build_en_video_prompt
    shot = SimpleNamespace(
        duration_sec=3.0, sound="", scale="近景", angle="平视", movement="固定机位",
        stability="稳定", staging="", tone="", lighting="", edit="", blocking="",
        camera_pos="", content="", emotion="",
    )
    scene = SimpleNamespace(
        action_blocks=["陈思扬跑向避难所"], dialogues=[], location="避难所", time="夜",
        blocking="", lighting="",
    )
    out = build_en_video_prompt(scene, shot, emotion="紧张", aspect="16:9", viewpoint=None)
    assert "Scene:" in out
    assert "run" in out or "walk" in out or "moves" in out
