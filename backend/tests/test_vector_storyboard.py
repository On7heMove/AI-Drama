"""产线二向量分镜生成器（vector_storyboard）单测。"""
import json

import pytest

from app.promptgen.schemas import ParsedScene, ParsedScript
from app.promptgen.vector_storyboard import build_vector_storyboard_prompts, scene_context


def _script():
    s1 = ParsedScene(scene_id="s1", location="办公室", time="夜", emotion="压抑",
                     participants=["苏天佑", "安保主管"],
                     action_blocks=["苏天佑端着咖啡站在窗边，雨水在玻璃上滑落"],
                     dialogues=[{"speaker": "苏天佑", "line": "苏冷呢？"},
                                {"speaker": "安保主管", "line": "她凌晨从消防梯下了楼"}])
    from app.promptgen.schemas import ParsedEpisode
    return ParsedScript(title="天罗", genre="都市", episodes=[ParsedEpisode(ep=1, scenes=[s1])])


class TestSceneContext:
    def test_ctx(self):
        c = scene_context(_script().episodes[0].scenes[0])
        assert "办公室" in c and "苏天佑" in c and "苏冷呢" in c


class TestBuild:
    @pytest.mark.asyncio
    async def test_vids(self):
        class Stub:
            async def chat(self, system, user, **kw):
                return json.dumps({
                    "vector": {"subject_count": 0.67, "conflict_level": 0.7, "tension": 0.7,
                               "valence": -0.5, "arousal": 0.5, "pacing_need": 0.5,
                               "motion_level": 0.3, "spatial_openness": 0.4, "novelty": 0.4,
                               "danger": 0.3, "valence_contrast": 0.4},
                    "affinity": {"monologue": 0.0, "dialogue": 0.9, "standoff": 0.5,
                                 "chase": 0.0, "intimacy": 0.1, "ceremony": 0.0,
                                 "combat": 0.0, "empty": 0.0, "aerial": 0.0},
                    "confidence": 0.85})
        vids = await build_vector_storyboard_prompts(_script(), client=Stub())
        assert len(vids) == 1
        v = vids[0]
        assert v["scene_id"] == "s1"
        assert v["image_prompts"] and "【风格锁定】" in v["image_prompts"][0]
        assert v["vector"]["branch"]
