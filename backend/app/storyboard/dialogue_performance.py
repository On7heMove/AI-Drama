"""对白表演载体生成（2026-08-20，学习抖音对白提示词写法落盘）。

核心原则（学自收藏视频 + MiniMax H3）：
- 不写抽象心情，写具体表演载体：身体动作 + 声音质感 + "接近但未达成"的否定细节
  范式："迟疑片刻，眼睛轻微抬起，但没有真正对视" / "声音接近耳语，喉咙略显干涩。她没有完全抬头"
- 对白单独成句，不混在长场景描述（防被改写）
- 表演载体从 对白文本特征 + 情绪 + 角色关系 派生，非情绪查表

机制：对白特征 → 表演模式模板填充（与 action_detail 同思路：触发→物理链/表演链派生）。
"""
from __future__ import annotations

# ---- 声音质感（情绪 → 声音）----
_VOICE_BY_EMO = {
    "虚弱": "声音接近耳语，喉咙略显干涩",
    "温情": "声音放轻，带着一点温度",
    "孤独": "声音很轻，像对自己说",
    "决然": "声音平稳，没有起伏",
    "试探": "声音带着试探，尾音微微上挑",
    "好奇": "声音带着好奇，语速稍快",
    "惊疑": "声音发紧，带着一点不相信",
    "茫然": "声音发飘，像没回过神来",
    "共鸣": "声音低下去，像终于被人听懂",
    "隐忍": "声音压得很低，几乎听不出情绪",
}
_DEFAULT_VOICE = "声音放缓，气息微沉"

# ---- 身体动作（对白特征 → 动作）----
_BODY_RULES = [
    # 安抚
    (("别怕", "没事", "放心", "别担心", "不要怕"), "伸手轻轻搭上对方肩头"),
    # 警告/威胁
    (("别逼我", "你等着", "你最好", "小心我"), "眯起眼睛，目光变沉"),
    # 激动/难以置信
    (("竟然", "居然", "怎么可能", "怎么会"), "手指收紧，指节微微发白"),
    # 疲惫/放弃
    (("唉", "算了", "罢了", "累了", "随你"), "肩膀慢慢松下来，垂下眼"),
    # 承诺
    (("我保证", "一定", "答应你", "发誓"), "抬起手，掌心向上"),
    # 犹豫/试探
    (("也许", "可能", "要不", "要不要", "或者"), "视线移开，又缓缓转回"),
    # 解释/说明
    (("因为", "所以", "其实", "原来", "也就是说"), "语速放缓，指尖轻点桌面"),
    # 疑问
    (("会怎样", "什么够了", "什么意思"), "视线微抬又落下"),
    (("为什么", "怎么", "谁", "哪里"), "眉头微蹙，眼神带着探究"),
    # 否定/拒绝
    (("没有", "不想", "不要", "不敢"), "手指停在半空，又缓缓收回"),
    # 肯定/确认
    (("知道", "够了", "行", "好", "可以"), "目光直视，不再移开"),
    # 回忆/往事
    (("十年", "一辈子", "一直"), "目光看向远处，像在看很久以前"),
    # 亲人/家
    (("父母", "家", "天穹", "亲人"), "动作慢下来，指尖轻轻收紧"),
    # 站立/勇气/停
    (("站", "勇气", "停"), "身体微微前倾，又克制地坐直"),
]
_DEFAULT_BODY = ""  # 无关键词命中不编造通用微动作（画面内容须来自原文剧情动作 action_blocks）

# ---- 否定细节（"接近但未达成"——灵魂所在）----
_NEG_RULES = [
    (("虚弱", "隐忍", "孤独"), "但没有真正对视"),
    (("孤独", "茫然"), "却没有完全抬头"),
    (("隐忍", "克制"), "却始终没有说出口"),
    (("试探", "迟疑"), "但没有真的问出来"),
]
_DEFAULT_NEG = ""


def voice_quality(emotion: str, dialogue: str = "") -> str:
    for e, v in _VOICE_BY_EMO.items():
        if e in (emotion or ""):
            return v
    return _DEFAULT_VOICE


def body_action(dialogue: str, emotion: str = "") -> str:
    for kws, act in _BODY_RULES:
        if any(k in (dialogue or "") for k in kws):
            return act
    return _DEFAULT_BODY


def negation_detail(emotion: str, dialogue: str = "") -> str:
    for kws, neg in _NEG_RULES:
        if any(k in (emotion or "") for k in kws):
            return neg
    return _DEFAULT_NEG


def build_performance(dialogue: str, emotion: str = "", speaker: str = "") -> str:
    """对白表演载体：身体动作 + 声音质感 + 否定细节。"""
    body = body_action(dialogue, emotion)
    voice = voice_quality(emotion, dialogue)
    neg = negation_detail(emotion, dialogue)
    parts = [body, voice]
    if neg:
        parts.append(neg)
    return "，".join(parts)
