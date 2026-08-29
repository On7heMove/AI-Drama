"""文本类型本地裁决器（分类闸门，2026-08-20 用户批准落地）。

管线把"文本类型分类（独白/画外音/对白/画面）"完全押在 LLM 一次性软判断上，
本地只有"消费分类结果"的机制（对白→正反打、vo→旁白多角度），没有任何"校验分类本身"的环节
→ LLM 波动即漏。触发案例（e1_s8）：父亲（VO，隔着玻璃）/母亲（VO）被提取成 dialogues，
且"她的智商测试是前百分之一——"在打断处被碎成多片。

本闸门在 parse 后、生成前做确定性校验，error 级阻断；遵循"模型提议，本地裁决"。

规则（2026-08-20 用户拍板固化）：主体人名 + VO/OS/画外音 标记 = 正常对话
（进 dialogues、走正反打、进【对白】，说话人按原文反查清洗为纯人名）；
只有"纯类型标记"说话人（旁白/VO/画外音/内心，无主体人名）才按 vo 处理。
"""
from __future__ import annotations

import re

# 规则1：说话人禁带类型标记（类型标记只允许进 vo）
_SPEAKER_TYPE_MARK = re.compile(r"(VO|画外音|OS|旁白|内心|独白|内心独白)", re.IGNORECASE)
# 规则2：碎句——以冒号/闭括号开头（中间碎片"）：“她的智商测试是前百"）；以"——"结尾且过短（尾部碎片"分之一——"）
_FRAGMENT_OPEN = re.compile(r"^[）)】\]：:]")
_FRAGMENT_TAIL_LEN = 10
# 规则4：系统音/机械音台词（【...】包裹，属 VO 类）不应作为对白
_MECH_MARK = re.compile(r"^【")
# 原文【旁白】标记区（如 "**【旁白（李维安）】** 太极听劲..."）
_PANBAI_SEG = re.compile(r"【旁白[^】]*】")
# VO 对话误归 vo 检测：行首"人物（VO，…）：台词"（在场人物带 VO 的合法对白）
_VO_DIALOGUE_RE = re.compile(
    r"^([^（：:\n]+?)(?:[（(][^）)]*[）)]|(?:OS|VO|画外音|旁白|内心)(?:[（(][^）)]*[）)])?)?\s*[:：]\s*(.+)$")


def _norm(s: str) -> str:
    """归一化：全角/半角括号等价、单双弯引号统一为直引号、去空格与换行（对齐比对用）。"""
    return ((s or "").replace("（", "(").replace("）", ")")
            .replace("’", "'").replace("‘", "'")
            .replace("”", "'").replace("“", "'")
            .replace(" ", "").replace("\n", "").replace("\r", ""))


def _find_vo_speaker(src: str, line_norm: str) -> tuple[str, bool]:
    """在原文中反查该台词前面的说话人标记（"人物（VO，…）："或"人物："），用于 VO 对话误归 vo 拆回。

    取最后一个引语冒号前的一小段，提取结尾说话人（避免 finditer 跨长段误匹配）。
    """
    idx = src.find(line_norm)
    if idx < 0:
        return "", False
    before = src[:idx]
    last = max(before.rfind("："), before.rfind(":"))
    if last < 0:
        return "", False
    head = before[max(0, last - 20):last]  # 只取冒号前一小段，避免吞入上一句台词
    _excl = r"[^：:\n“”\"'‘’（）().。！？…]+"
    m = re.search(r"(" + _excl + r")[（(][^）)]*[）)]$", head)
    if m:
        return m.group(1).strip(), True
    m2 = re.search(r"(" + _excl + r")$", head)
    if m2:
        return m2.group(1).strip(), True
    return "", False


def _split_vo_dialogues(src: str, lines: list[str]) -> tuple[list[dict], bool]:
    """逐行拆分 vo 中误归的对话（规则4 冲突本地优先）：
    - 行首"人物（VO/OS/画外音）：台词" → 新对话（说话人清洗）
    - 无前缀行 → 并入上一对话（台词续行）；首行无前缀 → 按原文反查说话人
    - 机械音【...】→ 不算对话（返回 False，交给 vo 校验）
    """
    parts: list[dict] = []
    for _l in lines:
        _mm = _VO_DIALOGUE_RE.match(_l.strip())
        if _mm:
            sp = _clean_dialogue_speaker(_mm.group(1))
            ln = _mm.group(2).strip()
            if ln.startswith("【"):
                return [], False  # 系统音/机械音：不算对话
            parts.append({"speaker": sp, "line": ln})
        elif parts:
            cur = _l.strip()
            spk, f = _find_vo_speaker(src, _norm(cur)[:30])
            if f and spk and spk != parts[-1]["speaker"]:
                # 反查说话人与上一不同 → 新台词（LLM 去掉了前缀）
                parts.append({"speaker": _clean_dialogue_speaker(spk), "line": cur})
            else:
                parts[-1]["line"] += cur  # 同说话人续行 / 反查失败按续行
        else:
            spk, f = _find_vo_speaker(src, _norm(_l.strip())[:30])
            if not f or not spk:
                return [], False
            parts.append({"speaker": _clean_dialogue_speaker(spk), "line": _l.strip()})
    return parts, True


def _clean_dialogue_speaker(spk: str) -> str:
    """拆回 dialogues 时清洗说话人：去括号（（笃定））与类型标记（OS/VO/画外音/旁白/内心），
    避免"罗伊娜OS（笃定）"污染【对白】行导致 fidelity 拦（原文无此前缀）。"""
    out = re.sub(r"[（(][^）)]*[）)]", "", spk or "")
    out = re.sub(r"(VO|OS|画外音|旁白|内心)", "", out, flags=re.IGNORECASE)
    return out.strip()


def _narration_regions(raw_text: str) -> list[tuple[str, str]]:
    """提取原文【旁白】标记区文本：[(归一化文本, 原文文本)]，用于规则4冲突本地优先与 vo 本地修复。"""
    out: list[tuple[str, str]] = []
    for m in _PANBAI_SEG.finditer(raw_text or ""):
        seg = raw_text[m.end():]
        nxt = re.search(r"【(?!旁白)", seg)
        end = nxt.start() if nxt else len(seg)
        region = seg[:end].split("\n", 1)[0]  # 标记常在行内，取到行尾
        region = region.lstrip("*").strip()  # 去 markdown 粗体标记与空白（"】** 正文"）
        if region:
            out.append((_norm(region), region))
    return out


def validate_classification(script) -> list[dict]:
    """parse 后校验文本类型分类。

    返回 [{severity, scene_id, evidence, suggestion}]；error 级由调用方阻断产出。
    """
    issues: list[dict] = []
    for ep in script.episodes:
        src = _norm(ep.raw_text or "")
        regions = _narration_regions(ep.raw_text or "")
        for s in ep.scenes:
            sid = s.scene_id or f"e{ep.ep}_s?"
            _drop_idx: list[int] = []
            for _di, d in enumerate(s.dialogues or []):
                spk = str(d.get("speaker") or "")
                line = str(d.get("line") or "").strip()
                if _SPEAKER_TYPE_MARK.search(spk):
                    _pure = _clean_dialogue_speaker(spk)
                    if not _pure or not re.search(r"[\u4e00-\u9fffA-Za-z]", _pure):
                        # 纯类型标记说话人（旁白/VO/画外音/内心…）当对白 → 旁白误归对白，error
                        issues.append({"severity": "error", "scene_id": sid,
                                       "evidence": f"说话人为纯类型标记: {spk[:40]}",
                                       "suggestion": "类型标记(VO/画外音/OS/旁白/内心)只允许进 vo，说话人应纯净"})
                    else:
                        # 人名（VO/OS/画外音）：合法 VO 对话（规则4 冲突本地优先）→
                        # 说话人以原文反查为准（修复 LLM 错字/带括号），本地清洗后放行
                        _spk2, _ok = _find_vo_speaker(src, _norm(line)[:30])
                        _fixed = _clean_dialogue_speaker(_spk2 if _ok and _spk2 else spk)
                        if _fixed != spk:
                            d["speaker"] = _fixed
                            issues.append({"severity": "warning", "scene_id": sid,
                                           "evidence": f"VO 对话说话人已按原文本地清洗: {spk[:30]} → {_fixed}",
                                           "suggestion": "人名（VO/OS）为合法 VO 对话，说话人以原文为准（规则4 冲突本地优先）"})
                if _FRAGMENT_OPEN.match(line):
                    issues.append({"severity": "error", "scene_id": sid,
                                   "evidence": f"碎句(以冒号/闭括号开头): {line[:40]}",
                                   "suggestion": "台词必须是完整直接引语，碎片应合并到所属对白"})
                if line.endswith("——") and len(line) < _FRAGMENT_TAIL_LEN:
                    issues.append({"severity": "error", "scene_id": sid,
                                   "evidence": f"碎句(以——结尾且过短): {line[:40]}",
                                   "suggestion": "台词必须是完整直接引语，碎片应合并到所属对白"})
                if _MECH_MARK.match(line):
                    # 系统音/机械音【...】：本地修复（确定性可判，不阻断）——移入 vo（原文子串，进【声音】），从对白移除
                    _vo_add = line.strip()
                    if _vo_add not in (s.vo or ""):
                        s.vo = ((s.vo or "").strip() + "\n" + _vo_add).strip()
                    _drop_idx.append(_di)
                    issues.append({"severity": "warning", "scene_id": sid,
                                   "evidence": f"系统音/机械音台词已移入 vo: {line[:40]}",
                                   "suggestion": "机械音/系统提示音属 VO 类，归 vo（进【声音】），不进正反打"})
                    continue
                ln = _norm(line)
                if ln and any(ln in r[0] for r in regions):
                    issues.append({"severity": "error", "scene_id": sid,
                                   "evidence": f"【旁白】标记区内容被提取为对白: {line[:40]}",
                                   "suggestion": "原文【旁白】区应进 vo，冲突时以本地标记为准"})
            if _drop_idx:
                s.dialogues = [d for _i, d in enumerate(s.dialogues or []) if _i not in _drop_idx]
            vo = str(s.vo or "")
            if vo:
                # 本地裁决（规则4 冲突本地优先）：
                # 0) 2026-08-20 修复：先判定 vo 是否来自原文/【旁白】区（真旁白）——是则直接放行，禁止 _split_vo_dialogues 误拆。
                #    根因：e1_s1 旁白【旁白（李维安）】无冒号，_find_vo_speaker 反查误抓到更早的"风格："/"裂缝碎片："，
                #    把"风格"/"裂缝碎片"拆回 dialogues → 站位"风格在画面左"硬凑轴线。
                vn = _norm(vo)
                joined = "".join(r[0] for r in regions)  # 多旁白区按序拼接（LLM 合并两段旁白时去标记）
                if vn in src or (joined and vn in joined):
                    truncated = next((r[1] for r in regions if len(r[0]) > len(vn) and r[0].startswith(vn)), None)
                    if truncated:
                        s.vo = truncated
                        issues.append({"severity": "warning", "scene_id": sid,
                                       "evidence": f"vo 被 LLM 截断/少提取，已按原文【旁白】区本地修复: {vo[:30]}...",
                                       "suggestion": "旁白以原文【旁白】标记区为准（本地裁决）"})
                    continue  # 原文子串 / 多旁白区拼接 → 放行，不拆回
                # 非旁白区 vo：可能是 OS/VO 台词误归 → 逐行智能拆回 dialogues
                _lines = [l for l in vo.split("\n") if l.strip()]
                _dlgs, _ok = _split_vo_dialogues(src, _lines)
                if _dlgs and _ok and len(_dlgs) >= 2:
                    s.dialogues = list(s.dialogues or []) + _dlgs
                    s.vo = ""
                    issues.append({"severity": "warning", "scene_id": sid,
                                   "evidence": f"VO 对话误归 vo，已按原文逐行拆回 dialogues: {vo[:30]}...",
                                   "suggestion": "在场人物带 VO 的合法对白应归 dialogues（规则4 冲突本地优先）"})
                    continue
                else:
                    prefix = vn[:12]
                    hit = next((r for r in regions if prefix in r[0]), None)
                    if hit and len(hit[0]) > len(vn):
                        s.vo = hit[1]
                        issues.append({"severity": "warning", "scene_id": sid,
                                       "evidence": f"vo 被 LLM 改写，已按原文【旁白】区本地修复: {vo[:30]}...",
                                       "suggestion": "旁白以原文【旁白】标记区为准（本地裁决）"})
                    else:
                        # OS/VO 台词（内心独白/画外音台词）误归 vo（非旁白区）：按原文反查说话人拆回 dialogues
                        _spk, _ok = _find_vo_speaker(src, vn[:30])
                        if _ok and _spk:
                            s.dialogues = list(s.dialogues or []) + [
                                {"speaker": _clean_dialogue_speaker(_spk), "line": vo}]
                            s.vo = ""
                            issues.append({"severity": "warning", "scene_id": sid,
                                           "evidence": f"OS/VO 台词误归 vo，已按原文反查说话人拆回 dialogues: {vo[:30]}...",
                                           "suggestion": "内心独白/画外音台词应归 dialogues（规则4 冲突本地优先）"})
                        else:
                            issues.append({"severity": "error", "scene_id": sid,
                                           "evidence": f"vo 未在原文中找到: {vo[:40]}",
                                           "suggestion": "旁白须逐字原样取自原文【旁白】/（VO/画外音）标记区"})
    return issues
