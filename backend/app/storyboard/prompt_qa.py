"""提示词质检机制（产线二必达项量具）：成条提示词确定性校验。

桥接"自然语言提示词 → SD/视频模型"鸿沟的本地规则层：
1. 抽象审美词缺视觉锚点（abstract_to_anchor：命中抽象词但无对应锚点 → 补锚点）
2. 负面通道缺失（negative_defaults：负面提示词为空 → 阻断）
3. 时间轴分段缺失（required_sections.timeline）
4. 合规硬词（blocked_terms：真实人名/公众人物 → 阻断）
5. 镜头语言缺失（camera_vocab / required_sections.camera）
6. 多主体未编号（"主体"无 "主体N" 绑定 → 告警）
7. 光线要素缺失（required_sections.light）

与 visual_guard（画面行内容：声音/比喻/物理）互补；与 sd_manual 的合并待后续。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "storyboard" / "prompt_qa.json"

BLOCKER = "blocker"
WARNING = "warning"


class QAIssue(BaseModel):
    rule_id: str
    severity: str = WARNING  # blocker / warning
    section: str = ""
    message: str = ""
    suggestion: str = ""


class PromptQAReport(BaseModel):
    total: int = 0
    blockers: int = 0
    warnings: int = 0
    passed: bool = True
    issues: list[QAIssue] = Field(default_factory=list)


class PromptQA:
    """确定性提示词质检器。"""

    def __init__(self, config_path: Path | str = CONFIG_PATH) -> None:
        data = json.loads(Path(config_path).read_text(encoding="utf-8")).get("data", {})
        self.abstract_to_anchor: dict = data.get("abstract_to_anchor", {})
        self.negative_defaults: dict = data.get("negative_defaults", {})
        self.blocked_terms: list[str] = data.get("blocked_terms", [])
        self.camera_vocab: list[str] = data.get("camera_vocab", [])
        self.required_sections: dict = data.get("required_sections", {})

    def audit(self, prompt_zh: str, negative: str = "") -> list[QAIssue]:
        out: list[QAIssue] = []
        out.extend(self._check_abstract_anchors(prompt_zh))
        out.extend(self._check_negative(negative))
        out.extend(self._check_timeline(prompt_zh))
        out.extend(self._check_blocked(prompt_zh))
        out.extend(self._check_camera(prompt_zh))
        out.extend(self._check_multi_subject(prompt_zh))
        out.extend(self._check_sections(prompt_zh))
        return out

    def validate(self, prompts: list[dict]) -> PromptQAReport:
        """批量校验：prompts=[{id, prompt_zh, negative}]。passed = 无 blocker。"""
        report = PromptQAReport()
        for p in prompts:
            issues = self.audit(p.get("prompt_zh", ""), p.get("negative", ""))
            report.issues.extend(issues)
            report.total += 1
        report.blockers = sum(1 for i in report.issues if i.severity == BLOCKER)
        report.warnings = len(report.issues) - report.blockers
        report.passed = report.blockers == 0
        return report

    def anchor_suggestions(self, abstract_word: str) -> list[str]:
        """返回抽象词的视觉锚点建议（供渲染/精修补锚点）。"""
        spec = self.abstract_to_anchor.get(abstract_word)
        return spec.get("anchors_zh", []) if spec else []

    def _check_abstract_anchors(self, text: str) -> list[QAIssue]:
        out = []
        for word, spec in self.abstract_to_anchor.items():
            if word in text and not any(a in text for a in spec.get("anchors_zh", [])):
                out.append(
                    QAIssue(
                        rule_id="qa.abstract_without_anchor",
                        section="视觉锚点",
                        message=f"抽象审美词「{word}」未落到可生成性视觉锚点",
                        suggestion="补锚点：" + "、".join(spec.get("anchors_zh", [])),
                    )
                )
        return out

    def _check_negative(self, negative: str) -> list[QAIssue]:
        if not (negative or "").strip():
            return [
                QAIssue(
                    rule_id="qa.negative_missing",
                    severity=BLOCKER,
                    section="负面通道",
                    message="缺少负面提示词通道",
                    suggestion="补默认负面：" + self.negative_defaults.get("zh", ""),
                )
            ]
        return []

    def _check_timeline(self, text: str) -> list[QAIssue]:
        if "秒" not in text:
            return [
                QAIssue(
                    rule_id="qa.timeline_missing",
                    section="时间轴",
                    message="提示词缺少时间轴分段（0-3秒/3-6秒…）",
                    suggestion="按 0-x秒/x-y秒 分段叙事",
                )
            ]
        return []

    def _check_blocked(self, text: str) -> list[QAIssue]:
        hits = [w for w in self.blocked_terms if w in text]
        return [
            QAIssue(
                rule_id="qa.blocked_term",
                severity=BLOCKER,
                section="合规",
                message=f"命中合规硬词：{'、'.join(hits)}",
                suggestion="改用特征化描述（年龄/发型/衣着/气质），禁用真实/公众人名",
            )
            for _ in hits
        ]

    def _check_camera(self, text: str) -> list[QAIssue]:
        keys = self.required_sections.get("camera", {}).get("keywords", [])
        if any(k in text for k in keys) or any(v in text for v in self.camera_vocab):
            return []
        return [
            QAIssue(
                rule_id="qa.camera_missing",
                section="镜头语言",
                message="缺少镜头语言（景别/机位/角度/运动）",
                suggestion="补景别与机位，或直接使用镜头词汇：" + "、".join(self.camera_vocab[:5]),
            )
        ]

    def _check_multi_subject(self, text: str) -> list[QAIssue]:
        if "主体" in text and "主体1" not in text and "主体 1" not in text:
            return [
                QAIssue(
                    rule_id="qa.multi_subject_unbound",
                    section="主体绑定",
                    message="出现「主体」但无 主体1/主体2 编号绑定",
                    suggestion="多主体用 主体1(特征)+主体1动作/情绪 编号绑定，避免属性张冠李戴",
                )
            ]
        return []

    def _check_sections(self, text: str) -> list[QAIssue]:
        out = []
        for sid, spec in self.required_sections.items():
            if sid in ("camera",):
                continue  # 已由 _check_camera 覆盖
            if not any(k in text for k in spec.get("keywords", [])):
                out.append(
                    QAIssue(
                        rule_id=f"qa.section_missing_{sid}",
                        section=spec.get("name", sid),
                        message=f"缺少{sid}要素：{spec.get('name', sid)}",
                        suggestion=spec.get("hint", ""),
                    )
                )
        return out
