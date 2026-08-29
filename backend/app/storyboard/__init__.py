"""分镜提示词生成模块（v0.1 骨架）。

落点：主流水线 剧本渲染(H) -> 本模块(场景→镜头方案) -> AI生成提示词(I)。
遵循项目原则"模型提议，本地裁决"：
- 本地确定性：场景分类、镜头选择、景别/运动/节奏规则（规则 + 配置包，可单测）；
- LLM 半信任：把镜头方案渲染为自然语言分镜提示词（模板接入后启用）；
- 审核人终裁：风格化分镜可人工调整。

数据底座：config/storyboard/shot_language.json
知识文档：docs/storyboard-shot-language.md
"""
from app.storyboard.scene_classifier import SceneClassifier
from app.storyboard.shot_selector import ShotSelector

__all__ = ["SceneClassifier", "ShotSelector"]
