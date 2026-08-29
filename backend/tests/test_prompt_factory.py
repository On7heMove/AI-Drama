"""统一 Prompt 入口单测（§17）：stage 注入 / 负面提示词 / build_user。"""
from __future__ import annotations

from app import prompt_factory


def test_inject_skill_build_spine_has_rules():
    s = prompt_factory.inject_skill("build_spine")
    assert "编剧方法论约束" in s
    assert "conflict_types" in s or "beat_structures" in s or "arc_shapes" in s


def test_inject_skill_unknown_stage_empty():
    assert prompt_factory.inject_skill("no_such_stage") == ""


def test_default_negatives_anti_hallucination():
    s = prompt_factory.default_negatives()
    assert "负面提示词" in s
    assert "只以梗概为唯一事实源" in s
    assert "不得把梗概未提及的角色" in s


def test_build_user_fills_and_injects():
    user = prompt_factory.build_user("build_spine", "题目：{{TITLE}}；梗概：{{BRIEF}}",
                                     TITLE="残片", BRIEF="六枚残片")
    assert "题目：残片；梗概：六枚残片" in user
    assert "编剧方法论约束" in user
    assert "负面提示词" in user


def test_build_user_quality_no_negatives():
    user = prompt_factory.build_user("quality", "质检：{{X}}", X="1")
    assert "负面提示词" not in user
