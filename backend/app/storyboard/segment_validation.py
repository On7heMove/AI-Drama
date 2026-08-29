"""段级校验（生产管线共用门，2026-08-21 管线整合）。

build_storyboard_prompts 段组装后调用（error 级阻断产出）；stable_regression 回归门复用同一套逻辑——
校验口径只有一份，产线与回归不再各写各的。

校验项（error 级）：
  1. 画面行无 主体/地点 元数据污染、无"主体"占位；站位·轴线行无场景属性/环境词当人物
  2. 画面纯视觉（visual_guard；画面行内嵌对白引语不参与，台词由 P9 单镜层把关）
  3. 段间画面不重复（(画面绑定, 画面行内对白) 完全一致才算重复）
  4. 段内镜头链 (机位, 画面) 键去时间戳后不重复（镜头→画面 1:1）
  5. 段时长门：非旁白 ≤15.5s / 旁白 ≤60.5s（已标【超时】人工排版的段放行）
"""
from __future__ import annotations

import re

from app.storyboard import visual_guard
from app.storyboard.skill_guard import rule_notes

# 场景属性/环境词黑名单：出现在【站位·轴线】行=人物污染（LLM 把"风格：抽象"的"风格"当人物）
_SCENE_WORD_BAN = ("风格", "裂缝", "碎片", "霓虹", "积水", "废墟", "擂台", "光点", "木剑", "心跳", "音效", "灯光")

_MARKS_RX = re.compile(r"^([①②③④⑤⑥⑦⑧⑨⑩]|\[[0-9]+\])")


def _chain_shots(zh: str) -> list[tuple[str, str, str]]:
    """解析【画面】条目 → [(序号, 机位去时间戳, 画面内容)]（1:1 绑定）。"""
    out: list[tuple[str, str, str]] = []
    in_chain = False
    for ln in zh.splitlines():
        if ln.startswith("【画面】"):
            in_chain = True
            continue
        if in_chain:
            if ln.startswith("【"):
                break
            m = re.match(r"^([①②③④⑤⑥⑦⑧⑨⑩]|\[[0-9]+\])\s*(.+?)\s*（(\d+-\d+s)）(｜(.+))?$", ln.strip())
            if m:
                cam = m.group(2).strip().split("｜")[0].strip()
                scene = (m.group(5) or "").strip()
                out.append((m.group(1), cam, scene))
    return out


def _strip_dialogue(text: str) -> str:
    """剔除画面行内嵌对白引语（“...”）：visual_guard 只审计画面描写，不审计台词（台词由 P9 单镜层把关）。"""
    return re.sub(r"[“][^”]*[”]", "", text or "")


def _dialogue_key(zh: str) -> str:
    """段文本内容键：画面行内对白（“...”） + 【旁白】行内容（2026-08-21 修复：旁白段画面内容相同但旁白不同
    不应判重复——旁白文本纳入区分）。"""
    out: list[str] = []
    for ln in zh.splitlines():
        if _MARKS_RX.match(ln.strip()):
            out.extend(m.group(1) for m in re.finditer(r"[“]([^”]+)[”]", ln))
    m = re.search(r"【旁白】“([^”]*)”", zh)
    if m:
        out.append("旁白:" + m.group(1)[:60])
    return "；".join(out)


def validate_video_segments(vids: list[dict]) -> list[dict]:
    """段级校验：返回 [{scene_id, dimension, evidence}]，error 级由调用方阻断。"""
    issues: list[dict] = []
    for it in vids:
        imgs = it.get("image_prompts", []) or []
        sid = it.get("scene_id", "")
        if len(imgs) > 1:
            pairs = []
            for zh in imgs:
                scenes_key = "｜".join(s for _, _, s in _chain_shots(zh))
                pairs.append((scenes_key, _dialogue_key(zh)))
            if len(set(pairs)) < len(pairs):
                _seen: dict = {}
                _dup_desc = ""
                for _i, _p in enumerate(pairs):
                    if _p in _seen:
                        _dup_desc = f"段{_seen[_p] + 1}==段{_i + 1}：{imgs[_i][:80]}"
                        break
                    _seen[_p] = _i
                issues.append({"scene_id": sid, "dimension": "seg_dup",
                               "evidence": f"拆段画面重复（{_dup_desc}）"})
        for zh in imgs:
            seen: set[tuple[str, str]] = set()
            for _mark, _cam, _sc in _chain_shots(zh):
                key = (_cam, _sc)
                if key in seen:
                    issues.append({"scene_id": sid, "dimension": "shot_dup",
                                   "evidence": f"段内镜头链重复: {_cam}"})
                seen.add(key)
                if "｜主体" in _sc or "｜地点" in _sc or _sc.strip() in ("主体", ""):
                    issues.append({"scene_id": sid, "dimension": "pollution",
                                   "evidence": f"画面元数据污染/占位: {_sc[:30]}"})
                for w in visual_guard.audit(_strip_dialogue(_sc)):
                    issues.append({"scene_id": sid, "dimension": "visual_purity",
                                   "evidence": f"{_sc[:30]} → {w}"})
            for ln in zh.splitlines():
                if ln.startswith("【站位·轴线】") and any(b in ln for b in _SCENE_WORD_BAN):
                    issues.append({"scene_id": sid, "dimension": "blocking_pollution",
                                   "evidence": "站位行场景属性词当人物"})
            m = re.search(r"【(\\d+)-(\\d+)秒】", zh)
            if m:
                dur = float(m.group(2)) - float(m.group(1))
                is_vo = "【旁白】" in zh
                limit = 60.5 if is_vo else 15.5
                if dur > limit and "【超时】" not in zh:
                    issues.append({"scene_id": sid, "dimension": "duration",
                                   "evidence": f"段 {dur:.0f}s 超限未标注"})
    issues.extend(rule_notes(vids))  # 技能规则提示（note 级）
    return issues