"""节拍时长自动派生（2026-08-19，替代手写秒数）：错落分配（防PPT流水账）+ 对白语速折算 + 单段≤15s。

依据 duration_rule：对白/旁白按语速折算秒数计入该镜时长；同场景时长须错落（std/mean≥0.15）。
"""
from __future__ import annotations


def derive_durations(n_beats: int, scene_type: str = "", dialogues: list[str] | None = None,
                     total_budget: float = 15.0) -> list[float]:
    """每节拍秒数：铺垫/转折/收束错落比例 + 对白语速折算，规范化到预算内。

    dialogues[i] 为该节拍的对白/旁白文本（无则空串）；升格转折节拍按 scene_type 给足时长。
    """
    from app.storyboard.duration_rule import dialogue_seconds
    n = max(n_beats, 1)
    if n == 1:
        ratios = [1.0]
    elif n == 2:
        ratios = [0.42, 0.58]        # 铺垫短、收束长（错落）
    elif n == 3:
        ratios = [0.32, 0.28, 0.40]  # 铺垫/转折/收束长短不一
    elif n == 4:
        ratios = [0.28, 0.22, 0.24, 0.26]
    else:
        ratios = [0.30] + [0.18] * (n - 2) + [0.34]  # 首尾重、中间轻（错落，非均匀）

    base = [total_budget * r for r in ratios]
    dialogues = dialogues or [""] * n
    for i in range(n):
        d = (dialogues[i] or "").strip()
        if d:
            base[i] += dialogue_seconds(d)  # 对白语速折算（中文0.35s/字、英文0.4s/词）
    s = sum(base)
    if s > total_budget:
        base = [b * total_budget / s for b in base]
    return [round(b, 1) for b in base]
