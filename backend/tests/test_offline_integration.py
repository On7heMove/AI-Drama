# -*- coding: utf-8 -*-
"""stub LLM 离线集成测试：逆推 -> 生成 -> 质检闭环（不调真实模型/网络）。

StubClient 按调用顺序返回固定合法 JSON（spine / beats / episode），
验证 run_inverse 主链可端到端跑通并产出结构化结果。
"""
from __future__ import annotations
import asyncio

import pytest

from app.inverse.service import run_inverse


class StubClient:
    """最小 stub：chat_json 按顺序返回 spine/beats/episode；chat 返回文本。"""
    model = "stub-llm"

    def __init__(self):
        self.calls = 0

    async def chat(self, system: str, user: str, **kw) -> str:
        return "stub screenplay text"

    async def chat_json(self, system: str, user: str, **kw) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "spine_title": "破芯·人间重燃",
                "nodes": [{"node_id": "n01", "type": "挫折", "title": "芯片失效",
                           "event": "彗星磁变", "function": "开局", "position": "开局",
                           "resolve_ep": 1}],
                "casting": [{"name": "陈思扬", "role": "主角", "goal": "守护情感", "flaw": "恐惧失去"}],
                "open_questions": [], "links": [],
            }
        if self.calls == 2:
            return {"beats": [{"ep": 1, "title": "第1集", "hook_open": "水危机",
                               "hook_end": "残片未解", "explosion": "入口被冲击",
                               "explosion_type": "反转", "scenes": [],
                               "emotional_curve": ["起", "升", "钩"],
                               "lines_advanced": ["协作线"]}]}
        return {
            "scenes": [{"location": "避难所", "time": "夜",
                        "action_blocks": ["陈思扬按住抽搐的男人"],
                        "dialogues": [{"speaker": "陈思扬", "line": "按住他！"}]}],
            "events": [{"type": "action", "actor": "陈思扬", "target": "韩启铭",
                        "detail": "按住抽搐者", "citation": "第1集"}],
            "summary": "众人第一次有限协作",
            "state_update": {"resolved": [], "open_threads": ["管道未清"]},
        }


def test_offline_integration_run_inverse():
    stub = StubClient()
    result = asyncio.run(run_inverse(
        title="破芯·人间重燃", genre="科幻",
        synopsis="芯片失效后人类重新摸索生存，陈思扬与孙丽莎建立羁绊。",
        brief={"theme": "带感情的人才是人"},
        eps_start=1, eps_end=1, client=stub))
    assert result["title"]
    assert result["spine"]["spine_title"] == "破芯·人间重燃"
    assert result["spine"]["nodes"]
    assert len(result["beats"]) >= 1
    assert len(result["episodes"]) >= 1
    assert result["episodes"][0]["scenes"]
    assert "quality" in result
    assert "quality_items" in result
    assert stub.calls >= 3
