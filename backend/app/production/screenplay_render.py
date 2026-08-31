# -*- coding: utf-8 -*-
"""剧本正文渲染（P1-8）：把结构化 episodes 渲染为可直接人工通读的剧本 Markdown 文本。

统计口径：screenplay_chars 必须基于本模块的渲染结果（与落盘 md 同源），
禁止用 json.dumps(episodes) 的序列化长度冒充剧本字数。
"""
from __future__ import annotations


def render_screenplay(episodes: list[dict]) -> str:
    """渲染剧本正文：集标题 → 场景（地点+时间）→ 动作块（全角括号）→ 对白（角色名：台词）。"""
    out: list[str] = []
    for ep in episodes or []:
        out.append("## 第%s集 %s" % (ep.get("ep", ""), ep.get("title", "")))
        out.append("")
        for sc in ep.get("scenes", []):
            out.append("### %s %s" % (sc.get("location", ""), sc.get("time", "")))
            for a in sc.get("action_blocks", []):
                out.append("（%s）" % a)
            for d in sc.get("dialogues", []):
                out.append("%s：%s" % (d.get("speaker", ""), d.get("line", "")))
        out.append("")
    return "\n".join(out)


def screenplay_chars(episodes: list[dict]) -> int:
    """剧本正文字符数（与实际交付文本一致）。"""
    return len(render_screenplay(episodes))
