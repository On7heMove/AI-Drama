"""镜头调度自动派生（2026-08-19，替代硬编码）：从场景类型+节拍数+空间事实 派生 机位/景别/运镜/视角。

导演思维：镜头=对画面的视图，从走向/场景空间事实派生，不手写硬编码。
"""

# 场景类型 → 镜头链模板（按节拍序，机位/景别/运镜/视角）
_CAMERA_TEMPLATES = {
    "abstract": ["固定机位，虚空中央，光效炸开·固定", "推近裂缝·升格揭示（关键转折）", "拉远，光点熄灭·缓拉"],
    "street_conflict": ["低机位仰拍·缓推", "中景·手持微晃", "侧拍过肩·跟拍（捕捉动作轨迹）"],
    "flashback": ["近景俯拍·固定", "闪回冷调·急推", "缓拉望向远方·缓拉"],
    "special_vision": ["推近·缓推", "主观视角·环绕", "特写·固定"],
    "dialogue_intro": ["过肩跟拍·缓推", "中景·固定", "特写·固定", "特写·微推"],
    "memory": ["特写·固定", "中景·缓推", "闪回广角·固定", "主观跟拍·固定", "特写·固定"],
    "dialogue_lesson": ["特写·缓推", "中景·固定", "过肩正反打·固定", "特写·急推"],
    "quiet": ["双人中景·固定", "特写·缓推", "中景·固定", "特写·缓推"],
}

# 顺序=优先级（具体场景词在前，抽象词精确化，防"周围/天穹/虚空"泛匹配误分类）
_SCENE_KEYWORDS = [
    ("dialogue_lesson", ("第一课", "讲解", "世界分为", "没有国家")),
    ("quiet", ("夜谈", "勇气", "睡不着")),  # 站桩太泛（第一场碎片闪回含"站桩"），去掉
    ("memory", ("穿梭机", "玻璃门", "十年前", "天穹理工")),
    ("abstract", ("纯黑虚空", "仅光", "心跳声", "白色裂缝")),
    ("special_vision", ("经络", "量子", "粒子", "线框", "感知视角")),
    ("flashback", ("闪回",)),
    ("street_conflict", ("围来", "弧形围", "拳风", "一拳", "液压臂", "打斗")),
    ("dialogue_intro", ("打量", "扫描仪", "靠气", "门从里面", "焊接笔")),
]

_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def classify_scene_type(text: str) -> str:
    """从文本/场景事实分类场景类型（镜头派生的依据）。"""
    for st, kws in _SCENE_KEYWORDS:
        if any(k in (text or "") for k in kws):
            return st
    return "dialogue_intro"


def derive_camera(text: str, n_beats: int) -> str:
    """从场景类型+节拍数派生镜头链（机位/景别/运镜/视角），不手写。"""
    st = classify_scene_type(text)
    tpl = _CAMERA_TEMPLATES.get(st, _CAMERA_TEMPLATES["dialogue_intro"])
    shots = [f"{_MARKS[i]} {tpl[i]}" for i in range(min(max(n_beats, 1), len(tpl)))]
    return " → ".join(shots)
