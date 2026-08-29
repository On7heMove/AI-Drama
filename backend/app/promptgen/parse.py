"""剧本解析：剧本文本 → ParsedScript（分集切分 + LLM 结构化提取）。

- 本地：优先按「第X集/第X话」分集（splitter 只支持 章回节卷部，这里补集/话）；无集标题回退 split_chapters
- LLM：提取每集 爆点/钩子/情绪曲线/场景，以及全局角色清单（忠实原文，不编造）
"""
from __future__ import annotations

import json
import logging
import re

from app.config import settings
from app.library.splitter import split_chapters
from app.production.llm_client import DeepSeekClient
from app.storyboard.classification_gate import _clean_dialogue_speaker  # 复用：beats 对话事件说话人清洗
from app.storyboard.duration_rule import strip_parens  # 复用：去括号（OS 中文注释）
from app.promptgen.schemas import (
    ParsedCharacter,
    ParsedEpisode,
    ParsedScene,
    ParsedScript,
)

_log = logging.getLogger(__name__)

_EPISODE_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:第[0-9零一二三四五六七八九十百千万]+[集话卷章回节部]|[0-9]{1,3}\s*[集话])(?:\s|：|:|$)"
)

_EPISODE_SYSTEM = """你是短剧剧本结构化提取器。把用户给定的一集剧本文本，提取为 JSON。
要求：
1. 只提取原文明确写出的内容，禁止编造（外貌/服装/地点/动作/台词若原文未写则留空）。
2. scenes 数组：每个场景含 location(地点)、interior(内/外)、era(时代)、time(日/夜/晨/昏)、lighting(光线基准)、atmosphere(氛围)、participants(事件参与者数组：从本场景动作/场面描写中找出有动作或被明确描写的人物，忠实原文收录；场景属性/环境成分不收录；群体编号统一为群体名，如 贵族1/贵族2→贵族；同一角色统一名字；无则空数组)、spatial(空间关系数组：忠实原文的位置/朝向信息，[{"character":"角色名","position":"位置描述","facing":"朝向"}]；原文没写的位置/朝向就不填对应字段；无则空数组)、lighting_arc(光线变化数组：只收原文明确写了光线变化的事件，[{"at":"触发事件","change":"变化增量"}]；change 只写可渲染物理描述，禁止抽象词/编造；无则空数组)、sound(环境底：本场景环境音底，忠实原文，无则空字符串)、negative(负面提示词字符串数组：必须是负面词汇（名词/形容词，如 塑料质感、廉价、轻飘飘、CG感、穿模、变形、模糊、僵硬、粗糙），禁止“不要X/避免X/要Y”的句子式表述（那是正面要求的否定式，AI 会反向理解）；按“本片题材+本场景类型+本场景实际内容”三层一次给出，去重、宁缺毋滥、无则空数组)、props(关联物品字符串数组：这场戏原文写到、可被镜头拍到的关键物品/道具/视觉焦点；环境成分不算；无则空数组)、character_states(角色状态数组：忠实原文提取状态/外观/伤势/着装变化，[{"character":"角色名","state":"状态/外观/伤势/着装变化原文描写"}]，禁止编造、无则空数组)。
3. 每个场景含 segments 数组：LLM 按【叙事单元】切段（封爵/刺杀/背叛/濒死等），每段一个连续叙事单元，段间有叙事推进或情绪变化；每段含 emotion(段情绪，参考已知情绪表：庄重/怨毒/狠厉/恶毒/虚弱/震惊/决然/冷酷/温柔/警惕/专注/安宁/戒备/紧张/狂乱/急切/压抑 等；随段内容变化，禁止全场景同词；如叙事更贴切可用表外词，LLM 给什么原样保留，不要清空/改写)、sound(本段声音：该段内容相关的声音，禁止把整场环境音全部塞进每段；无则空字符串)、blocking(该段可执行的轴线约束：谁在画面左/右、谁对谁；必须写"轴线=X→Y"或含左右/朝向/背后/面对等约束；并写禁止左右互换、禁止越轴跳切；背后攻击写"背后方向，非面对面"；只写该段画面实际出现的角色；禁止叙事/评价词（如"轴线混乱""独立性空"等无意义词）；无则空字符串)、beats(有序事件数组)。
4. beats 内事件三种：
- action beat：{"type":"action","text":"完整画面描述：主体+动作+结果，必填；写该镜角色的当前状态（面色惨白/嘴角溢血/华服变黑袍/狼耳琥珀瞳等，状态随剧情变化，忠实原文，参考 character_states）；禁只有主体名，禁泛化代称【他/她/其/他们/此人】，必须写具体角色名","subject":"画面主体，必填，具体角色名，禁泛化代称","focus":"主体动作/关系过程/细节/反应情绪/环境 选最接近一个，必填","motion":"可选，主要运动倾向，只能从枚举选：跟拍/急推/缓推/固定/横移/手持/穿越机/航拍/环绕/摇/升降，不自由写","slow_mo":"可选，该镜升格秒数（如 3）；非升格镜不给；无则省略"}；粒度=一镜一完整动作节拍；因果链合并（贯穿→血涌→僵住为一个节拍），节拍分开（冲出/凝剑/贯穿为三个节拍）；同一事件只写一条，禁止拆碎片/重叠/复制。
- dialogue beat：{"type":"dialogue","speaker":"说话人","line":"台词逐字原样","text":"可选：只写说话人当前状态/表情（面色惨白/神情亢奋/怒目圆睁）；禁止写动作（拔出/仰头/大喊/扬起等动作词不写）；动作在 action beat","pivot":false,"reactor":[],"is_os":false,"reaction":"该句后听者反应画面，如 罗伊娜瞳孔骤缩；无则省略"}
- vo beat：{"type":"vo","text":"旁白原文","speaker":"该旁白/OS 的说话人（内心独白的主人，从本场景参与者里选，必填）","visual":"可选：该旁白镜画面内容，如 罗伊娜站在大厅中央；无则省略"}
5. 只输出 JSON，格式示例：
{"title":"","explosion":"","hook_open":"","hook_end":"","emotional_curve":[],"scenes":[{"location":"中央教堂","interior":"内","era":"中世纪","time":"日","lighting":"日光","atmosphere":"庄重","participants":["珍妮芙","罗伊娜","主礼人"],"spatial":[],"lighting_arc":[],"sound":"教堂环境底","negative":["塑料质感","廉价","轻飘飘","CG感","穿模","僵硬"],"props":["漆黑长剑"],"character_states":[{"character":"罗伊娜","state":"白裙染血"}],"segments":[{"emotion":"庄重","sound":"人群低声","blocking":"主礼人在画面左、罗伊娜与伊索尔德在画面右，面对面；轴线=主礼人→罗伊娜；禁止左右互换、禁止越轴跳切","beats":[{"type":"action","text":"主礼人托起长剑，宣布封爵","subject":"主礼人","focus":"主体动作","motion":"固定"}]},{"emotion":"狠厉","sound":"剑鸣","blocking":"珍妮芙在罗伊娜身后（背后方向，非面对面）；轴线=珍妮芙→罗伊娜；禁止左右互换、禁止越轴跳切","beats":[{"type":"action","text":"珍妮芙掌心黑气凝出漆黑长剑","subject":"珍妮芙","focus":"细节","motion":"急推"},{"type":"action","text":"珍妮芙冲到罗伊娜身后，一剑贯穿罗伊娜身体，鲜血涌出","subject":"珍妮芙","focus":"主体动作","motion":"跟拍","slow_mo":3},{"type":"dialogue","speaker":"珍妮芙","line":"Die!","text":"珍妮芙面色狠厉","pivot":true,"reactor":["罗伊娜"],"is_os":false},{"type":"action","text":"罗伊娜身体僵住，鲜血从伤口涌出","subject":"罗伊娜","focus":"反应情绪","motion":"固定"},{"type":"vo","text":"旁白原文","speaker":"罗伊娜"}]}]}]}
"""

_CHARACTER_SYSTEM = (
    "你是短剧角色设定提取器。从整部剧本文本中提取所有主要角色，输出 JSON。\n"
    "要求：\n"
    "1. 只提取原文写出的设定，禁止编造；外貌/服装/气质未写则留空字符串。\n"
    "2. 字段：name(角色名)、role(主角/反派/配角)、gender(性别)、age(年龄描述)、"
    "appearance(外貌：发型/脸型/体型)、outfit(服装)、temperament(气质/性格)、source(原文一句依据)。\n"
    "3. 只输出 JSON：{\"characters\":[{\"name\":\"\",\"role\":\"\",\"gender\":\"\",\"age\":\"\","
    "\"appearance\":\"\",\"outfit\":\"\",\"temperament\":\"\",\"source\":\"\"}]}"
)


_PANBAI_SPEAKER_RE = re.compile(r"【旁白[（(]([^）)]+)[）)]】")
_SOUND_EFFECT_RE = re.compile(r"【音效[^】]*】")


def _extract_sound_effects(seg: str) -> str:
    """本地兜底：从原文【音效】标记区提取环境音/音效（LLM sound_effects 为空时才使用）。"""
    out: list[str] = []
    for m in _SOUND_EFFECT_RE.finditer(seg or ""):
        nxt = re.search(r"【(?!音效)", seg[m.end():])
        end = m.end() + (nxt.start() if nxt else len(seg) - m.end())
        region = seg[m.end():end].split("\n", 1)[0].lstrip("*").strip().rstrip("】").strip()
        if region:
            out.append(region)
    return "；".join(out)
_SCENE_HEAD_RE = re.compile(
    r"^#{2,4}\s*第?[0-9一二三四五六七八九十百]+场"       # 测试文档：### 第一场
    r"|^\d+-\d+\s*[日夜晚晨昏][ ]*[内]?[外]?"        # 暴君狼王 word：1-1日 内 …
    , re.M)


def _scene_raw_segments(raw_text: str) -> list[str]:
    """按原文场景标题（### 第X场）切分，返回各场景段文本（与 ep.scenes 顺序对齐）。"""
    hits = [m.start() for m in _SCENE_HEAD_RE.finditer(raw_text or "")]
    if not hits:
        return [raw_text or ""]
    out = []
    for i, st in enumerate(hits):
        en = hits[i + 1] if i + 1 < len(hits) else len(raw_text)
        out.append(raw_text[st:en])
    return out


_DELTA_ACTION_RE = re.compile(r"^\s*△\s*(.+)$")


def _extract_delta_actions(seg: str) -> list[str]:
    """从原文场景段提取 △ 动作行（剧本动作描写标记，逐句原样）。"""
    out: list[str] = []
    for ln in (seg or "").splitlines():
        m = _DELTA_ACTION_RE.match(ln)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


# 表现重点机制（2026-08-21）：focus 只允许这五个枚举，不在枚举宁缺毋滥
_FOCUS_ENUM = {"主体动作", "关系过程", "细节", "反应情绪", "环境"}
_FOCUS_MOTION_SPLIT_RE = re.compile(r"[/、,，;；]")



_OS_MARK_RE = re.compile(r"([^：:，。、△（）()\s]+?)(?:OS|VO|画外音|内心独白)[（(]?[^：:]*[）)]?\s*[:：]\s*$")


def _norm_name(s: str) -> str:
    """角色名机械清洗（问题1/硬编码修正）：仅去空白，不做名字映射/编号归一。"""
    return (s or "").strip()



def _norm_raw(s: str) -> str:
    return (s or "").replace(" ", "").replace("\n", "").replace("\r", "")


def _as_str_list(value) -> list[str]:
    """把 LLM 可能返回的字符串/字符串数组统一为去空白后的字符串数组（不在此做去重/可信名过滤）。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for x in value or []:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _clean_string_list(values) -> list[str]:
    """字符串数组清洗：去空、去重、保序（props/reactor 通用）。"""
    out: list[str] = []
    for x in values or []:
        if isinstance(x, str):
            x = x.strip()
            if x and x not in out:
                out.append(x)
    return out


def _clean_negative(value) -> list[str]:
    """LLM negative 本地清洗：去空、去重、保序（2026-08-21 攒批）。"""
    return _clean_string_list(_as_str_list(value))


def _clean_emotion(value) -> str:
    """LLM emotion 原样直通（本地零内容决策：不清空/不校验，LLM 给什么保留什么）。"""
    return str(value or "").strip()


def _clean_action_beat(ev: dict) -> dict | None:
    """segments action beat 清洗：focus 枚举、motion 去空去重、text/subject 去空（本地不处理代词）。"""
    ev = _clean_focus_meta(dict(ev))
    text = str(ev.get("text") or "").strip()
    subject = str(ev.get("subject") or "").strip()
    ev["text"] = text
    ev["subject"] = subject
    if not text:
        return None
    return ev


def _clean_dialogue_beat(ev: dict) -> dict | None:
    """segments dialogue beat 清洗：line 去空、speaker 复用 classification_gate 清洗、pivot 布尔化、reactor 去空去重。"""
    line = str(ev.get("line") or "").strip()
    if not line:
        return None
    ev = dict(ev)
    raw_spk = str(ev.get("speaker") or "")
    if re.search(r"OS|VO|画外音|内心", raw_spk):
        ev["is_os"] = True
    ev.update({
        "type": "dialogue",
        "speaker": _clean_dialogue_speaker(raw_spk),
        "line": line,
        "text": str(ev.get("text") or "").strip(),
        "pivot": bool(ev.get("pivot")),
        "reactor": _clean_string_list(_as_str_list(ev.get("reactor"))),
        "reaction": str(ev.get("reaction") or "").strip(),
        "is_os": bool(ev.get("is_os")),
    })
    return ev


def _clean_vo_beat(ev: dict) -> dict | None:
    """segments vo beat 清洗：旁白原文去空（逐字原样）；speaker/visual 为 LLM 可选字段，本地原样保留。"""
    text = str(ev.get("text") or "").strip()
    if not text:
        return None
    return {
        "type": "vo",
        "text": text,
        "speaker": str(ev.get("speaker") or "").strip(),
        "visual": str(ev.get("visual") or "").strip(),
    }


def _clean_segment_beats(raw_beats: list) -> list[dict]:
    """segments beats 清洗：只保留 action/dialogue/vo 三类，去空、保序。"""
    out: list[dict] = []
    for ev in raw_beats or []:
        if not isinstance(ev, dict):
            continue
        typ = str(ev.get("type") or "").strip()
        if typ == "action":
            cleaned = _clean_action_beat(ev)
        elif typ == "dialogue":
            cleaned = _clean_dialogue_beat(ev)
        elif typ == "vo":
            cleaned = _clean_vo_beat(ev)
        else:
            continue
        if cleaned:
            out.append(cleaned)
    return out


def _clean_segments(raw_segments: list) -> list[dict]:
    """segments 本地清洗：去空、去重、保序；每段 emotion/sound/beats 清洗。"""
    out: list[dict] = []
    seen: set[str] = set()
    for seg in raw_segments or []:
        if not isinstance(seg, dict):
            continue
        emotion = _clean_emotion(seg.get("emotion"))
        sound = _clean_sound_effects(seg.get("sound"))
        blocking = str(seg.get("blocking") or "").strip()
        beats = _clean_segment_beats(seg.get("beats") or [])
        if not emotion and not sound and not blocking and not beats:
            continue
        cleaned = {"emotion": emotion, "sound": sound, "blocking": blocking, "beats": beats}
        key = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _flatten_segments_to_beats(segments: list[dict]) -> list[dict]:
    """兼容期：把 segments 内 beats 展平为 ParsedScene.beats（segments 是主结构）。"""
    out: list[dict] = []
    for seg in segments or []:
        for b in seg.get("beats") or []:
            if isinstance(b, dict):
                out.append(dict(b))
    return out


def _clean_sound_effects(value) -> str:
    """LLM sound_effects 本地清洗：字符串去空；若带常见分隔符则去重保序后重新拼接。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [str(x).strip() for x in value if str(x).strip()]
    else:
        text = str(value).replace("\n", "；").strip()
        parts = [p.strip() for p in re.split(r"[；;，,、]", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return "；".join(out)


def _clean_focus_meta(ev: dict, trust: set[str] | None = None) -> dict:
    """focus/subject/motion 本地清洗（2026-08-21）：
    - focus 只保留枚举内值，不在枚举置空（宁缺毋滥）
    - subject 去空；提供可信名集合时，不在可信名内也置空（防 LLM 编造画面主体）
    - motion 去空、按分隔符拆词去重、保序
    """
    ev = dict(ev)
    _focus = str(ev.get("focus") or "").strip()
    ev["focus"] = _focus if _focus in _FOCUS_ENUM else ""
    if "subject" in ev or ev.get("type") == "action":
        _subject = str(ev.get("subject") or "").strip()
        if trust is not None and _subject and _subject not in trust:
            _subject = ""
        ev["subject"] = _subject
    _motion_raw = str(ev.get("motion") or "")
    _motion_parts: list[str] = []
    for _part in _FOCUS_MOTION_SPLIT_RE.split(_motion_raw):
        _part = _part.strip()
        if _part and _part not in _motion_parts:
            _motion_parts.append(_part)
    ev["motion"] = "、".join(_motion_parts)
    return ev


def _dedup_beats(script) -> None:
    """beats/dialogues 事件去重（2026-08-21 管线级）：LLM 重复提取同一事件（如同一 OS 两次）
    → 生成完全相同的段 → 段级门 seg_dup 拦截。按 (type, speaker, line/text) 保序去重。"""
    for ep in script.episodes:
        for s in ep.scenes:
            if s.dialogues:
                _seen: set = set()
                _out: list = []
                for d in s.dialogues:
                    _k = ("dlg", str(d.get("speaker") or ""), _norm_raw(str(d.get("line") or "")))
                    if _k in _seen:
                        continue
                    _seen.add(_k)
                    _out.append(d)
                s.dialogues = _out
            if s.beats:
                _seen = set()
                _out = []
                for b in s.beats:
                    _k = (str(b.get("type") or ""), str(b.get("speaker") or ""),
                          _norm_raw(str(b.get("text") or b.get("line") or "")))
                    if _k in _seen:
                        continue
                    _seen.add(_k)
                    _out.append(b)
                s.beats = _out


def _backfill_os_dialogues(script) -> None:
    """对白/beats 台词反查原文 OS 标记（2026-08-21）：LLM 提取时可能丢掉"罗伊娜OS："标记（speaker=罗伊娜），
    按原文反查补 is_os——OS 必须标注"不开口"，不能当成开口对白。"""
    for ep in script.episodes:
        raw_norm = _norm_raw(ep.raw_text)
        for s in ep.scenes:
            for d in s.dialogues or []:
                if d.get("is_os"):
                    continue
                _ln = _norm_raw(str(d.get("line") or ""))[:40]
                _idx = raw_norm.find(_ln) if _ln else -1
                if _idx > 0 and _OS_MARK_RE.search(raw_norm[max(0, _idx - 40):_idx]):
                    d["is_os"] = True
            for b in s.beats or []:
                if b.get("type") == "dialogue" and not b.get("is_os"):
                    _ln = _norm_raw(str(b.get("line") or ""))[:40]
                    _idx = raw_norm.find(_ln) if _ln else -1
                    if _idx > 0 and _OS_MARK_RE.search(raw_norm[max(0, _idx - 40):_idx]):
                        b["is_os"] = True


def _sanitize_dialogue_metadata(script) -> None:
    """dialogue 元数据本地裁决（2026-08-21 反应镜配套）：
    - pivot 布尔化（缺省 False）
    - reactor 只保留可信名（角色表 / participants / 对白说话人），去空去重保序，防 LLM 编造反应者
    - props 去空去重保序（parse_script 已清洗，此处兜底）"""
    char_names = {_norm_name(c.name) for c in script.characters if c.name}
    for ep in script.episodes:
        for s in ep.scenes:
            trust = set(char_names)
            trust.update(_norm_name(p) for p in s.participants)
            for d in s.dialogues or []:
                if d.get("speaker"):
                    trust.add(_norm_name(str(d["speaker"])))
            for b in s.beats or []:
                if b.get("type") == "dialogue" and b.get("speaker"):
                    trust.add(_norm_name(str(b["speaker"])))
            s.props = _clean_string_list(s.props)
            for d in s.dialogues or []:
                d["pivot"] = bool(d.get("pivot"))
                cleaned: list[str] = []
                for r in _as_str_list(d.get("reactor")):
                    r = _norm_name(r)
                    if r in trust and r not in cleaned:
                        cleaned.append(r)
                d["reactor"] = cleaned


def _sanitize_focus_metadata(script) -> None:
    """focus 主体可信名过滤（2026-08-21）：
    subject 只保留角色表/participants/对白说话人中的可信名（角色名仅机械去空白）。"""
    char_names = {_norm_name(c.name) for c in script.characters if c.name}
    for ep in script.episodes:
        for s in ep.scenes:
            trust = set(char_names)
            trust.update(_norm_name(p) for p in s.participants)
            for d in s.dialogues or []:
                if d.get("speaker"):
                    trust.add(_norm_name(str(d["speaker"])))
            for b in s.beats or []:
                if b.get("type") == "dialogue" and b.get("speaker"):
                    trust.add(_norm_name(str(b["speaker"])))
            for i, b in enumerate(s.beats or []):
                if isinstance(b, dict) and b.get("type") == "action":
                    s.beats[i] = _clean_focus_meta(b, trust=trust)


def _clean_spatial(raw: list) -> list[dict]:
    """spatial 本地清洗（批次B/问题1）：完全听 LLM，character 仅去空白；position/facing 去空；去重保序。"""
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        char = _norm_name(str(x.get("character") or "").strip())
        if not char:
            continue
        position = str(x.get("position") or "").strip()
        facing = str(x.get("facing") or "").strip()
        key = (char, position, facing)
        if key in seen:
            continue
        seen.add(key)
        item = {"character": char}
        if position:
            item["position"] = position
        if facing:
            item["facing"] = facing
        out.append(item)
    return out


def _clean_lighting_arc(raw: list) -> list[dict]:
    """lighting_arc 本地清洗（批次B）：change 空剔除、同事件同变化去重、保序。"""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        at = str(x.get("at") or "").strip()
        change = str(x.get("change") or "").strip()
        if not at or not change:
            continue
        key = (_norm_raw(at), _norm_raw(change))
        if key in seen:
            continue
        seen.add(key)
        out.append({"at": at, "change": change})
    return out


def _clean_character_states(raw: list) -> list[dict]:
    """character_states 本地清洗（问题3）：去空、同角色同状态去重、角色名仅去空白。"""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        char = _norm_name(str(x.get("character") or "").strip())
        state = str(x.get("state") or "").strip()
        if not char or not state:
            continue
        key = (char, _norm_raw(state))
        if key in seen:
            continue
        seen.add(key)
        out.append({"character": char, "state": state})
    return out


def _sanitize_spatial_lighting(script) -> None:
    """spatial/lighting_arc 统一本地清洗（批次B/问题1）：spatial character 完全听 LLM、只机械清洗；lighting_arc 去空去重。"""
    for ep in script.episodes:
        for s in ep.scenes:
            s.spatial = _clean_spatial(s.spatial)
            s.lighting_arc = _clean_lighting_arc(s.lighting_arc)


def _sanitize_segments(script) -> None:
    """segments 本地格式清洗：只做 focus/motion/pivot/reactor 机械整理。
    本地零内容决策：不按可信名过滤 subject/reactor，不处理代词，不删 LLM 内容。
    """
    for ep in script.episodes:
        for s in ep.scenes:
            for seg in s.segments or []:
                if not isinstance(seg, dict):
                    continue
                for i, b in enumerate(seg.get("beats") or []):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "action":
                        seg["beats"][i] = _clean_focus_meta(b)
                    elif b.get("type") == "dialogue":
                        b["pivot"] = bool(b.get("pivot"))
                        b["reactor"] = _as_str_list(b.get("reactor"))


def _local_adjudicate(script) -> None:
    """parse 后本地裁决统一入口（2026-08-21 管线整合，模型提议本地裁决）：
    ① participants 机械清洗（_sanitize_participants：去空/去重，完全听 LLM）
    ② beats 动作兜底（_backfill_action_beats，刺杀等关键动作从原文 △ 行补齐）
    ③ dialogue 元数据清洗（_sanitize_dialogue_metadata，pivot/reactor/props）
    文本类型闸门（classification_gate）在 pipeline 层 parse 后调用，属同一裁决层。"""
    _sanitize_participants(script)
    _backfill_action_beats(script)
    _backfill_os_dialogues(script)
    _dedup_beats(script)
    _sanitize_dialogue_metadata(script)
    _sanitize_focus_metadata(script)
    _sanitize_segments(script)
    _sanitize_spatial_lighting(script)


def _backfill_action_beats(script) -> None:
    """beats 动作事件本地兜底（2026-08-21 刺杀丢失根因）：
    LLM 提取遗漏动作序列（如 冲→贯穿→扭转→惨叫→抛起）时，从原文场景段 △ 动作行补齐 action 事件，
    按原文顺序插入到对应位置前——关键剧情动作不依赖 LLM 是否完整提取。"""
    def _n(s: str) -> str:
        return (s or "").replace(" ", "").replace("\n", "").replace("\r", "")

    for ep in script.episodes:
        segs = _scene_raw_segments(ep.raw_text or "")
        for i, s in enumerate(ep.scenes):
            seg = segs[i] if i < len(segs) else (ep.raw_text or "")
            deltas = _extract_delta_actions(seg)
            if not deltas:
                continue
            beats = list(s.beats or [])
            have = {_n(b.get("text", "")) for b in beats if b.get("type") == "action"}
            for d in deltas:
                clean = _scrub_visual_text(d)  # 插入前过清洗（比喻/感知动词剔除，拟声/角色发声保留）
                if not clean or _n(clean) in have:
                    continue
                pos = seg.find(d[:40])
                ins = len(beats)
                for k, b in enumerate(beats):
                    bt = str(b.get("text") or b.get("line") or "")[:40]
                    bp = seg.find(bt)
                    if bp > pos:
                        ins = k
                        break
                beats.insert(ins, {"type": "action", "text": clean})
                have.add(_n(clean))
            s.beats = beats


def _sanitize_participants(script) -> None:
    """participants 本地裁决（问题1，2026-08-21）：完全听 LLM——
    只做机械清洗（去空/去重/仅去空白），LLM 提议什么参与者就保留什么。"""
    for ep in script.episodes:
        for s in ep.scenes:
            out: list[str] = []
            for p in s.participants:
                p = _norm_name(str(p).strip())
                if not p:
                    continue
                if p not in out:
                    out.append(p)
            s.participants = out



_METAPHOR_PHRASE_RES = (
    re.compile(r"像[^，。；]{0,15}?(?:一样|一般|似的)"),
    re.compile(r"(?:仿佛|如同|宛如|犹如|好似|恍如|就像|恰如)[^，。；]{0,16}"),
)


def _scrub_visual_text(text: str) -> str:
    """清洗动作/情境描写文本（2026-08-21 修复刺杀丢失）：只剔除声音词/比喻片段，保留可渲染动作。

    - 比喻短语（像…一样/仿佛…）直接删除，动作主体保留（"身体像破布娃娃一样被力量抛起" → "身体被力量抛起"）
    - 含【环境/物体声】的子句（按，。；！？…切分）剔除，动作子句保留
      （"扭转剑身，鲜血涌出，门吱呀一声打开" → "扭转剑身，鲜血涌出"）
    - 【角色发声/表演反应】（惨叫/尖叫/呐喊等）与对白等价，画面保留（2026-08-21 用户指示：罗伊娜惨叫是画面反应，不许删）
    禁止整条删除动作块——关键剧情动作（贯穿/扭转/抛起/惨叫反应）必须保留。
    """
    from app.storyboard import visual_guard
    s = text or ""
    for r in _METAPHOR_PHRASE_RES:
        s = r.sub("", s)
    clauses: list[str] = []
    buf = ""
    for ch in s:
        buf += ch
        if ch in "，。；！？…":
            clauses.append(buf)
            buf = ""
    if buf.strip():
        clauses.append(buf)
    kept = [c for c in clauses
            if not visual_guard.find_sound_in_visual(c) and not visual_guard.find_metaphors(c)]
    return "".join(kept).strip("，。； ").strip()


def _clean_visual_blocks(blocks: list[str]) -> list[str]:
    """本地清洗动作/情境描写：只剔除声音词/比喻片段，保留可渲染动作（不整条删，防刺杀类关键动作丢失）。"""
    out: list[str] = []
    for b in blocks:
        scrubbed = _scrub_visual_text(b)
        if scrubbed:
            out.append(scrubbed)
    return out


_VO_PREFIX_RE = re.compile(
    r"^([^：:\n]*?)(?:[（(](?:旁白|VO|画外音|OS|内心)[^）)]*[）)]|(?:旁白|VO|画外音|OS|内心)(?:[（(][^）)]*[）)])?)\s*[:：]\s*")


_OS_LINE_RE = re.compile(
    r"^([^：:]+?)(?:OS|VO|画外音|内心独白)[（(]?[^：:]*[）)]?\s*[:：]\s*(.+)$")


def _split_os_lines(vo: str, raw: str = "") -> tuple[list[dict], str]:
    """OS/内心独白台词（原文"罗伊娜OS：..."）不应进【旁白】行——拆回 dialogues（is_os=True，交付标注"不开口"）。

    在 _clean_vo 之前处理：LLM 把 OS 台词混进 vo 时，前缀标记可能还在（行首匹配），
    也可能被 LLM 去掉前缀（只剩台词）——此时按原文反查：该台词在原文前有"xxxOS："标记 → 判定 OS。
    拆出后剩余（真旁白）再 _clean_vo。
    """
    dlg: list[dict] = []
    kept: list[str] = []
    _raw_norm = (raw or "").replace(" ", "").replace("\n", "").replace("\r", "")
    for ln in (vo or "").splitlines():
        m = _OS_LINE_RE.match(ln.strip())
        if m:
            spk = _clean_dialogue_speaker(m.group(1))
            line = strip_parens(m.group(2)).strip()  # 去中文注释（用户：英文OS里夹中文是灾难）
            if line:
                dlg.append({"speaker": spk, "line": line, "is_os": True})
            continue
        # 无前缀：原文反查（LLM 去掉了"罗伊娜OS："）
        _ln_norm = (ln or "").replace(" ", "").replace("\n", "").replace("\r", "")[:40]
        if _ln_norm and _raw_norm:
            _idx = _raw_norm.find(_ln_norm)
            if _idx > 0:
                _before = _raw_norm[max(0, _idx - 40):_idx]
                _os = re.search(r"([^：:，。、△（）()\s]+?)(?:OS|VO|画外音|内心独白)[（(]?[^：:]*[）)]?\s*[:：]\s*$", _before)
                if _os:
                    spk = _clean_dialogue_speaker(_os.group(1))
                    line = strip_parens(ln.strip()).strip()
                    if line:
                        dlg.append({"speaker": spk, "line": line, "is_os": True})
                    continue
        kept.append(ln)
    return dlg, "\n".join(kept).strip()


def _clean_vo(vo: str) -> str:
    """本地清洗旁白原文：剔除 LLM 误加的'角色(旁白/VO/画外音/OS)：'说话人前缀（旁白须逐字原样，进【声音】与 fidelity 校验）。

    注意：主体人名+VO（如 父亲（VO，隔着玻璃））是正常对白，不应进 vo——若 LLM 误放进 vo，
    前缀被剔除后由分类闸门 _split_vo_dialogues 按原文反查说话人拆回 dialogues（规则 2026-08-20）。"""
    if not vo:
        return ""
    return _VO_PREFIX_RE.sub("", vo).replace("**", "").strip()  # 清 markdown 星号（原文【旁白】区为 **粗体** 包裹）


def _split_action_blocks(raw_blocks: list) -> tuple[list[str], list[dict]]:
    """action_blocks 兼容纯文本与对象（对象可带 focus/subject/motion），
    文本统一进 action_blocks，元数据单独保留供 beats 合并（2026-08-21 focus 机制）。"""
    texts: list[str] = []
    metas: list[dict] = []
    for a in raw_blocks or []:
        if isinstance(a, dict):
            _t = str(a.get("text") or a.get("action") or a.get("content") or "").strip()
            if _t:
                texts.append(_t)
                metas.append({"text": _t,
                              "focus": str(a.get("focus") or ""),
                              "subject": str(a.get("subject") or ""),
                              "motion": str(a.get("motion") or "")})
        elif isinstance(a, str) and a.strip():
            texts.append(a)
    return texts, metas


def _merge_action_block_meta(beats: list, metas: list[dict]) -> list:
    """把 action_blocks 对象元数据并入 beats action 事件：
    同文本 action 事件存在则补字段，否则补一条 action 事件（只在 LLM 给了 focus 元数据时发生）。"""
    if not metas:
        return list(beats)
    out = [dict(b) if isinstance(b, dict) else b for b in beats]
    for meta in metas:
        _text = _scrub_visual_text(str(meta.get("text") or "").strip())
        if not _text:
            continue
        _meta = _clean_focus_meta(meta)
        _idx = next((i for i, b in enumerate(out)
                     if isinstance(b, dict) and b.get("type") == "action"
                     and _norm_raw(str(b.get("text") or "")) == _norm_raw(_text)), None)
        if _idx is not None:
            _b = out[_idx]
            for _k in ("focus", "subject", "motion"):
                if _meta.get(_k):
                    _b[_k] = _meta[_k]
        else:
            out.append({"type": "action", "text": _text,
                        "focus": _meta.get("focus", ""),
                        "subject": _meta.get("subject", ""),
                        "motion": _meta.get("motion", "")})
    return out


def _clean_beat_events_list(beats: list, raw: str, os_dlgs: list) -> list:
    """beats 事件本地裁决：普通事件过 _clean_beat_events；vo 事件若原文前有 OS 标记 → 转 dialogue(is_os)
    （2026-08-21 修复：OS 台词同时进旁白行+画面行的重复）。"""
    out: list[dict] = []
    for b in beats:
        if isinstance(b, dict) and b.get("type") == "vo":
            _txt = str(b.get("text") or "").strip()
            _ln_norm = _txt.replace(" ", "").replace("\n", "").replace("\r", "")[:40]
            _raw_norm = (raw or "").replace(" ", "").replace("\n", "").replace("\r", "")
            _idx = _raw_norm.find(_ln_norm) if _ln_norm and _raw_norm else -1
            _is_os = False
            if _idx > 0:
                _before = _raw_norm[max(0, _idx - 40):_idx]
                _is_os = bool(re.search(r"([^：:，。、△（）()\s]+?)(?:OS|VO|画外音|内心独白)[（(]?[^：:]*[）)]?\s*[:：]\s*$", _before))
            if _is_os:
                _m = re.search(r"([^：:，。、△（）()\s]+?)(?:OS|VO|画外音|内心独白)[（(]?[^：:]*[）)]?\s*[:：]\s*$",
                               _raw_norm[max(0, _idx - 40):_idx])
                out.append({"type": "dialogue",
                            "speaker": _clean_dialogue_speaker(_m.group(1)) if _m else "主体",
                            "line": strip_parens(_txt).strip(),
                            "is_os": True})
                continue
        out.append(_clean_beat_events(b) if isinstance(b, dict) else b)
    out.extend({"type": "dialogue", "speaker": d["speaker"], "line": d["line"], "is_os": True} for d in os_dlgs)
    return out


def _clean_beat_events(ev: dict) -> dict:
    """beats 有序事件本地裁决（2026-08-20 补齐）：
    - dialogue：说话人复用 classification_gate 清洗（去 OS/VO/括号），与 dialogues 字段一致，避免污染【对白】行；
    - action：text 与 action_blocks 同标准——含声音词/比喻句则本地剔除（宁缺毋滥，画面守卫一致标准），
      LLM 把"发出一声短促的笑"写进 beats action 时不再触发 visual_purity 整次失败。"""
    ev = dict(ev)
    if ev.get("type") == "dialogue":
        _raw_spk = str(ev.get("speaker") or "")
        if re.search(r"OS|VO|画外音|内心", _raw_spk):
            ev["is_os"] = True  # 2026-08-21 问题五：OS/内心独白保留标记，交付标注"不开口"
        ev["speaker"] = _clean_dialogue_speaker(_raw_spk)
    elif ev.get("type") == "action":
        text = str(ev.get("text") or "").strip()
        if text:
            ev["text"] = _scrub_visual_text(text)  # 只剔声音词/比喻片段，保留可渲染动作（刺杀动作不丢）
        ev = _clean_focus_meta(ev)  # focus 枚举/motion 去空去重；subject 可信名过滤在 _sanitize_focus_metadata 按场景统一做
    return ev


def _strip_md(text: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", text).strip()


def _ep_number(title: str, idx: int) -> int:
    m = re.search(r"(\d+)", title)
    return int(m.group(1)) if m else idx + 1


def _split_episodes(text: str) -> list[tuple[str, str]]:
    """优先按「第X集」切分；无集标题（或仅 1 个）回退 split_chapters。"""
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if ln.strip() and _EPISODE_RE.match(ln.strip())]
    if len(hits) < 2:
        return split_chapters(text)
    out: list[tuple[str, str]] = []
    for idx, start in enumerate(hits):
        end = hits[idx + 1] if idx + 1 < len(hits) else len(lines)
        title = lines[start].strip().lstrip("#").strip()[:30]
        body = "\n".join(lines[start + 1:end]).strip()
        if body:
            out.append((title, body))
    return out


def _episodes_from_chapters(chapters: list[tuple[str, str]]) -> list[ParsedEpisode]:
    out: list[ParsedEpisode] = []
    for idx, (title, body) in enumerate(chapters):
        out.append(ParsedEpisode(ep=_ep_number(title, idx), title=_strip_md(title), raw_text=body))
    return out


async def parse_script(
    text: str,
    *,
    title: str = "",
    genre: str = "",
    client: DeepSeekClient | None = None,
    max_episodes: int = 80,
) -> ParsedScript:
    """剧本文本 → ParsedScript（分集 + LLM 提取集信息与角色）。"""
    chapters = _split_episodes(text)[:max_episodes]
    episodes = _episodes_from_chapters(chapters)
    characters: list[ParsedCharacter] = []

    client = client or DeepSeekClient()
    for ep in episodes:
        try:
            data = await client.chat_json(_EPISODE_SYSTEM, ep.raw_text[:12000], max_tokens=settings.llm_max_tokens_spine)
            ep.title = str(data.get("title") or ep.title)
            ep.explosion = str(data.get("explosion") or "")
            ep.hook_open = str(data.get("hook_open") or "")
            ep.hook_end = str(data.get("hook_end") or "")
            curve = data.get("emotional_curve") or []
            ep.emotional_curve = [str(x) for x in curve if x][:4]
            _seg_list = _scene_raw_segments(ep.raw_text or "")
            for s in (data.get("scenes") or [])[:30]:
                if isinstance(s, dict):
                    _seg = _seg_list[len(ep.scenes)] if len(ep.scenes) < len(_seg_list) else (ep.raw_text or "")
                    _os_dlgs, _os_rest = _split_os_lines(str(s.get("vo") or ""), ep.raw_text or "")
                    _abs_texts, _abs_meta = _split_action_blocks(s.get("action_blocks") or [])
                    _segments_clean = _clean_segments(s.get("segments") or [])
                    _beats_clean = _clean_beat_events_list(
                        (s.get("beats") or []), ep.raw_text or "", _os_dlgs)
                    _beats_clean = _merge_action_block_meta(_beats_clean, _abs_meta)
                    _beats_compat = _flatten_segments_to_beats(_segments_clean) if _segments_clean else _beats_clean
                    ep.scenes.append(
                        ParsedScene(
                            scene_id=f"e{ep.ep}_s{len(ep.scenes)+1}",
                            location=str(s.get("location") or ""),
                            interior=str(s.get("interior") or ""),
                            era=str(s.get("era") or ""),
                            time=str(s.get("time") or ""),
                            lighting=str(s.get("lighting") or ""),
                            atmosphere=str(s.get("atmosphere") or ""),
                            participants=[str(x) for x in (s.get("participants") or []) if x],
                            character_states=_clean_character_states(s.get("character_states") or []),
                            action_blocks=_clean_visual_blocks(_abs_texts),
                            props=_clean_string_list(s.get("props") or []),
                            spatial=[
                                {"character": str(x.get("character") or ""),
                                 "position": str(x.get("position") or ""),
                                 "facing": str(x.get("facing") or "")}
                                for x in (s.get("spatial") or []) if isinstance(x, dict)
                            ],
                            lighting_arc=[
                                {"at": str(x.get("at") or ""),
                                 "change": str(x.get("change") or "")}
                                for x in (s.get("lighting_arc") or []) if isinstance(x, dict)
                            ],

                            dialogues=[
                                {"speaker": str(d.get("speaker") or "").replace("（旁白）", "").replace("(旁白)", ""),
                                 "line": str(d.get("line") or ""),
                                 "is_os": bool(re.search(r"OS|VO|画外音|内心", str(d.get("speaker") or ""))),
                                 "pivot": bool(d.get("pivot")),
                                 "reactor": _as_str_list(d.get("reactor"))}
                                for d in (s.get("dialogues") or []) if isinstance(d, dict) and d.get("line")
                                and "旁白" not in str(d.get("speaker") or "")
                            ] + _os_dlgs,
                            vo=_clean_vo(_os_rest),
                            sound_effects=_clean_sound_effects(s.get("sound_effects") or s.get("sound")) or _extract_sound_effects(_seg),
                            negative=_clean_negative(s.get("negative")),
                            emotion=_clean_emotion(s.get("emotion")),
                            segments=_segments_clean,
                            beats=_beats_compat,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            _log.warning("第 %s 集结构化提取失败：%s", ep.ep, exc)
            continue

    # 全局角色提取：覆盖全文（按集均匀采样，保证后出现角色不遗漏），总预算 15000 字
    n_ep = max(1, len(episodes))
    per = max(800, min(4000, 15000 // n_ep))
    sample = "\n\n".join(e.raw_text[:per] for e in episodes)
    try:
        data = await client.chat_json(_CHARACTER_SYSTEM, sample[:15000], max_tokens=settings.llm_max_tokens_spine)
        for c in (data.get("characters") or [])[:50]:
            if isinstance(c, dict) and c.get("name"):
                characters.append(
                    ParsedCharacter(
                        name=str(c["name"]).strip(),
                        role=str(c.get("role") or ""),
                        gender=str(c.get("gender") or ""),
                        age=str(c.get("age") or ""),
                        appearance=str(c.get("appearance") or ""),
                        outfit=str(c.get("outfit") or ""),
                        temperament=str(c.get("temperament") or ""),
                        source=str(c.get("source") or ""),
                    )
                )
    except Exception as exc:  # noqa: BLE001
        _log.warning("角色提取失败：%s", exc)

    failed_eps = [ep.ep for ep in episodes if not ep.scenes]
    if failed_eps:
        raise ValueError(f"以下集场景提取失败（不静默产出）：{failed_eps}；请检查 LLM/重试")
    total_scenes = sum(len(ep.scenes) for ep in episodes)
    if total_scenes == 0:
        raise ValueError("剧本场景提取失败：LLM 未返回任何场景（检查 API/模型配置），阻断产出，不静默空输出")
    script = ParsedScript(title=title, genre=genre, episodes=episodes, characters=characters)
    _local_adjudicate(script)
    return script
