"""分镜提示词渲染器（v0.1 占位）。

等待投喂分镜提示词模板后实现模板化渲染：LLM 将 ShotPlan 渲染为自然语言分镜提示词。
当前提供最小直译输出，便于人工检查镜头方案与后续测试。
"""
from __future__ import annotations

from app.storyboard.schemas import ShotPlan


class PromptRenderer:
    def render(self, plan: ShotPlan) -> str:
        lines = [f"【场景 {plan.scene_id or '-'}｜类型 {plan.scene_type}｜方案 {plan.pattern_key}】"]
        for s in plan.shots:
            lines.append(
                f"镜{s.index}｜{s.duration_sec}s｜{s.scale}｜{s.angle}｜"
                f"{s.movement}（{s.stability}）｜{s.content}｜调度：{s.staging or '—'}｜"
                f"声音：{s.sound or '—'}｜色调：{s.tone or '—'}｜剪辑：{s.edit or '—'}｜情绪：{s.emotion or '—'}"
            )
        return "\n".join(lines)
