"""伏笔台账单测：登记/回收/未回收注入。"""
from __future__ import annotations

from app.story.foreshadow_ledger import ForeshadowLedger


def test_add_and_open():
    L = ForeshadowLedger()
    L.add(1, ["父亲为何自愿离开", "渊核被涂黑"])
    assert len(L.open()) == 2
    assert "父亲为何自愿离开" in L.open_texts()


def test_payoff_exact():
    L = ForeshadowLedger()
    L.add(1, ["父亲为何自愿离开"])
    paid = L.payoff(12, ["父亲为何自愿离开"])
    assert paid == ["fs001"]
    assert L.open() == []


def test_payoff_substring():
    L = ForeshadowLedger()
    L.add(1, ["渊核被涂黑：局里真正要找渊核"])
    paid = L.payoff(7, ["渊核被涂黑"])
    assert paid == ["fs001"]          # 包含匹配


def test_dedupe_add():
    L = ForeshadowLedger()
    L.add(1, ["召唤"])
    L.add(2, ["召唤"])
    assert len(L.open()) == 1


def test_to_prompt_empty_and_nonempty():
    L = ForeshadowLedger()
    assert L.to_prompt() == ""
    L.add(3, ["矿灯编号"])
    p = L.to_prompt()
    assert "未回收伏笔" in p and "矿灯编号" in p


def test_summary_tracks_status():
    L = ForeshadowLedger()
    L.add(1, ["a"])
    L.payoff(9, ["a"])
    s = L.summary()
    assert s[0]["status"] == "payoff" and s[0]["payoff_ep"] == 9
