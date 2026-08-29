"""场景类型分类器（本地规则，确定性）。

按场景文本特征（动作线/情绪/地点）做关键词打分；支持上游/人工直接标注 scene_type。
关键词表为骨架，随书籍/文章学习迭代扩充；同分按 PRIORITY 顺序取（对话兜底）。
"""
from __future__ import annotations

from app.storyboard.schemas import SceneInput

# 分类优先级：同分时的先后（对话兜底）
PRIORITY = ["action", "emotion", "dialogue", "suspense", "reveal", "fantasy", "establishing", "transition"]

KEYWORDS: dict[str, list[str]] = {
    "dialogue": ["说", "道", "问", "答", "谈", "吵", "质问", "谈判", "对峙", "商量", "解释", "坦白", "威胁", "请求", "告诉", "秘密", "耳语", "私语", "演讲", "发言", "绕圈", "僵持", "告白", "争吵"],
    "action": ["打", "斗", "追", "逃", "杀", "冲", "撞", "踢", "砍", "劈", "躲", "袭", "炸", "坠落", "奔跑", "交手", "击中", "闪避", "埋伏", "突袭", "扭打", "缠斗", "倒地", "厮打", "追赶", "飞奔"],
    "emotion": ["哭", "泪", "怒", "惊", "喜", "悲", "绝望", "崩溃", "激动", "拥抱", "亲吻", "颤抖", "心碎", "暧昧", "心动", "依偎", "温情", "悲痛"],
    "suspense": ["悄悄", "缓缓", "暗中", "盯", "潜伏", "陷阱", "危险", "屏息", "阴影", "脚步", "窥视"],
    "reveal": ["发现", "真相", "原来", "竟然", "揭穿", "反转", "暴露", "身份", "秘密", "真面目"],
    "fantasy": ["梦", "回忆", "幻想", "闪回", "前世", "幻境", "幻象", "醒来"],
    "establishing": ["来到", "抵达", "进入", "站在", "清晨", "傍晚", "夜晚", "城市", "宫殿", "房间", "街道", "山", "湖", "城"],
    "transition": ["转眼", "多年后", "第二天", "日出", "日落", "四季", "时光", "岁月"],
}


class SceneClassifier:
    def classify(self, scene: SceneInput) -> str:
        if scene.scene_type:
            return scene.scene_type
        text = " ".join([scene.summary, scene.emotion, scene.location, " ".join(scene.participants)])
        scores = {t: sum(1 for kw in kws if kw in text) for t, kws in KEYWORDS.items()}
        best_score = max(scores.values())
        if best_score == 0:
            return "dialogue"  # 无特征时对话兜底
        candidates = [t for t in PRIORITY if scores[t] == best_score]
        return candidates[0]
