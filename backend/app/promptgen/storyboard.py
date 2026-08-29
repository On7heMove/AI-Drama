"""视频提示词（原故事板）：复用现有 storyboard 引擎，只保留中文（交付不整双语）。

输入 ParsedScript 需先转为 production.schemas.EpisodeScript（场景/对白/动作），
再由 build_shot_prompts 生成每镜双语提示词（机位/光线/站位/调度/声音）。
"""
from __future__ import annotations

import math
import re

from app.production.schemas import DialogueLine, EpisodeScript, SceneScript, ShotBeat
from app.production.storyboard_export import build_shot_prompts
from app.promptgen.schemas import ParsedScene, ParsedScript
from app.storyboard.constraints import validate_shot_prompts
from app.storyboard.shot_selector import ShotSelector
from app.production.segment_export import long_dialogue_plan as _long_dialogue_plan
from app.storyboard.duration_rule import (
    dialogue_seconds as _dlg_seconds,
    load_duration_rule as _load_duration_rule,
    vo_seconds as _vo_seconds,
)
from app.storyboard.dialogue_performance import body_action as _body_action
from app.storyboard.dialogue_performance import negation_detail as _negation_detail
from app.storyboard.dialogue_performance import voice_quality as _voice_quality
from app.storyboard.emotion_infer import infer_emotion as _infer_emotion
# 2026-08-21 最终规格：cinema_infer 保留供其他模块使用，段组装不再注入其技术参数
from app.storyboard.scene_classifier import SceneClassifier as _SceneClassifier
from app.storyboard.schemas import SceneInput as _SceneInput


# 反应类动作标记：画面主体=别人且含反应词时才裁剪为说话人（2026-08-21 机位/画面对齐折中）
_REACTION_MARKERS = ("瞳孔", "反应", "屏息", "神情", "目光", "表情", "一僵", "颤抖", "愣", "泪")
# 反应镜特征词：动作块必须含反应特征才算"听者反应"（2026-08-21 用户：反应镜头必须有具体表情/动作/视线变化）
_REACTION_ACTION_WORDS = ("目光", "看", "望", "瞳孔", "愣", "惊", "皱眉", "皱", "抿", "攥",
                          "低头", "抬头", "冷笑", "勾起", "转身", "退", "僵", "呼吸", "咬", "瞪")
# 环境/状态类动作词：只更新当前画面不成镜（晨光/分列/屏息等氛围，非剧情推进动作）
_AMBIENT_ACTION_WORDS = ("晨光", "透过", "洒", "映", "分列", "屏息", "望向", "环视", "空气",
                         "光线", "穹顶", "地面", "贵族", "人群", "大厅", "烛光", "站着", "跪在")
# 群体/无名角色词：动作主体推断用（不在 participants 时按动作文本中的群体词定主体，机位不撒谎）
_GROUP_SUBJ_WORDS = ("守卫", "人群", "贵族", "众人", "士兵", "护卫", "侍从")


def _as_reactor_list(value) -> list[str]:
    """把 reactor 统一为去空字符串数组（parse 层已清洗；此处兼容测试直构 beats 事件）。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(x).strip() for x in value if isinstance(x, str) and str(x).strip()]


def _dialogue_reaction_meta(s: ParsedScene, ev: dict, speaker: str, line: str) -> tuple[bool, list[str]]:
    """该 dialogue 的反应镜元数据：优先从 s.dialogues 匹配同句（pivot/reactor 的 parse 落点），
    否则回退到 beats 事件自身字段（兼容测试/直构数据）。"""
    for d in s.dialogues or []:
        if (str(d.get("speaker") or "").strip() == speaker
                and str(d.get("line") or "").strip() == line):
            return bool(d.get("pivot")), _as_reactor_list(d.get("reactor"))
    return bool(ev.get("pivot")), _as_reactor_list(ev.get("reactor"))


def _scene_beats(s: ParsedScene) -> list[ShotBeat]:
    """按内容生成镜头（引擎驱动，不写死）：

    - 对话：正反打——每发言组（1 句）一镜，锁定该句说话人；画面=说话人+表演载体（对白驱动）。
    - 旁白/动作：动作节拍 × 引擎角度组——每个动作节拍(action_block)的镜头数由
      shot_selector.beat_group_size 决定，机位/景别/角度/运动由 shot_selector.select_beat 裁决
      （复用既有书籍知识，不在此重造）；旁白 vo 按镜切片、段内拼接完整。

    duration_sec=0.0 占位：对白/旁白由 duration_rule 折算进总时长（纯旁白镜时长=旁白时长，不叠加视觉）。
    """
    subject = first_participant(s.participants)
    if s.beats:
        return _scene_beats_from_events(s, subject)
    blocks = list(s.action_blocks)
    dialogues = [d for d in (s.dialogues or []) if d.get("line")]
    vo = (s.vo or "").replace("\r", "").replace("\n", "").strip()  # 去换行：切片/拼接/正则逐字无损
    beats: list[ShotBeat] = []
    if dialogues:
        # 正反打：连续同说话人合并一镜（镜头锁定该人），说话人切换换镜；同说话人多次出现轮换机位
        for gi, g in enumerate(_group_dialogues(dialogues)):
            spk = [d["speaker"] for d in g if d.get("speaker")]
            subj = spk[0] if spk else subject
            dl = "；".join(
                f"{d['speaker']}OS（内心独白，不开口）：{d['line']}" if d.get("is_os")
                else f"{d['speaker']}：{d['line']}" for d in g)
            # 本地情绪推理（对白文本驱动）→ 喂表演载体（否定细节）与镜头动机（beat.emotion）
            emo = _infer_emotion(dialogue=dl).emotion
            # 剧情动作编入画面（画面拍什么须来自原文 action_blocks，按镜推进剧情——不是编造微动作）
            act = blocks[min(gi, len(blocks) - 1)] if blocks else ""
            # 2026-08-21 机位/画面对齐：动作块显式以其他参与者为主语（如"罗伊娜瞳孔骤缩"挂在伊索尔德镜）才裁剪为说话人；
            # 无主语动作（"刺向罗伊娜"=说话人执行）保留——防止把别人反应挂到说话人镜头上
            _other_head = [p for p in s.participants if p and p != subj and act.startswith(p)]
            _is_reaction = any(m in act for m in _REACTION_MARKERS)
            visual = act if (act and (subj in act or not _other_head or not _is_reaction)) else subj
            # 表演载体仅补充（命中关键词才加；无命中不编造"动作微微一顿"）
            # 2026-08-21 用户规则：表演载体/否定细节模板（眉头微蹙/指尖轻点桌面等）是编造——不进画面；
            # 画面只来自原文动作块 + 说话人；声音质感保留进【声音】行
            voice = _voice_quality(emo, dl)
            # 正反打：机位/景别/角度/运动由 shot_selector 按发言组全局序号轮换
            # （A1 贴脸→B1 正面→A2 侧面→B2 过肩……正反打差异化；不在此自选机位）
            beats.append(ShotBeat(
                subject=subj, action=visual, dialogue=dl, emotion=emo,
                sound=f"环境音；{subj}：{voice}；对白优先；音乐按情绪起伏",
                duration_sec=0.0,
                dialogue_turn_idx=gi,
            ))
    elif vo:
        # 旁白：动作节拍×定制角度；vo 按镜切片、段内拼接完整
        beats = _block_beat_shots(blocks, subject, vo=vo)
        if not beats:
            beats.append(ShotBeat(subject=subject, action=subject or "主体",
                                  sound=f"画外音（旁白·原文）：{vo}", duration_sec=0.0))
    else:
        # 纯动作：同样按动作节拍×定制角度
        beats = _block_beat_shots(blocks, subject)
        if not beats:
            beats.append(ShotBeat(subject=subject, action="；".join(blocks), duration_sec=0.0))
    return beats


def _scene_beats_from_events(s: ParsedScene, subject: str) -> list[ShotBeat]:
    """按有序事件流（beats）生成镜头：动作事件推进当前剧情画面，对白镜画面=该时刻剧情动作+说话人（时序对齐，不靠索引猜）。

    - action：每个动作事件生成独立动作镜（2026-08-21 刺杀序列修复——连续动作"冲→贯穿→扭转→拔剑"逐镜拍出，
      不再只更新 cur_action 被最后一个动作覆盖），并更新 cur_action 供后续对白镜取画面
    - dialogue：画面 = 当前剧情动作 + 说话人 +（命中才加）表演载体；台词忠实原文
    - vo：旁白镜（画面=当前剧情动作或主体，旁白原文进【旁白】行）
    """
    beats: list[ShotBeat] = []
    cur_action = ""
    _action_seq = 0
    for idx, ev in enumerate(s.beats):
        t = str(ev.get("type") or "")
        if t == "action":
            _txt = str(ev.get("text") or "").strip()
            if not _txt:
                continue
            _angle_idx = _action_seq
            _action_seq += 1
            cur_action = _txt
            # 2026-08-21：关键剧情动作（刺杀/打斗等）独立成镜——贯穿/扭转/抛起逐镜拍出，不再被后续动作覆盖；
            # 环境/状态类动作（晨光/屏息/望向等）只更新 cur_action 不成镜（避免空镜冗余）
            if not any(w in _txt for w in _AMBIENT_ACTION_WORDS):
                # 2026-08-21 机位主体=画面主体：动作镜 subject=动作主语——
                # ① 文本开头参与者；② 前部出现的参与者（"视野中看到珍妮芙"）；③ 群体角色词
                #（"拿弓箭的守卫拉开弓"→守卫，防止挂到场景主体罗伊娜）；④ 无主语沿用前一镜
                _act_subj = next((pp for pp in s.participants if pp and _txt.startswith(pp)), None)
                if _act_subj is None:
                    _act_subj = next((pp for pp in s.participants if pp and pp in _txt[:20]), None)
                if _act_subj is None:
                    _act_subj = next((w for w in _GROUP_SUBJ_WORDS if w in _txt[:12]), None)
                if _act_subj is None:
                    _act_subj = beats[-1].subject if beats else subject
                _ev_subject = str(ev.get("subject") or "").strip()
                if _ev_subject:
                    _act_subj = _ev_subject
                beats.append(ShotBeat(subject=_act_subj, action=_txt, dialogue="",
                                      duration_sec=0.0, dialogue_turn_idx=len(beats),
                                      angle_idx=_angle_idx,
                                      focus=str(ev.get("focus") or ""),
                                      motion=str(ev.get("motion") or "")))
        elif t == "dialogue":
            speaker = str(ev.get("speaker") or "").strip() or subject
            line = str(ev.get("line") or "").strip()
            if not line:
                continue
            emo = _infer_emotion(dialogue=line).emotion
            # 2026-08-21 用户规则：表演载体/否定细节模板是编造——不进画面
            _other_head = [pp for pp in s.participants if pp and pp != speaker and cur_action.startswith(pp)]
            _is_reaction = any(m in cur_action for m in _REACTION_MARKERS)
            visual = cur_action if (cur_action and (speaker in cur_action or not _other_head or not _is_reaction)) else speaker
            _is_os = bool(ev.get("is_os"))
            _dlg_label = f"{speaker}OS（内心独白，不开口）" if _is_os else speaker
            if _is_os:
                visual = speaker  # 2026-08-21 用户规则四：OS 镜画面=说话人（近景/特写进入内心），不取 cur_action——
                # 防"狼王黑影画面配罗伊娜OS"人格分裂
            beats.append(ShotBeat(
                subject=speaker, action=visual, dialogue=f"{_dlg_label}：{line}", emotion=emo,
                sound=f"环境音；{speaker}：{_voice_quality(emo, line)}；对白优先；音乐按情绪起伏",  # 声音行用纯说话人
                duration_sec=0.0, dialogue_turn_idx=len(beats),
                focus=str(ev.get("focus") or ""),
                motion=str(ev.get("motion") or ""),
            ))
            # 2026-08-21 反应镜配套：pivot 关键句 + reactor 非空 → 说话人镜后追加一个反应镜
            # （本次只做 reactor[0]，其余反应者留待后续；机位/景别/角度/运动仍由 shot_selector 按 beats 序号裁决）
            _pivot, _reactors = _dialogue_reaction_meta(s, ev, speaker, line)
            _reaction_text = str(ev.get("reaction") or "").strip()
            if _pivot and _reactors and not _is_os and _reaction_text:
                _rsubj = _reactors[0]
                beats.append(ShotBeat(
                    subject=_rsubj,
                    action=_reaction_text,
                    dialogue="",
                    duration_sec=0.0,
                    dialogue_turn_idx=len(beats),
                ))
        elif t == "vo":
            vo_text = str(ev.get("text") or "").strip()
            if vo_text:
                beats.append(ShotBeat(subject=subject, action=cur_action or subject,
                                      sound=f"画外音（旁白·原文）：{vo_text}", duration_sec=0.0,
                                      dialogue_turn_idx=len(beats)))
            last_was_action = False
    return beats


def _group_dialogues(dialogues: list[dict], per: int = 1) -> list[list[dict]]:
    """正反打分组：连续同说话人合并为一镜（镜头锁定该说话人），说话人切换才换镜。

    同一说话人连续多句不碎成同机位同画面多镜（避免"只差对白"的重复），
    正反打在 A→B→A 切换处换镜头。
    """
    groups: list[list[dict]] = []
    for d in dialogues:
        if groups and d.get("speaker") == groups[-1][-1].get("speaker"):
            groups[-1].append(d)
        else:
            groups.append([d])
    return groups


def _slice_vo(vo: str, idx: int, n: int) -> str:
    """按字符比例切旁白（连续无缝隙，跨镜连续；镜头数由时长驱动，n 可大于句数）。"""
    if not vo:
        return ""
    length = len(vo)
    start = length * idx // n
    end = length * (idx + 1) // n
    return vo[start:end].strip()


# 既有镜头引擎（复用，不重造）：镜头数/机位/景别/角度/运动全部由 shot_selector 裁决
_SHOT_SELECTOR = ShotSelector()


def _block_beat_shots(blocks: list[str], subject: str, vo: str = "") -> list[ShotBeat]:
    """动作节拍 → 内容镜（2026-08-21 推翻重写：拆分收敛、主体绑定）。

    - 拆分收敛：镜头数 = min(引擎角度数, 子句数)——一个动作块只有一个子句就一镜，
      禁止"同一动作复制三遍换景别"（重复堆砌根源）
    - 主体绑定：无主语子句（"她仰头大喊"）沿用前一镜主体（动作延续者），机位=画面主体
    - 旁白 vo 按镜切片、段内拼接完整
    """
    shots: list[ShotBeat] = []
    sel = _SHOT_SELECTOR
    total = sum(sel.beat_group_size(b) for b in blocks)
    vi = 0
    for b in blocks:
        _clauses = [c.strip() for c in re.split(r"[，。；！？]", b) if c.strip()]
        # 旁白(vo)场景：按时长驱动多镜（防 PPT，机位/角度轮换拍主体）；纯动作场景：按子句收敛（防重复堆砌）
        n = sel.beat_group_size(b) if vo else min(sel.beat_group_size(b), max(1, len(_clauses)))
        for k in range(n):
            # 旁白(vo)多镜：画面=整个动作块（机位/角度轮换拍主体，不拆子句）；动作多镜：子句分配
            _act = _clauses[k] if (n > 1 and len(_clauses) >= n and not vo) else b
            _subj = shots[-1].subject if (n > 1 and k > 0 and shots) else subject
            sound = f"画外音（旁白·原文）：{_slice_vo(vo, vi, total)}" if vo else ""
            shots.append(ShotBeat(
                subject=_subj,
                action=_act,
                dialogue="",
                sound=sound,
                duration_sec=0.0,
                angle_idx=k,
            ))
            vi += 1
    return shots


def _to_episode_scripts(script: ParsedScript) -> list[EpisodeScript]:
    eps: list[EpisodeScript] = []
    for ep in script.episodes:
        scenes: list[SceneScript] = []
        for i, s in enumerate(ep.scenes, 1):
            beats = _scene_beats(s) if s.action_blocks else []
            scenes.append(
                SceneScript(
                    scene_id=s.scene_id or f"e{ep.ep}_s{i}",
                    location=s.location,
                    time=s.time,
                    lighting=s.lighting,
                    participants=list(s.participants),
                    action_blocks=list(s.action_blocks),
                    dialogues=[
                        DialogueLine(speaker=d.get("speaker", ""), line=d.get("line", ""), emotion="")
                        for d in (s.dialogues or []) if d.get("line")
                    ],
                    beats=beats,
                )
            )
        eps.append(
            EpisodeScript(
                ep=ep.ep,
                title=ep.title,
                hook_open=ep.hook_open,
                hook_end=ep.hook_end,
                explosion=ep.explosion,
                scenes=scenes,
                events=[],
            )
        )
    return eps


def first_participant(participants: list[str]) -> str:
    return participants[0] if participants else "主体"


def _find_reaction_action(sc, react_name: str, used: set, after_idx: int = 0) -> str:
    """从场景动作块找【反应者】的独立动作（2026-08-21 用户：反应镜头必须有具体动作，不是"神情微动"废话；
    找不到→删镜不凑数）。约束：反应者必须在场（participants），转场/画外类动作（黑影/狼嚎/画面外）不作对话反应；
    只取【当前动作之后】的动作块（after_idx 后）——防时间线倒退（刺杀后拿到开场"并肩跪绒毯"）。"""
    if react_name not in sc.participants:
        return ""  # 反应者不在场
    for _i, a in enumerate(sc.action_blocks):
        if _i <= after_idx:
            continue  # 只取当前动作之后（时间顺序）
        if react_name in a and a not in used and not a.startswith("反应："):
            if any(w in a for w in ("画面外", "黑影", "宝座", "传来", "转场", "狼嚎")):
                continue  # 不在场/转场动作不作对话反应
            # 2026-08-21：必须是"反应类"动作（有目光/神情/身体反应特征），"并肩跪在绒毯上"这类场景定位不是反应
            if any(w in a for w in _REACTION_ACTION_WORDS):
                return a
    return ""


def _find_followup_action(sc, name: str, used: set, after_idx: int = 0) -> str:
    """从场景动作块找【说话人】的后续独立动作（收束镜画面，替代"语毕目光沉定"模板）。
    只取【当前动作之后】（after_idx 后）——防时间线倒退（濒死时插回开场动作）。"""
    for _i, a in enumerate(sc.action_blocks):
        if _i <= after_idx:
            continue
        if name and name in a and a not in used:
            return a
    return ""


def _apply_long_dialogue_split(eps) -> None:
    """2026-08-21 推翻重写：已废弃（不再插三拍镜）。保留空实现兼容旧调用；长对白由事件流自然成镜。"""
    return


# 段时长规则（用户指示 2026-08-20）：段=多镜；单段总时长 ≤15s 超过另起一段；旁白连续段可宽容到 60s；单镜 <4s 提到 4s
_MIN_SEG_SEC = 4.0
_MAX_SEG_SEC = 15.0
_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"

_LINE_RX = {
    "style": r"^【风格锁定】(.+)$",
    "scene": r"^【画面】(.+)$",
    "dlg": r"^【对白】(.+)$",
    "blocking": r"^【站位·轴线】(.+)$",
    "staging": r"^【调度】(.+)$",
    "tone": r"^【色调】(.+)$",
    "lighting": r"^【光线】(.+)$",
    "edit": r"^【剪辑】(.+)$",
    "sound": r"^【声音】(.+)$",
    "speed": r"^【速度】(.+)$",
    "emotion": r"^【情绪】(.+)$",
    "negative": r"^【负面】(.+)$",
}


def _parse_shot(zh: str) -> dict:
    """解析单镜提示词为字段字典（段组装用）。"""
    d: dict = {}
    for key, pat in _LINE_RX.items():
        m = re.search(pat, zh, flags=re.M)
        d[key] = m.group(1).strip() if m else ""
    mc = re.search(r"^【镜头】机位：(.+)$", zh, flags=re.M)
    d["camera"] = mc.group(1).strip() if mc else ""
    _cam_parts = d["camera"].split("｜")
    d["scale"] = _cam_parts[1].strip() if len(_cam_parts) >= 2 else ""
    dm = re.search(r"（约(\d+(?:\.\d+)?)s", zh)
    d["dur"] = float(dm.group(1)) if dm else 0.0
    return d


def _is_narration_shot(sh: dict) -> bool:
    return "旁白·原文" in sh.get("sound", "")


def _group_shots_into_segments(shots: list[dict]) -> list[list[dict]]:
    """逐镜分组（2026-08-20 修复混合场景整段超时）：
    - 连续旁白镜 → 整段一段（旁白台词逐字连续、不被时长边界切碎；60s 宽容，多角度拍主体靠镜头链）
    - 长对白三拍（说话→反应→收束）→ 强制同段（P2：长对白允许突破15s，标人工排版）
    - 其余对白/动作镜 → 相邻加和 ≤15s 一组，超过另起一段
    """
    groups: list[list[dict]] = []
    i = 0
    n = len(shots)
    while i < n:
        if _is_narration_shot(shots[i]):
            j = i
            while j < n and _is_narration_shot(shots[j]):
                j += 1
            groups.append(shots[i:j])
            i = j
            continue
        if (shots[i].get("dlg") and i + 2 < n
                and str(shots[i + 1].get("scene", "")).startswith("反应：")):
            # 三拍=说话→反应(L-Cut)→收束 强制同段；识别不依赖收束镜文案（2026-08-21 去模板化后"扫场后落定"已移除）
            groups.append(shots[i:i + 3])
            i += 3
            continue
        cur = [shots[i]]
        cur_dur = max(shots[i].get("dur", 0.0), _MIN_SEG_SEC)
        i += 1
        while i < n and not _is_narration_shot(shots[i]):
            d = max(shots[i].get("dur", 0.0), _MIN_SEG_SEC)
            if cur_dur + d > _MAX_SEG_SEC:
                break
            cur.append(shots[i])
            cur_dur += d
            i += 1
        groups.append(cur)
    return groups


def _join_scene_dlg(sc: str, dlg_txt: str) -> str:
    """画面内容与对白引语拼接：对白前自动补句号（防粘连），画面为空时只输出对白。"""
    if not sc:
        return dlg_txt
    if not dlg_txt:
        return sc
    if sc[-1] in "。！？…，；":
        return sc + dlg_txt
    return sc + "。" + dlg_txt


def _fmt_dlg(dlg: str) -> str:
    """对白格式化为范例样式（画面行内嵌）：'说话人：台词' → '说话人：“台词”'；一镜多句按；拆段分别加引号。"""
    parts = []
    for seg in (dlg or "").split("；"):
        seg = seg.strip()
        m = re.match(r"^(.+?)：(.+)$", seg, flags=re.S)
        parts.append(f"{m.group(1).strip()}：“{m.group(2).strip()}”" if m else seg)
    return "".join(parts)


def _extract_vo_body(sound: str) -> str:
    m = re.search(r"画外音（旁白·原文）：(.+)$", sound, flags=re.S)
    return m.group(1).strip() if m else ""


_VO_OS_PREFIX_RE = re.compile(r"^([^：:]+?)(?:OS|VO|画外音|内心独白)[（(]?[^：:]*[）)]?\s*[:：]\s*(.+)$", re.S)


def _vo_os_parts(ev: dict, scene) -> tuple[str, str]:
    """vo 台词与说话人：LLM speaker 优先，其次 vo 文本前缀（罗伊娜OS：...），再 visual 反查，最后场景主体兜底。"""
    text = str(ev.get("text") or "").strip()
    speaker = str(ev.get("speaker") or "").strip()
    line = text
    if not speaker:
        m = _VO_OS_PREFIX_RE.match(text)
        if m:
            speaker = m.group(1).strip()
            line = m.group(2).strip()
    if speaker:
        speaker = re.sub(r"(?:OS|VO|画外音|内心独白|旁白)[（(]?[^）)]*[）)]?", "", speaker).strip()
    if not speaker:
        visual = str(ev.get("visual") or "").strip()
        for pp in scene.participants:
            if pp and pp in visual:
                speaker = pp
                break
    if not speaker:
        # LLM 未给 speaker 且 visual 也未命中参与者：不猜 first_participant，只用通用主体
        speaker = "主体"
    return speaker, line


def _merge_sound(shots: list[dict], is_vo: bool, sound_effects: str = "") -> str:
    """段内【声音】行：环境音 + 人物语气语音指导 + 音乐（2026-08-21 用户指示：人物语气可以有语音指导）；
    台词内容（对白/旁白）不留声音行（已内嵌画面行/【旁白】行）。

    优先级：原文【音效】区（sound_effects）→ 对白镜人物语气（voice_quality）→ 场景声音轨（无台词）→ 通用"环境音"。
    """
    voices: list[str] = []
    for s in shots:
        m = re.search(r"环境音；(.*?)(?:；对白优先|；音乐)", s.get("sound", ""))
        if m and m.group(1).strip() and m.group(1).strip() not in voices:
            voices.append(m.group(1).strip())
    if sound_effects:
        core = f"环境音：{sound_effects}"
    else:
        base = shots[0].get("sound", "") if shots else ""
        if voices:
            core = "环境音"
        elif not base or "画外音（旁白·原文）" in base or "对白优先" in base or "环境音" in base:
            core = "环境音"
        else:
            return base  # 场景声音轨（如"音效主导（打击/碰撞/运动）；节奏音乐"）无台词，保留
    tail = "；".join(voices)
    if tail:
        return f"{core}；{tail}；音乐按情绪起伏"
    return f"{core}；音乐按情绪起伏"


def _segment_blocking(shots: list[dict], fallback: str, scene_participants: list | None = None) -> str:
    """段级站位·轴线（2026-08-21 豆包问题2/4）：每段按【该段实际人物】重写，无法重建/人物不符 → 删除整块。

    规则：
    - 参与者 = 段内对白说话人 + 段内画面中出现的其他参与者（按首个镜头人物关系）
    - 轴线只在这些参与者之间建立；轴线上两人必须都在段内出现
    - 无法重建（<2 人或推理失败）→ 返回 ""（段组装删掉【站位·轴线】行，不沿用过时场景轴线）"""
    spk: list[str] = []
    for s in shots:
        dlg = s.get("dlg") or ""
        m = re.match(r"^([^：:]+)：", dlg)
        if m and m.group(1).strip():
            _sp = re.sub(r"OS（内心独白，不开口）", "", m.group(1).strip()).strip()  # 站位/轴线用纯说话人
            if _sp and _sp not in spk:
                spk.append(_sp)
    sc_text = " ".join(s.get("scene", "") for s in shots)
    parts = list(spk)
    for pp in (scene_participants or []):
        if pp and pp not in parts and pp in sc_text:
            parts.append(pp)
    if len(parts) < 2:
        return ""  # 单人/无人物信息：无法重建轴线，删除整块（不沿用过时场景轴线）
    from app.storyboard.blocking import infer_blocking as _ib
    from app.production.schemas import DialogueLine as _DL, SceneScript as _SS
    _sc = _SS(scene_id="seg", participants=parts,
              action_blocks=[s.get("scene", "") for s in shots if s.get("scene")],
              dialogues=[_DL(speaker=x, line="") for x in parts])
    _tb = _ib(_sc)
    if _tb.required and _tb.active and len(_tb.axis) > 1 and _tb.axis[1]:
        _a, _b = _tb.axis[0], _tb.axis[1]
        if _a in parts and _b in parts:
            return _tb.to_prompt()
    return ""  # 推理失败或轴线人物不在段内 → 删除整块


def _is_three_beat(shots: list[dict]) -> bool:
    """长对白三拍段判定（2026-08-21 统一）：说话→反应(L-Cut)→收束，识别不依赖收束镜文案。"""
    return bool(shots[0].get("dlg")) and len(shots) >= 3 and str(shots[1].get("scene", "")).startswith("反应：")


def _segment_durations(shots: list[dict]) -> list[float]:
    """段内镜头时长（ceil；2026-08-21 修复时间轴断裂）：
    长对白三拍段=对白时长按 0.4/0.35/0.25 切分后向上取整（单镜保 4s 下限）；其余=原镜头时长 ceil（保 4s）。
    _assemble_segment 与 build_storyboard_prompts 外层时间轴累加共用同一口径，避免段间起点偏移。"""
    if _is_three_beat(shots):
        _dlg_sec = _dlg_seconds(shots[0].get("dlg", "") or "")
        _ratios = (0.4, 0.35, 0.25)
        durs = [float(max(math.ceil(_dlg_sec * r), _MIN_SEG_SEC)) for r in _ratios]
        return durs[:len(shots)] + [float(max(math.ceil(float(s.get("dur", 0.0) or 0.0)), _MIN_SEG_SEC)) for s in shots[len(durs):]]
    return [float(max(math.ceil(float(s.get("dur", 0.0) or 0.0)), _MIN_SEG_SEC)) for s in shots]


def _segment_lighting_value(shots: list[dict], lighting: str, lighting_arc: list | None = None,
                             seg_index: int = 0) -> str:
    """段级【光线】行（批次B）：首段=lighting 基准；后续段有原文 lighting_arc 变化→"同上，{change}"，否则"同上"。"""
    base = str(shots[0].get("lighting") or "").strip() if shots else ""
    if not base and isinstance(lighting, str):
        base = lighting.strip()
    if seg_index == 0 or not base:
        return base
    seg_text = " ".join(str(s.get("scene") or "") for s in shots)
    changes: list[str] = []
    for arc in lighting_arc or []:
        if not isinstance(arc, dict):
            continue
        at = str(arc.get("at") or "").strip()
        change = str(arc.get("change") or "").strip()
        if at and change and at in seg_text:
            changes.append(change)
    if changes:
        return "同上，" + "；".join(changes)
    return "同上"


def _segment_negative(scene_negative: list[str] | None, fallback: str) -> str:
    """段级【负面】行：LLM negative 优先（NOT 拼装）；空则回退现有 shot_negative 兜底。"""
    if scene_negative:
        return "NOT " + "、".join(scene_negative)
    return fallback or ""


def _segment_emotion(scene_emotion: str, shots: list[dict]) -> str:
    """段级【情绪】行：LLM emotion 优先；缺失/非法（parse 已置空）回退 emotion_infer。"""
    if scene_emotion:
        return scene_emotion
    _emo = (shots[0].get("emotion") or "") if shots else ""
    if _emo in ("", "中性"):
        _act_txt = " ".join(s.get("scene", "") for s in shots)
        _dlg_txt = "；".join(s.get("dlg", "") for s in shots if s.get("dlg"))
        return _infer_emotion(action=_act_txt, dialogue=_dlg_txt).emotion or "中性"
    return _emo


_BLOCKING_SPATIAL_WORDS = ("左", "右", "朝向", "背后", "面对", "背对", "对面", "身后", "前方")


def _segment_beat_characters(segment: dict, scene_participants: list | None = None) -> list[str]:
    """该段 beats 实际出现的角色名单（2026-08-21 轴线质检）：
    subject/speaker/reactor 字段 + 出现在 beat 文本中的场景参与者；
    用于判定 blocking 是否涉及段内角色关系。"""
    names: list[str] = []
    parts = [str(p).strip() for p in (scene_participants or []) if str(p).strip()]
    for b in segment.get("beats") or []:
        if not isinstance(b, dict):
            continue
        for key in ("subject", "speaker"):
            v = str(b.get(key) or "").strip()
            if v and v not in names:
                names.append(v)
        for r in _as_reactor_list(b.get("reactor")):
            if r and r not in names:
                names.append(r)
        text = " ".join(str(b.get(k) or "") for k in ("text", "line", "visual"))
        for p in parts:
            if p and p not in names and p in text:
                names.append(p)
    return names


def _is_executable_blocking(blocking: str, char_names: list[str] | None = None) -> bool:
    """blocking 必须是明确的轴线/空间约束（2026-08-21 形意破械质检）：
    - 轴线=X→Y：X/Y 必须都是段内角色名（"玻璃门内"等非角色端点不通过）
    - 或含明确左右/朝向/背后/面对关系且涉及段内角色
    - 单角色段 / blocking 未涉及段内角色关系 → 省略（不输出"禁止左右互换"样板）"""
    b = (blocking or "").strip()
    if not b:
        return False
    names = [str(n).strip() for n in (char_names or []) if str(n).strip()]
    if len(names) < 2:
        return False  # 单角色段无真正轴线
    m = re.search(r"轴线\s*[=:：]\s*([^→，。；;、\s]+?)\s*→\s*([^，。；;、\s]+)", b)
    if m:
        return m.group(1).strip() in names and m.group(2).strip() in names
    if not any(w in b for w in _BLOCKING_SPATIAL_WORDS):
        return False
    return any(n in b for n in names)


def _style_lock(aspect: str) -> str:
    """交付样式锁定：按画幅取风格文案（9:16/16:9/2.39:1/2.35:1/21:9/1.85:1/4:3/1:1）。

    复用 realism_style 画幅映射（8 种画幅 → 真人风风格锁定），与 production 链路一致。
    """
    from app.storyboard.realism_style import style_lock_zh as _realism_style_zh
    return _realism_style_zh(aspect) or (
        "写实电影质感，35mm胶片颗粒，暖调自然光，横屏16:9。" if aspect == "16:9"
        else "写实电影质感，35mm胶片颗粒，暖调自然光，9:16。"
    )


def _load_focus_duration_cfg() -> dict:
    """focus_duration 配置：配置化镜头时长（2026-08-21 parse 重构批次2）。"""
    return (_load_duration_rule().get("data", {}) or {}).get("focus_duration", {}) or {}


def _focus_shot_duration(focus: str) -> float:
    """focus 查表时长（ceil）：细节/反应情绪 1.5→2；主体动作/关系过程 2.5→3；环境 3.5→4；无 focus 2.5→3。"""
    cfg = _load_focus_duration_cfg()
    key = str(focus or "").strip()
    raw = float(cfg[key]) if key and key in cfg else float(cfg.get("default", 2.5))
    return float(max(1, math.ceil(raw)))


def _beat_shot_duration(ev: dict) -> float:
    """beat 时长（ceil）：dialogue=语速折算；vo=旁白时长；action/无类型=focus 查表。"""
    typ = str(ev.get("type") or "").strip()
    if typ == "dialogue":
        line = str(ev.get("line") or "").strip()
        return float(max(1, math.ceil(_dlg_seconds(line)))) if line else _focus_shot_duration("")
    if typ == "vo":
        text = str(ev.get("text") or "").strip()
        return float(max(1, math.ceil(_vo_seconds(f"画外音（旁白·原文）：{text}")))) if text else _focus_shot_duration("")
    return _focus_shot_duration(str(ev.get("focus") or ""))


def _ensure_camera_subject(camera_pos: str, subject: str) -> str:
    """机位括号主体：裸机位必须补（{subject}）；已有括号但未含当前主体则替换为当前主体。"""
    cam = str(camera_pos or "").strip()
    subj = str(subject or "").strip()
    if not cam:
        return f"机位（{subj or '主体'}）"
    if not subj:
        return cam
    if subj in cam:
        return cam
    if re.search(r"（[^）]*）", cam):
        return re.sub(r"（[^）]*）", f"（{subj}）", cam, count=1)
    return f"{cam}（{subj}）"


def _select_segment_beat_shot(scene: ParsedScene, summary: str, subject: str, index: int, total: int,
                              focus: str = "", motion: str = "", beat_dialogue: str = "",
                              has_dialogue: bool = False) -> dict:
    """调用 ShotSelector.select_beat 选单镜（本地只做镜头组装，不手写机位/景别/角度/运动）。"""
    participants = [subject] if subject else []
    participants += [p for p in (scene.participants or []) if p and p != subject]
    if not participants:
        participants = ["主体"]
    si = _SceneInput(
        scene_id=scene.scene_id or "",
        scene_type="",
        emotion=scene.emotion or "",
        participants=participants,
        location=scene.location,
        summary=summary or subject or "主体",
    )
    return _SHOT_SELECTOR.select_beat(
        si,
        index=index,
        total=total,
        scene_summary=summary or subject or "主体",
        has_dialogue=has_dialogue,
        beat_dialogue=beat_dialogue,
        focus=focus,
        motion=motion,
        focus_seq=index,
    )


def _segment_shot_camera_line(shot, subject: str) -> str:
    """镜头机位行：机位｜景别｜角度｜运动，且机位必须带（主体）。"""
    cam = _ensure_camera_subject(shot.camera_pos, subject)
    return f"机位：{cam}｜景别：{shot.scale}｜角度：{shot.angle}｜运动：{shot.movement}"


def _build_segment_shots(segment: dict, scene: ParsedScene) -> list[dict]:
    """一个 LLM segment → 镜头列表：action/dialogue/vo 一 beat 一镜；dialogue pivot+reactor 追加反应镜。"""
    beats = [b for b in (segment.get("beats") or []) if isinstance(b, dict)]
    shots: list[dict] = []
    total = max(1, len(beats))
    for idx, ev in enumerate(beats):
        typ = str(ev.get("type") or "").strip()
        if typ == "action":
            text = str(ev.get("text") or "").strip()
            if not text:
                continue
            subject = str(ev.get("subject") or "").strip() or first_participant(scene.participants)
            shot = _select_segment_beat_shot(
                scene, text, subject, len(shots), total,
                focus=str(ev.get("focus") or ""),
                motion=str(ev.get("motion") or ""),
            )
            # 2026-08-21 升格最终规格：LLM 给 slow_mo 才标；没给不标、本地零升格兜底
            _slow_mo = ev.get("slow_mo")
            try:
                _slow_mo_sec = float(_slow_mo) if _slow_mo not in (None, "") else 0.0
            except (TypeError, ValueError):
                _slow_mo_sec = 0.0
            shots.append({
                "type": "action",
                "subject": subject,
                "scene": text,
                "dlg": "",
                "sound": "",
                "duration": _beat_shot_duration(ev),
                "camera": _segment_shot_camera_line(shot, subject),
                "speed": {"mode": "slow_mo", "seconds": _slow_mo_sec} if _slow_mo_sec > 0 else {},
            })
        elif typ == "dialogue":
            speaker = str(ev.get("speaker") or "").strip() or first_participant(scene.participants)
            line = str(ev.get("line") or "").strip()
            if not line:
                continue
            # 2026-08-21 批次4：状态由 LLM 写进 text，本地原样用于画面，不本地拼装
            state_text = str(ev.get("text") or "").strip()
            is_os = bool(ev.get("is_os"))
            dlg = f"{speaker}OS（内心独白，不开口）：{line}" if is_os else f"{speaker}：{line}"
            shot = _select_segment_beat_shot(
                scene, dlg, speaker, len(shots), total,
                beat_dialogue=dlg, has_dialogue=True,
            )
            shots.append({
                "type": "dialogue",
                "subject": speaker,
                "scene": state_text,
                "dlg": dlg,
                "sound": "",
                "duration": _beat_shot_duration(ev),
                "camera": _segment_shot_camera_line(shot, speaker),
            })
            reactors = _as_reactor_list(ev.get("reactor"))
            reaction_text = str(ev.get("reaction") or "").strip()
            if ev.get("pivot") and reactors and reaction_text and not is_os:
                rsubj = reactors[0]
                rshot = _select_segment_beat_shot(
                    scene, reaction_text, rsubj, len(shots), total,
                    focus="反应情绪",
                )
                shots.append({
                    "type": "reaction",
                    "subject": rsubj,
                    "scene": reaction_text,
                    "dlg": "",
                    "sound": "",
                    "duration": _focus_shot_duration("反应情绪"),
                    "camera": _segment_shot_camera_line(rshot, rsubj),
                })
        elif typ == "vo":
            text = str(ev.get("text") or "").strip()
            if not text:
                continue
            # 2026-08-21 最终规格：vo 镜画面 = LLM visual（如有）；否则只写“说话人表情”（本地不编具体表情）
            speaker, line = _vo_os_parts(ev, scene)
            visual = str(ev.get("visual") or "").strip()
            picture = visual if visual else f"{speaker}表情"
            dlg = f"{speaker}OS（内心独白，不开口）：{line}"
            shot = _select_segment_beat_shot(
                scene, picture, speaker, len(shots), total,
                beat_dialogue=dlg, has_dialogue=True,
            )
            shot.movement = "固定"  # vo/旁白镜运动安全缺省=固定，不继承相邻动作
            shots.append({
                "type": "vo",
                "subject": speaker,
                "scene": picture,
                "dlg": dlg,
                "sound": "",
                "duration": _beat_shot_duration(ev),
                "camera": _segment_shot_camera_line(shot, speaker),
            })
    return shots


def _render_segment_prompt(shots: list[dict], segment: dict, scene: ParsedScene, start_sec: float,
                           lighting: str, aspect: str, scene_negative: list[str] | None,
                           scene_participants: list | None, lighting_arc: list | None,
                           seg_index: int = 0) -> tuple[str, float]:
    """渲染一段 LLM segment 提示词；返回 (文本, 段时长)。"""
    durs = [float(max(1, math.ceil(float(s.get("duration") or 0.0)))) for s in shots]
    total = sum(durs)
    if shots and total < float(_load_focus_duration_cfg().get("min_seg_sec", 4.0)):
        extra = float(_load_focus_duration_cfg().get("min_seg_sec", 4.0)) - total
        shots[-1]["duration"] = float(shots[-1].get("duration") or 0.0) + extra
        durs[-1] = float(max(1, math.ceil(shots[-1]["duration"])))
        total = sum(durs)
    t = start_sec
    chain: list[str] = []
    for i, (s, d) in enumerate(zip(shots, durs)):
        mark = _MARKS[i] if i < len(_MARKS) else f"[{i + 1}]"
        cam = str(s.get("camera") or "").strip()
        scene_text = str(s.get("scene") or "").strip()
        dlg = str(s.get("dlg") or "").strip()
        bind = f"｜{_join_scene_dlg(scene_text, _fmt_dlg(dlg))}" if (scene_text or dlg) else ""
        # 2026-08-21 批次4：升格落到镜头行（可执行参数），画面 text 不写“慢动作”
        _spd = s.get("speed") or {}
        slow_mo = ""
        if _spd.get("mode") == "slow_mo":
            _sec = float(_spd.get("seconds") or 0)
            slow_mo = f"｜该镜慢动作（约{_sec:.0f}s）"
        chain.append(f"{mark} {cam}（{t:.0f}-{t + d:.0f}s）{bind}{slow_mo}")
        t += d
    lines = [f"【风格锁定】{_style_lock(aspect)}"]
    lines.append("【画面】")
    lines.extend(chain)
    # 2026-08-21 M3：删除段级时间行（时间已含在镜头行）
    # 2026-08-21 最终规格：段级轴线=LLM segment.blocking 原样摆放；不可执行结构则省略，不本地推导 spatial
    _blocking = str(segment.get("blocking") or "").strip()
    if _blocking and _is_executable_blocking(_blocking, _segment_beat_characters(segment, scene_participants)):
        lines.append(f"【站位·轴线】{_blocking}")
    # 2026-08-21 最终规格：【光线】只写 LLM 自然描述（基准/同上/同上+lighting_arc），不注入 cinema_infer 技术参数
    _lighting_value = _segment_lighting_value(shots, lighting, lighting_arc, seg_index)
    if _lighting_value:
        lines.append(f"【光线】{_lighting_value}")
    sound = str(segment.get("sound") or "").strip()
    if sound:
        lines.append(f"【声音】{sound}")
    emotion = str(segment.get("emotion") or "").strip()
    lines.append(f"【情绪】{emotion or '中性'}")
    neg = _segment_negative(list(scene_negative or []), "")
    if neg:
        lines.append(f"【负面】{neg}")
    return "\n".join(lines), total
def _assemble_segment(shots: list[dict], start_sec: float, subject: str = "",
                      lighting=None, tech_spec: str = "", sound_effects: str = "",
                      scene_participants: list | None = None,
                      lighting_arc: list | None = None, seg_index: int = 0,
                      scene_negative: list[str] | None = None,
                      scene_emotion: str = "") -> str:
    """把一组镜头组装为一段多镜提示词：镜头链逐镜绑定画面（1:1）+ 对白 + 段级要素。

    镜头→画面 1:1 绑定（用户口径 2026-08-20）：每条镜头链 = 机位（起止秒）｜画面：本镜展示内容，
    SD/人按镜拆分时每镜自带画面，不再有"15 镜 vs 3 画面"的分配歧义。
    """
    durs = _segment_durations(shots)
    total = sum(durs)
    t = start_sec
    chain = []
    for i, (s, d) in enumerate(zip(shots, durs)):
        mark = _MARKS[i] if i < len(_MARKS) else f"[{i + 1}]"
        cam = s.get("camera", "").split("（约")[0].strip()
        sc = (s.get("scene") or "").strip()
        _dlg_txt = _fmt_dlg(s.get("dlg", "")) if s.get("dlg") else ""
        bind = f"｜{_join_scene_dlg(sc, _dlg_txt)}" if (sc or _dlg_txt) else ""
        chain.append(f"{mark} {cam}（{t:.0f}-{t + d:.0f}s）{bind}")
        t += d
    is_vo = any(_is_narration_shot(s) for s in shots)
    sound = _merge_sound(shots, is_vo, sound_effects)
    lines = [
        f"【风格锁定】{shots[0].get('style', '')}",
    ]
    # 2026-08-21 最终规格：不注入本地 cinema_infer 技术参数行（【技术规格】删除）
    lines.append("【画面】")  # 2026-08-20 用户指示：镜头链改画面（交付品不用内部术语）
    lines.extend(chain)
    if is_vo:
        _vo_full = "".join(_extract_vo_body(s.get("sound", "")) for s in shots if s.get("sound"))
        if _vo_full:
            # 2026-08-20 用户指示：旁白单列（参照范例），对白内嵌画面行；【声音】行只留环境音。
            # 2026-08-21：不标单一说话人——旁白段可能是多角色 OS（珍妮芙+罗伊娜），标 subject 会归属错误
            lines.append(f"【旁白】“{_vo_full}”")
    if total > _MAX_SEG_SEC and (len(shots) == 1 or _is_three_beat(shots)):
        # 单镜超长 或 长对白三拍段 >15s：P2 允许突破，标注人工排版
        marker = f"【超时】本段内容约 {round(total)}s（>15s），长对白/长旁白允许突破；请人工按对白/旁白重新排版。"
        lines.append(marker)
    elif is_vo and total > 60.5:
        # 旁白连续段 >60s（超长旁白）：不切段（台词连续），标人工排版（P2 临时突破）
        marker = f"【超时】本旁白段约 {round(total)}s（>60s），旁白台词连续不切段；请人工按旁白重新排版。"
        lines.append(marker)
    # 2026-08-20 用户指示：对白并入【画面】行；删除【调度】/【色调】。
    # 2026-08-21：blocking 按段内对话焦点重建（轴线不僵化）
    _lighting_base = str(shots[0].get("lighting") or "").strip() if shots else ""
    _lighting_base = _lighting_base or (lighting.strip() if isinstance(lighting, str) else "")
    for key, tag in (("blocking", "【站位·轴线】"), ("lighting", "【光线】")):
        if shots[0].get(key) or (key == "lighting" and _lighting_base):
            if key == "blocking":
                _blk = _segment_blocking(shots, shots[0].get(key, ""), scene_participants)
                _line = f"{tag}{_blk}" if _blk else ""
            else:
                # 2026-08-21 用户规则：光线用自然语言（保留既有文本），不追加色温/照度/光比参数（写错且对生成无效）
                _lighting_value = _segment_lighting_value(shots, lighting, lighting_arc, seg_index)
                _line = f"{tag}{_lighting_value}" if _lighting_value else ""
            if _line:
                lines.append(_line)
    # 2026-08-21 死亡场景特判：悲情收尾用 静默延续+缓慢淡出（不用惊吓点音效/切镜，豆包问题8）
    _death = any(w in " ".join(s.get("scene", "") for s in shots)
                for w in ("合上眼睑", "眼睑缓缓合上", "咽气", "死去", "死亡", "生机"))
    # 段级【剪辑】：优先取含 L-Cut/J-Cut 的镜（反应镜的剪辑技法不能丢），否则取第一镜
    _edits = [s.get("edit", "") for s in shots if s.get("edit")]
    _edit_line = next((e for e in _edits if "L-Cut" in e or "J-Cut" in e), _edits[0] if _edits else "")
    if _death:
        _edit_line = "缓慢淡出（静默延续）"
    if _edit_line:
        lines.append(f"【剪辑】{_edit_line}")
    if _death:
        sound = "环境音渐弱，静默延续"
    lines.append(f"【声音】{sound}")
    # 2026-08-21 情绪接入：LLM 场景情绪优先；缺失时用 emotion_infer 按段内容推断（不再全"中性"）
    _emo = _segment_emotion(scene_emotion, shots)
    lines.append(f"【情绪】{_emo}")
    # 2026-08-21 负面接入：LLM 场景负面优先；缺失时回退 shot_negative（genre_negative.json）兜底
    _neg = _segment_negative(scene_negative, shots[0].get("negative") if shots else "")
    if _neg:
        lines.append(f"【负面】{_neg}")
    return "\n".join(lines)


def build_storyboard_prompts(
    script: ParsedScript,
    *,
    aspect: str = "9:16",
    viewpoint: str | None = None,
) -> list[dict]:
    """生成视频提示词：按 ParsedScene.segments 逐段生成（LLM 段=一个交付段，不再按 beats 全局分组/15s 切段）。"""
    out: list[dict] = []
    _cls = _SceneClassifier()
    for ep in script.episodes:
        for i, scene in enumerate(ep.scenes, 1):
            sid = scene.scene_id or f"e{ep.ep}_s{i}"
            if not scene.segments:
                continue
            _emo = scene.emotion or str(scene.segments[0].get("emotion") or "")
            _summary = " ".join(
                [scene.time, scene.location]
                + [f"{d.get('speaker')}：{d.get('line')}" for d in (scene.dialogues or [])]
            )
            _si = _SceneInput(
                scene_id=sid,
                scene_type="",
                emotion=_emo,
                participants=list(scene.participants),
                location=scene.location,
                summary=_summary,
            )
            _st = _cls.classify(_si)
            # 2026-08-21 最终规格：不注入 cinema_infer 技术参数，【光线】只写 LLM 自然描述（lighting 基准/同上/同上+lighting_arc）
            _lighting = str(scene.lighting or "").strip()
            _lighting_arc = list(scene.lighting_arc) if scene.lighting_arc else []
            segs: list[str] = []
            t = 0.0
            for _gi, _seg in enumerate(scene.segments):
                _shots = _build_segment_shots(_seg, scene)
                if not _shots:
                    continue
                _zh, _dur = _render_segment_prompt(
                    _shots, _seg, scene, t,
                    lighting=_lighting, aspect=aspect,
                    scene_negative=list(scene.negative),
                    scene_participants=list(scene.participants),
                    lighting_arc=_lighting_arc,
                    seg_index=_gi,
                )
                segs.append(_zh)
                t += _dur
            if segs:
                out.append({
                    "ep": ep.ep,
                    "scene_id": sid,
                    "scene_type": _st,
                    "plan_text": "",
                    "image_prompts": segs,
                    "english_prompts": [],
                })
    return out

