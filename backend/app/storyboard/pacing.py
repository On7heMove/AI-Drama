"""场景节奏引擎（本地规则，确定性）：由 场景类型 × 情绪张力 × 内容强度 决定单镜时长与镜头数。

节奏标准见 config/storyboard/pacing.json（2026-08-13 建立，回应"场景/情节节奏无标准"问题）：
- 动作/激烈：单镜 1-3s，快切，打击点可升格
- 对话/对峙：3-6s 正反打
- 情绪/压抑/诡异/疏离：6-10s 长镜头，缓推/固定
- 高潮顶点：1-2s 特写
- 张力越高 → 镜头越短越多；张力越低 → 镜头越长越少
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root

PACING_PATH = data_root() / "config" / "storyboard" / "pacing.json"


@lru_cache(maxsize=1)
def load_pacing() -> dict:
    if not PACING_PATH.exists():
        return {"data": {}}
    try:
        return json.loads(PACING_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"data": {}}


class PacingEngine:
    """节奏引擎：输入 SceneScript（含 scene_type/action_blocks/dialogues 情绪），输出每镜时长。"""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or load_pacing().get("data", {})

    # ---------- 场景张力 0..1 ----------
    def scene_intensity(self, scene) -> float:
        emo = " ".join(d.emotion for d in scene.dialogues)
        text = " ".join(scene.action_blocks) + " " + emo
        # 节拍携带逐镜情绪与动作，必须纳入张力判定（否则激烈场景会被漏判）
        for b in getattr(scene, "beats", []) or []:
            text += " " + (b.action or "") + " " + (b.emotion or "")
            if getattr(b, "emotion", ""):
                emo += " " + b.emotion
        emo_map = self.data.get("emotion_intensity", {})
        score = 0.0
        for e in emo_map.get("high", []):
            if e in emo:
                score += 2.0
                break
        for e in emo_map.get("mid", []):
            if e in emo:
                score += 1.0
                break
        for e in emo_map.get("low", []):
            if e in emo:
                score -= 1.5
                break
        score += sum(1.0 for k in self.data.get("content_intensity_high", []) if k in text)
        score -= sum(0.8 for k in self.data.get("content_intensity_low", []) if k in text)
        return max(0.0, min(1.0, score / 4.0))

    def intensity_label(self, scene) -> str:
        s = self.scene_intensity(scene)
        if s >= 0.6:
            return "高张力（快切）"
        if s >= 0.35:
            return "中张力（常规）"
        return "低张力（长镜头）"

    # ---------- 节奏推荐 ----------
    def recommend_rhythm(self, scene, target_sec: float = 15.0, n_shots: int | None = None) -> list[float]:
        """返回每镜时长（和≈target_sec）。高张力 → 短镜多切（末镜作'收'拉长）；低张力 → 长镜渐长。"""
        intensity = self.scene_intensity(scene)
        # 目标单镜均值：高张力≈2.5s，低张力≈7s（线性插值）
        avg = 7.0 - intensity * 4.5
        n = n_shots or max(2, min(8, round(target_sec / avg)))
        if intensity >= 0.6:
            # 激烈：逐镜收紧，末镜作"收"（如镜头拉出/定格）
            weights = [1.0 * (0.85 ** i) for i in range(n)]
            weights[-1] = 2.2 * (0.85 ** (n - 1))
        else:
            # 低张力：渐强/渐缓，末镜最长
            weights = [1.0 + 0.4 * i for i in range(n)]
        total_w = sum(weights)
        lo, hi = self.data.get("clamp", [1, 12])
        durs = [max(lo, min(hi, round(target_sec * w / total_w, 1))) for w in weights]
        diff = target_sec - sum(durs)
        if durs:
            durs[-1] = max(lo, min(hi, round(durs[-1] + diff, 1)))
        return durs

    def apply(self, scene, beats: list, target_sec: float = 15.0) -> list[float]:
        """把节奏时长写回 beats（原地改 duration_sec），返回时长列表。"""
        durs = self.recommend_rhythm(scene, target_sec=target_sec, n_shots=len(beats) if beats else None)
        for b, d in zip(beats, durs):
            b.duration_sec = d
        return durs
