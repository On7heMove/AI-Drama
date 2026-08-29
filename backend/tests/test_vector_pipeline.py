"""20维向量完整链路（vector_pipeline）单测。"""
import json

import pytest

from app.storyboard.vector_pipeline import assemble_prompt, render_scene_prompt


def _stub(v_override=None, a_override=None):
    class Stub:
        async def chat(self, system, user, **kw):
            v = {"subject_count": 0.67, "conflict_level": 0.9, "tension": 0.7, "valence": -0.8,
                 "arousal": 0.8, "pacing_need": 0.8, "motion_level": 0.9, "spatial_openness": 0.8,
                 "novelty": 0.5, "danger": 0.8, "valence_contrast": 0.3}
            a = {"monologue": 0.0, "dialogue": 0.1, "standoff": 0.1, "chase": 0.9, "intimacy": 0.1,
                 "ceremony": 0.0, "combat": 0.3, "empty": 0.0, "aerial": 0.9}
            if v_override: v.update(v_override)
            if a_override: a.update(a_override)
            return json.dumps({"vector": v, "affinity": a, "confidence": 0.9})
    return Stub()


class TestPipeline:
    @pytest.mark.asyncio
    async def test_full(self):
        r = await render_scene_prompt("摩托疾驰，仇家车队紧咬，无人机俯冲", client=_stub(), scene_id="s1")
        assert r["scene_id"] == "s1"
        assert r["render"]["branch"]
        assert r["skills"]["hit_skills"]
        assert "【风格锁定】" in r["prompt"]
        assert "【负面】" in r["prompt"]

    def test_assemble(self):
        p = assemble_prompt(
            {"confidence": 0.9},
            {"params": {"景别分布": ["特写"], "机位角度": ["过肩"], "运镜": ["手持"],
                        "负面": "禁越轴"}, "branch": "x"},
            {"advices": [{"slug": "confrontation-shot-techniques", "source_book": "《大师镜头》"}]})
        assert "【画面】" in p and "【负面】" in p and "confrontation-shot-techniques" in p
