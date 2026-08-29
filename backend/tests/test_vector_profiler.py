"""20维向量 profiler 单测：normalize / validate / render 链路 / LLM stub。"""
import json

import pytest

from app.storyboard.vector_profiler import (
    DEFAULT_AFFINITY,
    DEFAULT_VECTOR,
    normalize,
    profile_scene,
    render_from_profile,
    validate,
)


class TestNormalize:
    def test_bounds_and_fill(self):
        raw = {"vector": {"subject_count": 2.5, "tension": -1}, "affinity": {"dialogue": 1.5},
               "confidence": 1.2}
        n = normalize(raw)
        assert n["vector"]["subject_count"] == 1.0
        assert n["vector"]["tension"] == 0.0
        assert n["affinity"]["dialogue"] == 1.0
        assert n["confidence"] == 1.0
        # 缺失维度补默认
        for d in DEFAULT_VECTOR:
            assert d in n["vector"]
        for d in DEFAULT_AFFINITY:
            assert d in n["affinity"]

    def test_empty(self):
        n = normalize({})
        assert n["vector"]["valence"] == 0.0
        assert n["affinity"]["dialogue"] == 0.8


class TestValidate:
    def test_consistency(self):
        assert validate({"spatial_openness": 0.2}, {"aerial": 0.9})
        assert validate({"motion_level": 0.2}, {"chase": 0.9})
        assert validate({"conflict_level": 0.2}, {"combat": 0.9})
        assert validate({"valence": -0.5}, {"intimacy": 0.9})
        assert not validate({"spatial_openness": 0.8}, {"aerial": 0.9})


class TestRender:
    def test_render_from_profile(self):
        v = {k: 0.5 for k in ("subject_count", "conflict_level", "tension", "valence",
                              "arousal", "pacing_need", "motion_level", "spatial_openness",
                              "novelty", "danger", "valence_contrast")}
        a = {k: 0.0 for k in ("monologue", "dialogue", "standoff", "chase", "intimacy",
                              "ceremony", "combat", "empty", "aerial")}
        a["chase"] = 0.9
        r = render_from_profile({"vector": v, "affinity": a, "confidence": 0.9})
        assert r["branch"]
        assert "景别分布" in r["params"]


class TestProfileScene:
    @pytest.mark.asyncio
    async def test_llm_success(self):
        class Stub:
            async def chat(self, system, user, **kw):
                return json.dumps({"vector": {"subject_count": 0.67, "conflict_level": 0.9,
                                              "tension": 0.7, "valence": -0.8, "arousal": 0.8,
                                              "pacing_need": 0.8, "motion_level": 0.9,
                                              "spatial_openness": 0.8, "novelty": 0.5,
                                              "danger": 0.8, "valence_contrast": 0.3},
                                   "affinity": {"monologue": 0.0, "dialogue": 0.1, "standoff": 0.1,
                                                "chase": 0.9, "intimacy": 0.1, "ceremony": 0.0,
                                                "combat": 0.3, "empty": 0.0, "aerial": 0.9},
                                   "confidence": 0.9})
        p = await profile_scene("航拍穿越：无人机高空俯瞰城市", client=Stub(), scene_id="s1")
        assert p["scene_id"] == "s1"
        assert p["vector"]["conflict_level"] == 0.9
        assert p["affinity"]["aerial"] == 0.9
        assert p["confidence"] == 0.9  # aerial 高且 openness 高 → 无 issue，不降置信
        assert p["issues"] == []

    @pytest.mark.asyncio
    async def test_llm_fail_fallback(self):
        class Bad:
            async def chat(self, system, user, **kw):
                raise RuntimeError("boom")
        p = await profile_scene("随便", client=Bad())
        assert p["vector"]["valence"] == 0.0
        assert p["affinity"]["dialogue"] == 0.8
