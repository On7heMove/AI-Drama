"""25 技能契约内置（skill_contract）单测。"""
from app.storyboard.skill_contract import apply_skills, call_skill, route_skills


class TestRoute:
    def test_trigger(self):
        assert "confrontation-shot-techniques" in route_skills("两人对峙，丈夫故作镇定")
        assert "audiovisual-added-value" in route_skills("先闻其声再看其人")

    def test_affinity(self):
        aff = {"aerial": 0.9, "dialogue": 0.1}
        hits = route_skills("无人机高空俯冲", aff)
        assert "aerial-shot-techniques" in hits

    def test_empty(self):
        assert route_skills("普通场景", {"dialogue": 0.2}) == []


class TestCall:
    def test_existing(self):
        r = call_skill("对峙", {"conflict_level": 0.8}, {"standoff": 0.9}, "confrontation-shot-techniques")
        assert r and r["slug"] == "confrontation-shot-techniques"
        assert r["source_book"]

    def test_missing(self):
        assert call_skill("x", {}, {}, "not-exist") is None


class TestApply:
    def test_apply(self):
        prof = {"vector": {"conflict_level": 0.7}, "affinity": {"standoff": 0.9, "dialogue": 0.4}}
        r = apply_skills("两人对峙，谁怕谁", prof)
        assert r["hit_skills"]
        assert r["advices"]
