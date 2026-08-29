"""分镜提示词：复用 storyboard 模块 + 书知识直通最终提示词。
import logging as _logging

_log = _logging.getLogger(__name__)


- 双语输出（2026-08-13 产品需求）：每镜同时输出【中文版】与【English Version】两套，分开给出、一起交付。
- 书知识直通（2026-08-13 接通）：镜头方案里的 调度(staging)/色调(tone)/剪辑(edit)/声音(sound)
  直接进入最终提示词，不再被丢弃（卡茨/大师镜头/阿里洪/声音设计/色彩学/剪辑书成果）。
- 节奏：pacing=True 时用 PacingEngine 为缺失时长的节拍定时长（动作快切 / 情绪长镜头）。
- LLM 翻译：build_shot_prompts_llm 把 画面+调度+声音+色调+剪辑 一并译为英文（导演级），失败回退本地。
- 画幅：产品决策 2026-08-13 默认横屏 16:9。
"""
from __future__ import annotations

import re

import json
import logging as _logging

from app.production.schemas import EpisodeScript, ShotPrompt
from app.production.verb_selector import select_verb
from app.storyboard.blocking import infer_blocking as _infer_blocking
from app.storyboard.dialogue_staging import (
    enrich_dialogue_shot as _enrich_dialogue_shot,
)
from app.storyboard.duration_rule import effective_duration as _eff_duration
from app.storyboard.duration_rule import (
    strip_dialogue_translation as _strip_dialogue_translation,
)
from app.storyboard.emotion_infer import infer_emotion as _infer_emotion
from app.storyboard.emotion_infer import llm_propose_emotion as _llm_propose_emotion
from app.storyboard.emotion_infer import should_llm_refine as _should_refine_emotion
from app.storyboard.emotion_infer import validate_proposal as _validate_emotion_proposal
from app.storyboard.pacing import PacingEngine
from app.storyboard.prompt_renderer import PromptRenderer
from app.storyboard.realism_style import negative_en as _realism_neg_en
from app.storyboard.realism_style import negative_zh as _realism_neg_zh
from app.storyboard.realism_style import style_lock_en as _realism_lock_en
from app.storyboard.realism_style import style_lock_zh as _realism_lock_zh
from app.storyboard.sd_manual import negative_en as _sd_manual_neg_en
from app.storyboard.sd_manual import negative_zh as _sd_manual_neg_zh
from app.storyboard.negative_selector import shot_negative as _shot_negative
from app.storyboard.shot_motivation import (
    audit_shot_motivation as _audit_motivation_internal,
)
from app.storyboard.shot_motivation import derive_motivation as _derive_motivation
from app.storyboard.shot_motivation import (
    llm_propose_motivation as _llm_propose_motivation,
)
from app.storyboard.shot_motivation import motivation_en as _motivation_en
from app.storyboard.shot_motivation import motivation_zh as _motivation_zh
from app.storyboard.shot_motivation import should_llm_refine as _should_llm_refine
from app.storyboard.shot_motivation import (
    validate_proposal as _validate_motivation_proposal,
)
from app.storyboard.speed_control import augment_sound_en as _aug_sound_en
from app.storyboard.speed_control import augment_sound_zh as _aug_sound_zh
from app.storyboard.speed_control import augment_staging_en as _aug_staging_en
from app.storyboard.speed_control import augment_staging_zh as _aug_staging_zh
from app.storyboard.speed_control import decide_speed as _decide_speed
from app.storyboard.speed_control import negative_en as _neg_en
from app.storyboard.speed_control import negative_zh as _neg_zh
from app.storyboard.speed_control import render_en as _speed_en
from app.storyboard.speed_control import render_zh as _speed_zh
from app.storyboard.visual_guard import audit as _audit_visual

_log = _logging.getLogger(__name__)
from app.storyboard.schemas import SceneInput
from app.storyboard.shot_selector import ShotSelector

STYLE_LOCK_16_9 = "写实电影质感，35mm胶片颗粒，暖调自然光，横屏16:9。"
STYLE_LOCK_9_16 = "写实电影质感，35mm胶片颗粒，暖调自然光，9:16。"
STYLE_LOCK_EN_16_9 = "cinematic film look, 35mm film grain, warm natural light, 16:9 widescreen."
STYLE_LOCK_EN_9_16 = "cinematic film look, 35mm film grain, warm natural light, vertical 9:16."
# 当前画幅（build 入口设置）：9:16 竖屏 / 16:9 横屏
_CURRENT_ASPECT: str = "16:9"


def _join_neg(*parts: str) -> str:
    """负面词拼接：两个 NOT 块之间加分隔符（；/; ），避免 '慢动作NOT 动画感' 这类无分隔拼接；
    每个块内按词保序去重（2026-08-21 修复："背景几何扭曲、透视错误、人群脸糊" 重复粘贴）。"""
    def _dedup(block: str) -> str:
        seen: set[str] = set()
        out: list[str] = []
        for token in re.split(r"[、;；]", block):  # 只按中文顿号/分号拆（英文逗号保留，防 "no deformation, no slow motion" 被拆）
            t = token.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return "、".join(out)

    joined = [_dedup(p.strip()) for p in parts if (p or "").strip()]
    if not joined:
        return ""
    result = joined[0]
    for p in joined[1:]:
        sep = "; " if "no " in result[:16] else "；"
        result = f"{result}{sep}{p}"
    # 2026-08-21 跨块中文词去重（"穿模"在 NOT 列表和"角色穿模"各出现一次）——包含去重，保序
    seen_zh: set[str] = set()
    toks = re.split(r"([、；;])", result)
    out: list[str] = []
    for t in toks:
        if re.fullmatch(r"[、；;]", t):
            out.append(t)
            continue
        if re.search(r"[\u4e00-\u9fff]", t):  # 中文词（正则判断，2026-08-21 修复字面转义失效）
            if any(s in t for s in seen_zh) or any(t in s for s in seen_zh):
                continue  # 已出现词的包含重复（角色穿模 vs 穿模）
            seen_zh.add(t)
        out.append(t)
    return "".join(out)


def _style_lock() -> str:
    r = _realism_lock_zh(_CURRENT_ASPECT)
    return r or (STYLE_LOCK_9_16 if _CURRENT_ASPECT == "9:16" else STYLE_LOCK_16_9)  # realism_style 已按 8 画幅映射


def _style_lock_en() -> str:
    r = _realism_lock_en(_CURRENT_ASPECT)
    return r or (STYLE_LOCK_EN_9_16 if _CURRENT_ASPECT == "9:16" else STYLE_LOCK_EN_16_9)  # realism_style 已按 8 画幅映射

_SCALE_EN = {
    "大远景": "extreme wide shot", "远景": "wide shot", "全景": "full shot",
    "中景": "medium shot", "中近景": "medium close-up", "近景": "close-up",
    "特写": "close-up", "大特写": "extreme close-up", "插入特写": "insert close-up",
    "过肩中近景": "over-the-shoulder medium close-up", "主观视角": "POV",
    "过肩": "over-the-shoulder", "空镜": "empty shot",
    "双人中景": "medium two-shot", "双人近景": "two-shot close-up",
}
_ANGLE_EN = {
    "平视": "eye level", "仰视": "low angle", "俯视": "high angle",
    "鸟瞰": "aerial view", "荷兰角": "Dutch angle", "过肩": "over-the-shoulder",
    "主观视角": "POV",
}
_MOVEMENT_EN = {
    "固定机位": "locked", "固定": "locked", "推": "slow push-in", "缓推": "slow push-in",
    "拉": "pull back", "横移": "lateral tracking", "跟拍": "tracking",
    "航拍": "aerial shot", "环绕": "orbit", "手持": "handheld",
    "甩镜": "whip pan", "升降": "crane", "摇": "pan", "俯仰": "tilt",
    "手持晃动": "handheld sway", "急推": "fast push-in", "急拉": "fast pull-back",
    "缓拉": "slow pull-back",
}
_STABILITY_EN = {"稳定": "stable", "稳": "stable", "固定": "locked", "手持": "handheld", "微抖": "with slight sway", "晃": "with slight sway", "随动": "following", "稳中带冲": "steady with momentum"}
_TONE_EN = {
    "冷调低饱和": "cool low-saturation", "暖调": "warm tone", "暖调低饱和": "warm low-saturation",
    "高饱和": "high saturation", "低饱和": "low saturation", "暗调": "dark tone", "高反差": "high contrast",
}

# 机位英文词表（本地兜底；生产走 LLM 翻译更完整）
_POS_EN = {
    "正面": "front of the subject", "正面机位": "front of the subject",
    "侧面": "side", "侧面过肩": "over-the-shoulder from the side", "过肩": "over-the-shoulder",
    "背后": "from behind", "背面": "from behind",
    "贴地低机位": "low to the ground", "低机位": "low camera position",
    "门缝低机位": "low at the door gap", "门框外": "outside the doorframe",
    "门框内": "inside the doorframe", "窗外": "outside the window",
    "头顶俯拍": "overhead", "斜侧": "three-quarter angle",
    "床边": "beside the bed", "床边低机位": "low beside the bed", "枕边": "by the pillow",
    "墙边": "by the wall", "树梢间": "among the treetops", "小路边": "beside the forest path",
    "林间高机位": "high among the trees", "林外高机位": "high outside the woods",
    "轿车侧方": "beside the car", "车头前": "in front of the car",
    "车窗侧方": "beside the window", "车窗高度": "at window height",
}

# LLM 字段翻译（画面+调度+声音+色调+剪辑+机位 一并翻成英文）
_FIELDS_SYSTEM = (
    "你是短剧分镜提示词翻译。把中文分镜要素译成可直接给视频生成模型的英文；"
    "动词精准、画面可执行、专有名词（人名/车名/地名）保留中文或音译、禁止截断。"
    "只输出一个 JSON 对象：{\"scene\": \"完整英文句子\", \"staging\": \"...\", "
    "\"sound\": \"...\", \"tone\": \"...\", \"edit\": \"...\", "
    "\"camera\": \"机位+景别+角度+运动的完整英文（如：low camera beside the bed, medium close-up, low angle, slow push-in）\", "
    "\"lighting\": \"物理光线（主光源方向/色温/暗光）英文，如 dim moonlight from the window, cool low-key\", "
    "\"blocking\": \"人物站位/轴线英文（如：A on the left, B on the right, face to face; keep the 180-degree axis, no left-right swap; cross-the-line=越轴; 禁止越轴跳切=do not cross the 180-degree line (never write 'jump cut')）\", "
    "\"dialogue\": \"台词英文（忠实原意，逐字翻译，不加戏）\"}，不要输出其它文字。"
)
_translation_cache: dict[str, dict] = {}
# 当前视角人物（build 入口设置；站位推理锚点 = 主动方/视角人物，居左）
_CURRENT_VIEWPOINT: str | None = None


async def _translate_fields(client, scene_zh: str, staging: str, sound: str, tone: str, edit: str,
                            camera: str = "", lighting: str = "", blocking: str = "",
                            dialogue: str = "") -> dict:
    zh = json.dumps({"scene": scene_zh, "staging": staging, "sound": sound,
                     "tone": tone, "edit": edit, "camera": camera,
                     "lighting": lighting, "blocking": blocking,
                     "dialogue": dialogue}, ensure_ascii=False)
    if zh in _translation_cache:
        return _translation_cache[zh]
    from app.storyboard.visual_guard import is_skeleton_en as _is_skeleton_en

    data: dict = {}
    for _attempt in range(3):  # 骨架回退=失败，自动重试（防 moves./clutches. 漏网）
        try:
            text = await client.chat(_FIELDS_SYSTEM, zh, json_mode=True, max_tokens=2000, temperature=0.3)
            cand = json.loads(text)
            import re as _re
            _zh_rem = _re.search(r"[\u4e00-\u9fff]", json.dumps(cand, ensure_ascii=False)) if isinstance(cand, dict) else None
            if isinstance(cand, dict) and not _is_skeleton_en(cand.get("scene", "")) and not _zh_rem:
                data = cand
                break
        except Exception:  # noqa: BLE001, S112 翻译失败回退本地，不阻塞管线
            continue
    _translation_cache[zh] = data
    return data


def _default_camera_pos(shot) -> str:
    """机位兜底标准：shot 已有机位则用之，否则按 角度/景别/运动 推导，保证每镜必有机位。"""
    if shot is None:
        return "正面机位"
    if getattr(shot, "camera_pos", ""):
        return shot.camera_pos
    angle = shot.angle or ""
    scale = shot.scale or ""
    movement = shot.movement or ""
    if "仰" in angle:
        return "低机位，仰拍"
    if "俯" in angle:
        return "高机位，俯拍"
    if "荷兰" in angle:
        return "倾斜机位"
    if "过肩" in angle:
        return "过肩机位"
    if "主观" in angle:
        return "主观机位（第一人称）"
    if "特写" in scale:
        return "贴近主体"
    if "远景" in scale or "大全" in scale:
        return "远位定场机位"
    if "跟拍" in movement or "横移" in movement:
        return "侧面跟随机位"
    return "正面平视机位"


def _blocking_text(scene, shot) -> str:
    """站位/轴线标准（2026-08-14）：本镜优先，否则继承场景基准；未给则默认防越轴约束。"""
    b = ((shot.blocking if shot else "") or (getattr(scene, "blocking", "") or "")).strip()
    if b:
        return b
    return _infer_blocking(scene, viewpoint=_CURRENT_VIEWPOINT).to_prompt()


async def _refine_motivation_llm(client, shot, scene, plan, beat, summary: str, emotion: str, index: int, total: int) -> None:
    # 情绪+动机 LLM 精修（后台数据）：原文无情绪标签且本地置信低 → LLM 补情绪并本地校验；
    # 随后用（可能精修后的）情绪重新本地推导动机；仍低置信 → LLM 提议动机，本地校验后落地；失败回退本地
    src_emotion = beat.emotion or emotion
    if not src_emotion:
        ei = _infer_emotion(beat.action or "", beat.dialogue or "", getattr(scene, "scene_type", "") or plan.scene_type)
        if _should_refine_emotion(ei):
            try:
                prop_e = await _llm_propose_emotion(
                    client, action=beat.action or "",
                    dialogue=_strip_dialogue_translation(beat.dialogue) or "",
                    scene_type=getattr(scene, "scene_type", "") or plan.scene_type)
                refined_e = _validate_emotion_proposal(prop_e)
            except Exception:  # noqa: BLE001  LLM 失败回退本地情绪，不阻塞管线
                refined_e = None
            if refined_e is not None:
                ei = refined_e
        shot.emotion_inferred = ei.emotion
        src_emotion = ei.emotion
    m = _derive_motivation(
        summary=summary, action=beat.action or "", emotion=src_emotion,
        scene_type=getattr(scene, "scene_type", "") or plan.scene_type,
        has_dialogue=bool(scene.dialogues) or bool(beat.dialogue),
        index=index, total=total, location=scene.location,
    )
    if not _should_llm_refine(m):
        shot.motivation = _motivation_zh(m)
        shot.motivation_en = _motivation_en(m)
        return
    refined = None
    try:
        prop = await _llm_propose_motivation(
            client, summary=summary, action=beat.action or "", emotion=src_emotion,
            dialogue=_strip_dialogue_translation(beat.dialogue) or "",
            scene_type=getattr(scene, "scene_type", "") or plan.scene_type, location=scene.location,
        )
        refined = _validate_motivation_proposal(prop)
    except Exception:  # noqa: BLE001  LLM 失败回退本地动机，不阻塞管线
        refined = None
    if refined is not None:
        shot.motivation = _motivation_zh(refined)
        shot.motivation_en = _motivation_en(refined)


def _lighting_text(scene, shot) -> str:
    """光线标准（2026-08-13）：本镜光线优先，否则继承场景光线基准，保证镜头间光线连续。"""
    light = ((shot.lighting if shot else "") or (getattr(scene, "lighting", "") or "")).strip()
    if light:
        return light
    tm = getattr(scene, "time", "") or ""
    if "夜" in tm or "晚" in tm:
        return "夜色环境光（以月光/烛光等剧情光源为准）"
    return "常规环境光（方向/强度以剧情为准）"


def _tone_text(scene, shot) -> str:
    """色调与光线对齐：物理光线给出时，色调跟随主光源，避免镜头间色温跳变。"""
    light = ((shot.lighting if shot else "") or (getattr(scene, "lighting", "") or "")).lower()
    if "白昼" in light or ("日" in light and "夜" not in light):
        return "高对比，暖金/冷白混合（白昼）"
    if "月光" in light or ("冷" in light and "烛" not in light):
        return "冷调低饱和，暗光（月光）"
    if "烛" in light or ("暖" in light and "月" not in light):
        return "暖调低饱和，暗光（烛光/暖光）"
    if shot and shot.tone:
        return shot.tone
    return "中性/环境色调（客观）"


def _en_camera(scale: str, angle: str, movement: str, duration: float, stability: str = "") -> str:
    s = _SCALE_EN.get(scale, scale or "medium shot")
    a = _ANGLE_EN.get(angle, angle or "eye level")
    m = _MOVEMENT_EN.get(movement, movement or "locked")
    st = _STABILITY_EN.get(stability, stability or "")
    tail = ", " + st if st else ""
    return f"Camera: {s} | {a} | {m} (approx {duration:.0f}s{tail})"


def _conjugate(verb_en: str, subject: str) -> str:
    """第三人称单数兜底（v0.1 简单规则；主语为人名/角色名时对首个单词变形）。"""
    if not subject or subject in ("我", "你", "I", "you", "we", "they", "我们", "你们", "他们"):
        return verb_en
    words = verb_en.split(" ", 1)
    head = words[0]
    tail = " " + words[1] if len(words) > 1 else ""
    if head == "be":
        head = "is"
    elif head.endswith(("s", "x", "z", "ch", "sh", "o")):
        head += "es"
    elif len(head) > 1 and head.endswith("y") and head[-2] not in "aeiou":
        head = head[:-1] + "ies"
    else:
        head += "s"
    return head + tail


def _en_scene_line(subject: str, action_zh: str, location: str, time: str, emotion: str) -> tuple[str, object]:
    vc = select_verb(action_zh)
    verb = _conjugate(vc.verb_en, subject)
    subj = subject or "The character"
    loc = location or "the scene"
    time_part = f" ({time})" if time else ""
    if subject in ("镜头", "摄影机", "Camera", "camera"):
        cam_verb, cam_prep = "moves", "toward"
        if any(k in action_zh for k in ("拉", "退")):
            cam_verb, cam_prep = "pulls back", "from"
        elif any(k in action_zh for k in ("推", "推进")):
            cam_verb, cam_prep = "pushes in", "toward"
        elif "摇" in action_zh:
            cam_verb, cam_prep = "pans", "across"
        elif any(k in action_zh for k in ("移", "横移")):
            cam_verb, cam_prep = "glides", "over"
        return f"The camera {cam_verb} {cam_prep} {loc}{time_part}", None
    if vc.family in ("移动-受伤行走", "移动-常规与速度"):
        if vc.verb_en == "stop":
            scene = f"{subj} {verb} at {loc}{time_part}"
            return scene, vc
        prep = "into"
        if any(k in action_zh for k in ("走出", "跑出", "冲出", "逃出", "离开")):
            prep = "out of"
        elif any(k in action_zh for k in ("走向", "朝", "向")):
            prep = "toward"
        elif any(k in action_zh for k in ("穿过", "横穿")):
            prep = "across"
        elif any(k in action_zh for k in ("沿", "沿着")):
            prep = "along"
        scene = f"{subj} {verb} {prep} {loc}{time_part}"
    else:
        scene = f"{subj} {verb}. Location: {loc}{time_part}"
    return scene, vc


def _seg_label(start: float, end: float) -> str:
    return f"{start:.0f}-{end:.0f}秒"


def _seg_label_en(start: float, end: float) -> str:
    return f"[{start:.0f}-{end:.0f}s]"


def _zh_fields_line(subject: str, action: str, location: str, time: str, emotion: str) -> str:
    return f"主体{subject or '角色'}：{action}；地点：{location}；时间：{time}；情绪：{emotion}"


def _eff_dur(beat, shot) -> float:
    """镜总时长 = 视觉时长 + 对白时长 + 旁白时长（台词占时长机制）。"""
    visual = beat.duration_sec or (shot.duration_sec if shot else 3.0)
    return _eff_duration(visual, beat.dialogue or "", (shot.sound if shot else "") or "")


def _apply_pacing(engine: PacingEngine, scene, target_sec: float = 15.0) -> None:
    """节奏引擎：节拍全缺时长时按场景张力定时长（有显式时长则尊重生成结果）。"""
    beats = scene.beats
    if not beats or any(b.duration_sec for b in beats):
        return
    durs = engine.recommend_rhythm(scene, target_sec=target_sec, n_shots=len(beats))
    for b, d in zip(beats, durs):
        b.duration_sec = d


def build_video_prompt(scene, shot, emotion: str, start_sec: float = 0.0, speed: dict | None = None,
                       *, scene_type: str = "", genre: str = "") -> str:
    """中文版：含 调度/色调/剪辑/声音（书知识直通）。负面按 scene_type×scale×genre 自适应。"""
    dur = shot.duration_sec
    acts = "；".join(scene.action_blocks[:3])
    dlg = "；".join(_strip_dialogue_translation(f"{d.speaker}：{d.line}") for d in scene.dialogues[:3])
    _spd = speed or _decide_speed(acts, emotion or shot.emotion)
    lines = [
        f"【风格锁定】{_style_lock()}",
        f"【镜头】机位：{_default_camera_pos(shot)}｜{shot.scale}｜{shot.angle}｜{shot.movement}（约{dur:.0f}s，稳定）",
        f"【{_seg_label(start_sec, start_sec + dur)}】",
    ]
    if acts:
        lines.append(f"【画面】{acts}")
    else:
        lines.append(f"【画面】{shot.content}")
    if dlg:
        lines.append(f"【对白】{dlg}")
    lines.append(f"【站位·轴线】{_blocking_text(scene, shot)}")
    if shot.staging:
        lines.append(f"【调度】{_aug_staging_zh(shot.staging, _spd)}")
    lines.append(f"【色调】{_tone_text(scene, shot)}")
    _lt = _lighting_text(scene, shot)
    if _lt:
        lines.append(f"【光线】{_lt}")
    if shot.edit:
        lines.append(f"【剪辑】{shot.edit}")
    lines.append(f"【声音】{_aug_sound_zh(shot.sound or '环境音+对白优先；音乐按情绪起伏', _spd)}")
    lines.append(f"【速度】{_speed_zh(_spd)}")
    lines.append(f"【情绪】{emotion or shot.emotion or '中性'}")
    lines.append(f"【负面】{_shot_negative(scene_type, shot.scale, _spd, genre)}")
    return "\n".join(lines)


def build_en_video_prompt(scene, shot, emotion: str, start_sec: float = 0.0,
                          fields_en: dict | None = None, speed: dict | None = None) -> str:
    """英文版（本地兜底或 LLM 字段译文）。"""
    dur = shot.duration_sec
    acts = "；".join(scene.action_blocks[:3])
    _spd = speed or _decide_speed(acts, emotion or shot.emotion)
    fe = fields_en or {}
    if fe.get("scene"):
        scene_line = fe["scene"]
    else:
        scene_line, _vc = _en_scene_line("", acts, scene.location, scene.time, emotion or shot.emotion)
    if fe.get("camera"):
        cam = f"Camera: {fe['camera']}"
    else:
        cam = _en_camera(shot.scale, shot.angle, shot.movement, dur, shot.stability)
        pos = _default_camera_pos(shot)
        cam += f" | position: {_POS_EN.get(pos, pos)}"
    lines = [
        f"【Style】{_style_lock_en()}",
        cam,
        _seg_label_en(start_sec, start_sec + dur),
        f"Scene: {scene_line.rstrip('.').rstrip()}.",
    ]
    dlg_en = "；".join(_en_dialogue_line(f"{d.speaker}：{d.line}") for d in scene.dialogues[:3])
    if dlg_en:
        lines.append(f"Dialogue: {dlg_en}")
    fe = fields_en or {}
    staging = fe.get("staging") or shot.staging or ""
    tone = fe.get("tone") or _tone_text(scene, shot) or ""
    edit = fe.get("edit") or shot.edit or ""
    sound = fe.get("sound") or shot.sound or "ambient + dialogue priority; music follows the mood"
    light_en = fe.get("lighting") or (shot.lighting or scene.lighting or "")
    blocking_en = _fix_blocking_en(fe.get("blocking") or _blocking_text(scene, shot))
    if staging:
        lines.append(f"Staging: {_aug_staging_en(staging, _spd)}")
    if blocking_en:
        lines.append(f"Blocking: {blocking_en}")
    if tone:
        lines.append(f"Tone: {tone}")
    if light_en:
        lines.append(f"Lighting: {light_en}")
    if edit:
        lines.append(f"Edit: {edit}")
    lines.append(f"Sound: {_aug_sound_en(sound, _spd)}")
    lines.append(f"Mood: {emotion or shot.emotion or 'neutral'}")
    lines.append(f"Speed: {_speed_en(_spd)}")
    lines.append(f"Constraints: {_join_neg(_neg_en(_spd), _realism_neg_en(), _sd_manual_neg_en())}")
    return "\n".join(lines)


def build_video_prompt_from_beat(scene, beat, shot, emotion: str, start_sec: float = 0.0,
                               speed: dict | None = None, *, scene_type: str = "", genre: str = "") -> str:
    """中文版（节拍）：含 调度/色调/剪辑/声音。负面按 scene_type×scale×genre 自适应。"""
    dur = _eff_dur(beat, shot)
    # 旁白镜（无对白、声音含旁白·原文）：画面是旁白伴随视觉，非动作戏——强制实时，禁止动作升格/打击音效
    _is_vo_shot = bool(shot and "旁白·原文" in (shot.sound or "")) and not (beat.dialogue or "")
    _spd = speed or _decide_speed(("" if _is_vo_shot else beat.action) or "", beat.emotion or emotion)
    if shot:
        cam = f"{shot.scale}｜{shot.angle}｜{shot.movement}（约{dur:.0f}s，稳定）"
    else:
        cam = f"近景｜平视｜固定机位（约{dur:.0f}s，稳定）"
    lines = [
        f"【风格锁定】{_style_lock()}",
        f"【镜头】机位：{_default_camera_pos(shot)}｜{cam}",
        f"【{_seg_label(start_sec, start_sec + dur)}】",
        f"【画面】{beat.action or shot.content if shot else ''}",
    ]
    if beat.dialogue:
        lines.append(f"【对白】{_strip_dialogue_translation(beat.dialogue)}")
    if shot:
        lines.append(f"【站位·轴线】{_blocking_text(scene, shot)}")
        if shot.staging:
            lines.append(f"【调度】{_aug_staging_zh(shot.staging, _spd)}")
        lines.append(f"【色调】{_tone_text(scene, shot)}")
        _lt = _lighting_text(scene, shot)
        if _lt:
            lines.append(f"【光线】{_lt}")
        if shot.edit:
            lines.append(f"【剪辑】{shot.edit}")
        lines.append(f"【声音】{_aug_sound_zh(shot.sound or '环境音+对白优先；音乐按情绪起伏', _spd)}")
    else:
        lines.append("【声音】环境音+对白优先；音乐按情绪起伏")
    lines.append(f"【速度】{_speed_zh(_spd)}")
    lines.append(f"【情绪】{beat.emotion or emotion or '中性'}")
    lines.append(f"【负面】{_shot_negative(scene_type, shot.scale, _spd, genre)}")
    return "\n".join(lines)


_SPEAKER_EN = {"主礼人": "the Grand Officiant", "罗伊娜": "Rowena", "罗伊娜OS": "Rowena (V.O.)",
               "珍妮芙": "Jennifer", "伊索尔德": "Isolde", "伊索尔": "Isolde",
               "贵族1": "Nobleman 1", "贵族2": "Nobleman 2"}


def _fix_blocking_en(s: str) -> str:
    """英文站位/轴线措辞纠错：越轴统一为 cross the 180-degree line，禁止出现 jump cut（跳切）。"""
    if not s:
        return s
    return (s.replace("jump cut across the axis", "do not cross the 180-degree line")
             .replace("no jump cut", "do not cross the 180-degree line")
             .replace("jump cut", "do not cross the 180-degree line"))


def _en_dialogue_line(beat_dialogue: str) -> str:
    """英文版对白：台词正文逐字忠实原文（不用 LLM 意译），说话人/OS 翻译成英文。"""
    base = _strip_dialogue_translation(beat_dialogue or "")
    if not base:
        return ""
    speaker, line = base, ""
    for sep in ("：", ":"):
        if sep in base:
            speaker, line = base.split(sep, 1)
            break
    if not line:
        return base
    sp = _SPEAKER_EN.get(speaker.split("（")[0].strip(), speaker)
    return f"{sp}: {line.strip()}"


def build_en_video_prompt_from_beat(scene, beat, shot, emotion: str, start_sec: float = 0.0,
                                    fields_en: dict | None = None, speed: dict | None = None) -> str:
    """英文版（节拍）：本地兜底或 LLM 字段译文。"""
    dur = _eff_dur(beat, shot)
    # 旁白镜（无对白、声音含旁白·原文）：画面是旁白伴随视觉，非动作戏——强制实时，禁止动作升格/打击音效
    _is_vo_shot = bool(shot and "旁白·原文" in (shot.sound or "")) and not (beat.dialogue or "")
    _spd = speed or _decide_speed(("" if _is_vo_shot else beat.action) or "", beat.emotion or emotion)
    fe = fields_en or {}
    if fe.get("camera"):
        cam = f"Camera: {fe['camera']}"
    elif shot:
        cam = _en_camera(shot.scale, shot.angle, shot.movement, dur, shot.stability)
        pos = _default_camera_pos(shot)
        cam += f" | position: {_POS_EN.get(pos, pos)}"
    else:
        cam = f"Camera: medium shot | eye level | locked (approx {dur:.0f}s, stable)"
    if fe.get("scene"):
        scene_line = fe["scene"]
    else:
        scene_line, _vc = _en_scene_line(
            beat.subject or "", beat.action or (shot.content if shot else ""),
            scene.location, scene.time, beat.emotion or emotion,
        )
    lines = [
        f"【Style】{_style_lock_en()}",
        cam,
        _seg_label_en(start_sec, start_sec + dur),
        f"Scene: {scene_line.rstrip('.').rstrip()}.",
    ]
    if beat.dialogue:
        lines.append(f"Dialogue: {_en_dialogue_line(beat.dialogue)}")
    staging = fe.get("staging") or (shot.staging if shot else "") or ""
    tone = fe.get("tone") or _tone_text(scene, shot) or ""
    edit = fe.get("edit") or (shot.edit if shot else "") or ""
    sound = fe.get("sound") or (shot.sound if shot else "") or "ambient + dialogue priority; music follows the mood"
    light_en = fe.get("lighting") or ((shot.lighting if shot else "") or scene.lighting or "")
    blocking_en = _fix_blocking_en(fe.get("blocking") or _blocking_text(scene, shot))
    if staging:
        lines.append(f"Staging: {_aug_staging_en(staging, _spd)}")
    if blocking_en:
        lines.append(f"Blocking: {blocking_en}")
    if tone:
        lines.append(f"Tone: {tone}")
    if light_en:
        lines.append(f"Lighting: {light_en}")
    if edit:
        lines.append(f"Edit: {edit}")
    lines.append(f"Sound: {_aug_sound_en(sound, _spd)}")
    lines.append(f"Mood: {beat.emotion or emotion or 'neutral'}")
    lines.append(f"Speed: {_speed_en(_spd)}")
    lines.append(f"Constraints: {_join_neg(_neg_en(_spd), _realism_neg_en(), _sd_manual_neg_en())}")
    return "\n".join(lines)


def _beat_dur(beat, shots: list, j: int) -> float:
    if beat.duration_sec:
        return beat.duration_sec
    if shots:
        return shots[j % len(shots)].duration_sec
    return 3.0


async def build_shot_prompts_llm(episodes: list[EpisodeScript], client, *, global_timeline: bool = False,
                                 pacing: bool = False, viewpoint: str | None = None,
                                 aspect: str = "16:9", genre: str = "") -> list[ShotPrompt]:
    """生产用：双语输出 + 书知识字段 + LLM 导演级翻译 + 可选节奏填充 + 站位推理视角人物 + 画幅。"""
    global _CURRENT_VIEWPOINT, _CURRENT_ASPECT
    _CURRENT_VIEWPOINT = viewpoint
    _CURRENT_ASPECT = aspect
    selector = ShotSelector()
    renderer = PromptRenderer()
    engine = PacingEngine() if pacing else None
    out: list[ShotPrompt] = []
    t_global = 0.0
    for ep in episodes:
        for i, scene in enumerate(ep.scenes, 1):
            if engine is not None:
                _apply_pacing(engine, scene, target_sec=15.0)
            summary_parts = list(scene.action_blocks)
            lines = [f"{d.speaker}：{d.line}" for d in scene.dialogues[:4]]
            if lines:
                summary_parts.append("对白：" + "；".join(lines))
            summary = " ".join(x for x in [scene.time, scene.location, *summary_parts] if x).strip()
            emotion = next((d.emotion for d in scene.dialogues if d.emotion), "")
            participants = [d.speaker for d in scene.dialogues]
            si = SceneInput(
                scene_id=f"e{ep.ep}_s{i}",
                scene_type="",
                emotion=emotion,
                participants=participants,
                location=scene.location,
                summary=summary,
            )
            plan = selector.select(si)
            plan_text = renderer.render(plan)
            if scene.beats:
                image_prompts: list[str] = []
                english_prompts: list[str] = []
                t = t_global if global_timeline else 0.0
                focus_seq_counts: dict[str, int] = {}
                prev_motions: list[str] = []
                for j, beat in enumerate(scene.beats):
                    dur = beat.duration_sec or 3.0
                    for _w in _audit_visual(beat.action or ""):
                        _log.warning("画面守卫 %s: %s", (beat.action or "")[:30], _w)
                    # 正反打：镜头目标=本镜说话人（beat.subject）放首位，机位/过肩按说话人推导
                    _focus_key = beat.focus or ""
                    _focus_seq = focus_seq_counts.get(_focus_key, 0)
                    focus_seq_counts[_focus_key] = _focus_seq + 1
                    _spk_first = ([beat.subject] + [x for x in scene.participants if x != beat.subject]) \
                        if beat.subject else (scene.participants or [])
                    shot = selector.select_beat(SceneInput(
                        scene_id=f"e{ep.ep}_s{i}",
                        scene_type="",
                        emotion=beat.emotion or emotion,
                        participants=_spk_first,
                        location=scene.location,
                        summary=f"{beat.action or ''} {beat.emotion or ''}",
                        duration_hint_sec=beat.duration_sec,
                    ), index=j, total=len(scene.beats),
                        scene_summary=summary, has_dialogue=bool(scene.dialogues), beat_dialogue=beat.dialogue,
                        beat_angle_idx=beat.angle_idx, dialogue_turn_idx=beat.dialogue_turn_idx,
                        focus=_focus_key, motion=beat.motion or "", focus_seq=_focus_seq,
                        prev_motions=prev_motions)
                    # 分镜设计覆盖：节拍显式给出 景别/角度/运动/调度 时，以人工设计为准
                    if beat.scale:
                        shot.scale = beat.scale
                    if beat.angle:
                        shot.angle = beat.angle
                    if beat.movement:
                        shot.movement = beat.movement
                    if beat.staging:
                        shot.staging = beat.staging
                    if beat.camera_pos:
                        shot.camera_pos = beat.camera_pos
                    if beat.sound:
                        shot.sound = beat.sound
                    if beat.lighting:
                        shot.lighting = beat.lighting
                    if beat.blocking:
                        shot.blocking = beat.blocking
                    if beat.dialogue:
                        _enrich_dialogue_shot(shot, beat, emotion)
                    prev_motions = (prev_motions + [selector.movement_id(shot.movement)])[-2:]
                    await _refine_motivation_llm(client, shot, scene, plan, beat, summary, emotion, j, len(scene.beats))
                    for _w in _audit_motivation_internal(shot):
                        _log.warning("镜头动机(后台) %s: %s", (beat.action or "")[:30], _w)
                    img = build_video_prompt_from_beat(scene, beat, shot, emotion, start_sec=t, scene_type=plan.scene_type, genre=genre)
                    zh = _zh_fields_line(beat.subject, beat.action or (shot.content if shot else ""),
                                         scene.location, scene.time, beat.emotion or emotion)
                    fields_en = await _translate_fields(
                        client, zh,
                        shot.staging if shot else "", shot.sound if shot else "",
                        shot.tone if shot else "", shot.edit if shot else "",
                        camera=shot.camera_pos if shot else "",
                        lighting=(shot.lighting if shot else "") or scene.lighting or "",
                        blocking=(shot.blocking if shot else "") or scene.blocking or "",
                        dialogue=_strip_dialogue_translation(beat.dialogue) or "",
                    )
                    en = build_en_video_prompt_from_beat(scene, beat, shot, emotion, start_sec=t, fields_en=fields_en)
                    if j < len(scene.beats) - 1:
                        img += "\n【硬切】"
                        en += "\nHARD CUT"
                    image_prompts.append(img)
                    english_prompts.append(en)
                    dur = _eff_dur(beat, shot)
                    t += dur
            else:
                image_prompts = []
                english_prompts = []
                t = t_global if global_timeline else 0.0
                for j, s in enumerate(plan.shots):
                    img = build_video_prompt(scene, s, emotion, start_sec=t, scene_type=plan.scene_type, genre=genre)
                    zh = _zh_fields_line("", "；".join(scene.action_blocks[:3]), scene.location, scene.time, emotion)
                    fields_en = await _translate_fields(
                        client, zh, s.staging, s.sound, s.tone, s.edit,
                        camera=s.camera_pos, lighting=s.lighting or scene.lighting or "",
                        blocking=s.blocking or scene.blocking or "")
                    en = build_en_video_prompt(scene, s, emotion, start_sec=t, fields_en=fields_en)
                    if j < len(plan.shots) - 1:
                        img += "\n【硬切】"
                        en += "\nHARD CUT"
                    image_prompts.append(img)
                    english_prompts.append(en)
                    t += s.duration_sec
            if global_timeline:
                t_global = t
            out.append(ShotPrompt(ep=ep.ep, scene_id=si.scene_id, scene_type=plan.scene_type,
                                  plan_text=plan_text, image_prompts=image_prompts,
                                  english_prompts=english_prompts))
    return out


def build_shot_prompts(episodes: list[EpisodeScript], *, global_timeline: bool = False,
                       pacing: bool = False, viewpoint: str | None = None,
                       aspect: str = "16:9", genre: str = "") -> list[ShotPrompt]:
    """离线/测试用：同步构建（英文版本地兜底）+ 站位推理视角人物 + 画幅。"""
    global _CURRENT_VIEWPOINT, _CURRENT_ASPECT
    _CURRENT_VIEWPOINT = viewpoint
    _CURRENT_ASPECT = aspect
    selector = ShotSelector()
    renderer = PromptRenderer()
    engine = PacingEngine() if pacing else None
    out: list[ShotPrompt] = []
    t_global = 0.0
    for ep in episodes:
        for i, scene in enumerate(ep.scenes, 1):
            if engine is not None:
                _apply_pacing(engine, scene, target_sec=15.0)
            summary_parts = list(scene.action_blocks)
            lines = [f"{d.speaker}：{d.line}" for d in scene.dialogues[:4]]
            if lines:
                summary_parts.append("对白：" + "；".join(lines))
            summary = " ".join(x for x in [scene.time, scene.location, *summary_parts] if x).strip()
            emotion = next((d.emotion for d in scene.dialogues if d.emotion), "")
            participants = [d.speaker for d in scene.dialogues]
            si = SceneInput(
                scene_id=f"e{ep.ep}_s{i}",
                scene_type="",
                emotion=emotion,
                participants=participants,
                location=scene.location,
                summary=summary,
            )
            plan = selector.select(si)
            plan_text = renderer.render(plan)
            if scene.beats:
                image_prompts: list[str] = []
                english_prompts: list[str] = []
                t = t_global if global_timeline else 0.0
                focus_seq_counts: dict[str, int] = {}
                prev_motions: list[str] = []
                for j, beat in enumerate(scene.beats):
                    dur = beat.duration_sec or 3.0
                    for _w in _audit_visual(beat.action or ""):
                        _log.warning("画面守卫 %s: %s", (beat.action or "")[:30], _w)
                    # 正反打：镜头目标=本镜说话人（beat.subject）放首位，机位/过肩按说话人推导
                    _focus_key = beat.focus or ""
                    _focus_seq = focus_seq_counts.get(_focus_key, 0)
                    focus_seq_counts[_focus_key] = _focus_seq + 1
                    _spk_first = ([beat.subject] + [x for x in scene.participants if x != beat.subject]) \
                        if beat.subject else (scene.participants or [])
                    shot = selector.select_beat(SceneInput(
                        scene_id=f"e{ep.ep}_s{i}",
                        scene_type="",
                        emotion=beat.emotion or emotion,
                        participants=_spk_first,
                        location=scene.location,
                        summary=f"{beat.action or ''} {beat.emotion or ''}",
                        duration_hint_sec=beat.duration_sec,
                    ), index=j, total=len(scene.beats),
                        scene_summary=summary, has_dialogue=bool(scene.dialogues), beat_dialogue=beat.dialogue,
                        beat_angle_idx=beat.angle_idx, dialogue_turn_idx=beat.dialogue_turn_idx,
                        focus=_focus_key, motion=beat.motion or "", focus_seq=_focus_seq,
                        prev_motions=prev_motions)
                    # 分镜设计覆盖：节拍显式给出 景别/角度/运动/调度 时，以人工设计为准
                    if beat.scale:
                        shot.scale = beat.scale
                    if beat.angle:
                        shot.angle = beat.angle
                    if beat.movement:
                        shot.movement = beat.movement
                    if beat.staging:
                        shot.staging = beat.staging
                    if beat.camera_pos:
                        shot.camera_pos = beat.camera_pos
                    if beat.sound:
                        shot.sound = beat.sound
                    if beat.lighting:
                        shot.lighting = beat.lighting
                    if beat.blocking:
                        shot.blocking = beat.blocking
                    if beat.dialogue:
                        _enrich_dialogue_shot(shot, beat, emotion)
                    prev_motions = (prev_motions + [selector.movement_id(shot.movement)])[-2:]
                    for _w in _audit_motivation_internal(shot):
                        _log.warning("镜头动机(后台) %s: %s", (beat.action or "")[:30], _w)
                    img = build_video_prompt_from_beat(scene, beat, shot, emotion, start_sec=t, scene_type=plan.scene_type, genre=genre)
                    en = build_en_video_prompt_from_beat(scene, beat, shot, emotion, start_sec=t)
                    if j < len(scene.beats) - 1:
                        img += "\n【硬切】"
                        en += "\nHARD CUT"
                    image_prompts.append(img)
                    english_prompts.append(en)
                    dur = _eff_dur(beat, shot)
                    t += dur
            else:
                image_prompts = []
                english_prompts = []
                t = t_global if global_timeline else 0.0
                for j, s in enumerate(plan.shots):
                    img = build_video_prompt(scene, s, emotion, start_sec=t, scene_type=plan.scene_type, genre=genre)
                    en = build_en_video_prompt(scene, s, emotion, start_sec=t)
                    if j < len(plan.shots) - 1:
                        img += "\n【硬切】"
                        en += "\nHARD CUT"
                    image_prompts.append(img)
                    english_prompts.append(en)
                    t += s.duration_sec
            if global_timeline:
                t_global = t
            out.append(ShotPrompt(ep=ep.ep, scene_id=si.scene_id, scene_type=plan.scene_type,
                                  plan_text=plan_text, image_prompts=image_prompts,
                                  english_prompts=english_prompts))
    return out
