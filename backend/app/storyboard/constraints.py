"""分镜提示词·本地约束支撑层（2026-08-14 产品化）。

把 机位必填 / 光线连续 / 画面纯视觉 / 物理合理 / 站位·越轴 / 节奏内容驱动 等标准
做成产品内的本地确定性支撑与校验，遵循"模型提议，本地裁决"：
- prepare_scene：生成前本地准备（节奏填时长 + 站位推理）
- validate_scene：生成前校验场景输入
- validate_shot_prompts：生成后校验最终分镜提示词（中文版）
全部本地运行，不依赖 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.storyboard.blocking import infer_blocking
from app.storyboard.duration_rule import (check_uniformity, dialogue_seconds,
                                          extract_dialogue_en as _extract_dialogue_en,
                                          parse_prompt_duration)
from app.storyboard.motion_guard import audit_motion, audit_sequence
from app.storyboard.pacing import PacingEngine
from app.storyboard.visual_guard import audit as audit_visual
import re as _re


@dataclass
class ConstraintIssue:
    dimension: str       # camera / lighting / visual_purity / physics / blocking / rhythm
    severity: str        # error / warning
    scene_id: str = ""
    shot_index: int = 0
    evidence: str = ""
    suggestion: str = ""


def _has_dialogue(scene) -> bool:
    return bool(getattr(scene, "dialogues", []) or [])


def prepare_scene(scene, viewpoint: str | None = None, target_sec: float = 15.0):
    """生成前本地准备：节拍全缺时长时用 PacingEngine 按张力填充；并推场景级站位表。

    返回 (scene, blocking_table)；blocking_table 供生成提示词时作为【站位·轴线】来源。
    """
    engine = PacingEngine()
    beats = getattr(scene, "beats", []) or []
    if beats and not any(b.duration_sec is not None for b in beats):
        durs = engine.recommend_rhythm(scene, target_sec=target_sec, n_shots=len(beats))
        for b, d in zip(beats, durs):
            b.duration_sec = d
    table = infer_blocking(scene, viewpoint=viewpoint)
    return scene, table


def validate_scene(scene, viewpoint: str | None = None) -> list[ConstraintIssue]:
    """生成前校验：画面纯视觉 / 物理合理 / 站位推理触发 / 节奏时长。"""
    issues: list[ConstraintIssue] = []
    beats = getattr(scene, "beats", []) or []
    texts = list(getattr(scene, "action_blocks", []) or [])
    texts += [b.action or "" for b in beats]
    for i, t in enumerate(texts):
        for w in audit_visual(t):
            issues.append(ConstraintIssue(
                dimension="physics" if "绷断" in w or "物理" in w else "visual_purity",
                severity="error", scene_id=getattr(scene, "scene_id", "") or "", shot_index=i,
                evidence=(t or "")[:40], suggestion=w))
    table = infer_blocking(scene, viewpoint=viewpoint)
    if _has_dialogue(scene) and not table.required:
        issues.append(ConstraintIssue(
            "blocking", "error", getattr(scene, "scene_id", "") or "",
            evidence="有对白但站位推理未触发",
            suggestion="检查 dialogues/participants 是否进入场景"))
    elif table.required and not table.active:
        issues.append(ConstraintIssue(
            "blocking", "error", getattr(scene, "scene_id", "") or "",
            evidence="站位推理无主动方",
            suggestion="提供 participants 或视角人物"))
    if beats and not any(b.duration_sec is not None for b in beats):
        issues.append(ConstraintIssue(
            "rhythm", "warning", getattr(scene, "scene_id", "") or "",
            evidence="节拍全部无时长",
            suggestion="调用 prepare_scene 由 PacingEngine 按张力填充"))
    return issues


def _split_sentences(s: str) -> list[str]:
    """按句末标点/省略号切分（保留标点；容忍原文段落/台词间被【画面】/舞台指示隔开导致的不连续）。"""
    out = []
    for p in _re.split(r"(?<=[。！？…!?])", s or ""):
        p = p.strip()
        if p:
            out.append(p)
    return out


def _fidelity_quotes(line: str) -> list[tuple[str, str]]:
    """从【对白】/【声音·旁白·原文】提取待校验的原文片段，返回 [(类型, 原文片段)]。

    - dialogue：【对白】支持 '；' 连接多条（正反打每组≤2句），逐条去说话人前缀/括号取台词正文。
    - vo：【声音】取 '画外音（旁白·原文）：' 后的首段（尾部是本地增强词如"音乐按情绪起伏"，不在原文，不参与比对）。
    """
    s = line.strip()
    if s.startswith("【对白】"):
        out: list[tuple[str, str]] = []
        for entry in s[len("【对白】"):].split("；"):
            q = _extract_dialogue_en(entry).strip().rstrip("。！？：；，")
            if q:
                out.append(("dialogue", q))
        return out
    if s.startswith("【声音】") and "旁白·原文" in s:
        m = _re.search(r"画外音（旁白·原文）：([^；]+)", s)
        if m:
            return [("vo", m.group(1).strip())]
    return []


def _missing_quotes(quote: str, src: str) -> list[str]:
    """返回未在原文中找到的台词片段。

    优先级：整段命中→放行；整段不中→按句/省略号切分逐段查（容忍"停顿"等非台词间隔被 LLM 抽离），
    仍能拦截整句/整段编造。短于 2 字符的碎片（如单独省略号）不参与比对。
    """
    q = _norm(quote or "")
    if not q or q in src:
        return []
    missing: list[str] = []
    for s in _split_sentences(quote or ""):
        sn = _norm(s).rstrip("。！？：；，")
        if len(sn) >= 2 and sn not in src:
            missing.append(sn)
    return missing


def _norm(s: str) -> str:
    """忠实比对的归一化：全角/半角括号等价、单双弯引号统一为直引号、去空格与换行（原文用半角括号+弯引号）。"""
    return ((s or "").replace("（", "(").replace("）", ")")
            .replace("’", "'").replace("‘", "'")
            .replace("”", "'").replace("“", "'")
            .replace(" ", "").replace("\n", "").replace("\r", ""))


def check_dialogue_fidelity(plans, source_text: str) -> list[ConstraintIssue]:
    """台词忠实原文（本地检验）：对白/旁白句子必须在原文语料中找到，找不到=编造。"""
    issues: list[ConstraintIssue] = []
    src = _norm(source_text or "")
    for sp in plans:
        for idx, zh in enumerate(getattr(sp, "image_prompts", []) or [], 1):
            for ln in zh.splitlines():
                for kind, quote in _fidelity_quotes(ln):
                    for miss in _missing_quotes(quote, src):
                        issues.append(ConstraintIssue(
                            "dialogue_fidelity", "error", sp.scene_id, idx,
                            evidence=f"台词/旁白未在原文中找到：{miss[:40]}",
                            suggestion="对白只保留原文直接引语，旁白只取原文原句；英文按原意忠实翻译"))
    return issues


def validate_shot_prompts(plans) -> list[ConstraintIssue]:
    """生成后校验最终分镜提示词（中文版）：机位必填 / 光线必填 / 站位·轴线 / 画面纯视觉。"""
    issues: list[ConstraintIssue] = []
    for sp in plans:
        for idx, zh in enumerate(getattr(sp, "image_prompts", []) or [], 1):
            lines = zh.splitlines()
            join = "\n".join(lines)
            if "机位：" not in join or "机位：—" in join:
                issues.append(ConstraintIssue("camera", "error", sp.scene_id, idx,
                                             evidence="缺少机位", suggestion="机位必填（摄影机位置/朝向）"))
            if "【光线】" not in join:
                issues.append(ConstraintIssue("lighting", "error", sp.scene_id, idx,
                                             evidence="缺少光线", suggestion="光线必填/继承场景基准"))
            if "【站位·轴线】" not in join:
                issues.append(ConstraintIssue("blocking", "warning", sp.scene_id, idx,
                                             evidence="缺站位·轴线", suggestion="站位约束必填（可走推理/默认防越轴）"))
            for ln in lines:
                if ln.startswith("【画面】"):
                    for w in audit_visual(ln):
                        issues.append(ConstraintIssue("visual_purity", "error", sp.scene_id, idx,
                                                     evidence=ln[:40], suggestion=w))
            for w in audit_motion(join):
                issues.append(ConstraintIssue("motion", "error", sp.scene_id, idx,
                                             evidence=f"镜头{idx} 活性", suggestion=w))
            if "【对白】" in join:
                _ds = _dialogue_staging_check(zh)
                if _ds:
                    issues.append(ConstraintIssue("dialogue_staging", "error", sp.scene_id, idx,
                                                 evidence=_ds,
                                                 suggestion="对白镜必须有调度：说话人动作/微动作 + 有动机运镜（静态仅当表演能撑住且调度有明确动作），禁止静态PPT"))
            need = dialogue_seconds(_extract_dialogue(zh)) + _extract_vo(zh)
            if need > 0 and parse_prompt_duration(zh) < need - 0.01:
                issues.append(ConstraintIssue("duration", "error", sp.scene_id, idx,
                                             evidence=f"镜头{idx} 台词/旁白需≈{need:.1f}s，实际≈{parse_prompt_duration(zh):.1f}s",
                                             suggestion="该镜时长须包含台词/旁白时间（视觉+对白+旁白）"))
    for w in audit_sequence(plans):
        issues.append(ConstraintIssue("motion_sequence", "error", evidence=w,
                                     suggestion="打散固定镜：穿插运镜/换景别，或缩短静止时长"))
    _by_scene: dict[str, list[float]] = {}
    for sp in plans:
        _by_scene[sp.scene_id] = [parse_prompt_duration(zh) for zh in getattr(sp, "image_prompts", []) or []]
    for sid, durs in _by_scene.items():
        for w in check_uniformity(durs, sid):
            issues.append(ConstraintIssue("duration_uniform", "warning", evidence=w,
                                         suggestion="时长按内容张力错落（对话/爆点/收束长短不一）"))
    return issues


def _dialogue_staging_check(zh: str) -> str:
    """对白镜调度校验：有对白必须有【调度】；静态机位必须靠说话人表演（调度或画面含动作信号）撑住。"""
    from app.storyboard.dialogue_staging import has_speaker_action as _has_action
    cam = next((l for l in zh.splitlines() if l.startswith("【镜头】")), "")
    staging = next((l for l in zh.splitlines() if l.startswith("【调度】")), "")
    visual = "\n".join(l for l in zh.splitlines() if l.startswith("【画面】"))
    if not staging:
        return "有对白但缺【调度】"
    if any(s in cam for s in ("固定", "固定机位", "locked", "static")):
        if not (_has_action(staging) or _has_action(visual)):
            return "对白镜静态且无说话人动作/微动作（像PPT）"
    return ""


def _extract_dialogue(zh: str) -> str:
    for ln in zh.splitlines():
        if ln.startswith("【对白】"):
            return ln[len("【对白】"):].strip()
    return ""


def _extract_vo(zh: str) -> float:
    import re
    for ln in zh.splitlines():
        if ln.startswith("【声音】") and "旁白·原文" in ln:
            m = re.search(r"画外音（旁白·原文）：([^；]+)", ln)
            if m:
                from app.storyboard.duration_rule import vo_seconds
                return vo_seconds(m.group(1))
    return 0.0


def validate_shot_prompts_fidelity(plans, source_text: str) -> list[ConstraintIssue]:
    """台词忠实原文：本地校验对白/旁白都在原文语料中。"""
    return check_dialogue_fidelity(plans, source_text)


def check_segment_dialogue_fidelity(zh_blocks: list[str], source_text: str) -> list[str]:
    """段级台词忠实（场景连续段格式）：【对白】按 '；' 拆单条，逐条只取英文台词正文比对原文语料。"""
    issues: list[str] = []
    src = _norm(source_text or "")
    for idx, zh in enumerate(zh_blocks, 1):
        for ln in zh.splitlines():
            if not ln.startswith("【对白】"):
                continue
            body = ln[len("【对白】"):].strip()
            for entry in body.split("；"):
                q = _norm(_extract_dialogue_en(entry)).rstrip("。！？：；，")
                if q and q not in src:
                    issues.append(f"段{idx}: 台词未在原文中找到：{entry[:40]}")
    return issues
