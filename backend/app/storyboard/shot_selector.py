"""镜头方案选择器（本地规则，确定性）。

从 shot_language 配置包的 scene_types 中取默认方案/变体，展开为 ShotPlan：
景别/角度/运动/时长/内容填充。
时长按场景 pace_sec 从宽到紧插值（情绪升级收节奏）；v0.1 单镜取中值。
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.paths import data_root
from app.storyboard.emotion_infer import infer_emotion
from app.storyboard.loader import load_shot_language
from app.storyboard.motion_guard import load_motion_guard
from app.storyboard.scene_classifier import SceneClassifier
from app.storyboard.schemas import SceneInput, Shot, ShotPlan
from app.storyboard.shot_motivation import (
    derive_motivation,
    motivation_en,
    motivation_zh,
)

# 变体命中关键词（骨架：情绪/氛围 -> 变体，随学习迭代扩充）
VARIANT_TRIGGERS: dict[str, list[str]] = {
    "对峙": ["对峙", "僵持", "仇视", "剑拔弩张", "威胁", "质问"],
    "亲密": ["亲密", "拥抱", "亲吻", "温柔", "告白", "依偎"],
    "多人": ["众人", "大家", "在场", "围", "群", "多人"],
    "追逐": ["追", "逃", "奔跑", "追逐", "追赶"],
    "力量型": ["沉重", "力量", "一拳", "重击", "碾压"],
    "仙侠": ["仙", "法术", "飞剑", "腾空", "轻功", "灵力"],
    "宏大": ["宏大", "壮观", "浩瀚", "辽阔", "大军", "全城"],
    "压迫": ["压迫", "压抑", "阴森", "囚", "笼"],
    "温馨": ["温馨", "温暖", "日常", "团圆", "和睦"],
    "极致悲痛": ["悲痛", "崩溃", "心碎", "痛哭", "绝望"],
    "狂喜": ["狂喜", "欣喜若狂", "欢呼", "大喜"],
    "隐忍": ["隐忍", "克制", "忍住", "沉默"],
}


# 节拍级镜头覆盖（2026-08-13 接通：镜头方案按节拍内容对齐，而非场景整体循环分配）
# 运动覆盖：命中即改用对应运动 id（movements 字典键）
_BEAT_MOVE_OVERRIDES = [
    (("缓缓拉", "向后拉", "后拉", "拉远", "退出", "拉出"), "pull_slow"),
    (("急拉",), "pull_fast"),
    (("推进", "推近", "推镜"), "push_slow"),
    (("急推",), "push_fast"),
    (("环绕", "绕"), "orbit"),
    (("摇",), "pan"),
    (("升", "降"), "crane"),
    (("撞", "猛", "打斗", "厮打", "扭打", "攻击", "撕", "推", "倒下", "倒向"), "handheld"),
    (("追", "跑", "逃", "飞奔", "追逐"), "track"),
]


# ---- 技能规则接入（2026-08-24，数据驱动，见 docs/技能接入shot_selector设计.md）----
_MOVEMENT_KEYS = frozenset(("static", "push_slow", "push_fast", "pull_slow", "pull_fast",
                            "orbit", "pan", "crane", "handheld", "track", "aerial"))


@lru_cache(maxsize=1)
def _skill_rules() -> dict:
    p = data_root() / "config" / "storyboard" / "skill_rules.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _skill_move_rules() -> list[tuple[tuple[str, ...], str]]:
    """技能 decision（scope=movement）→ move_rules 补充（命中 trigger 即覆盖运动）。"""
    out = []
    for r in _skill_rules().get("decision_rules", []):
        if r.get("scope") == "movement" and r.get("decision") in _MOVEMENT_KEYS:
            trigs = tuple(t for t in (r.get("triggers") or []) if t)
            if trigs:
                out.append((trigs, r["decision"]))
    return out


def _parse_move_rules(entries) -> list:
    """把配置里的平铺条目（关键词... + 末尾运动id）解析为 [(keys...), mv] 列表。"""
    out: list = []
    for e in entries or []:
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            out.append((tuple(e[:-1]), e[-1]))
    return out
# 景别按场景类型递进（激烈场景逐镜收紧：全景→中景→中近景→特写；情绪戏直入近景/特写）
_SCALE_BY_TYPE = {
    "action": ["full", "medium", "medium_close", "closeup"],
    "dialogue": ["medium", "medium_close", "closeup"],
    "emotion": ["medium_close", "closeup", "big_closeup"],
    "suspense": ["medium", "medium_close", "closeup"],
    "reveal": ["medium_close", "closeup", "big_closeup"],
    "transition": ["full", "long"],
    "fantasy": ["medium", "medium_close"],
    "establishing": ["long", "full", "medium"],
}
# 景别覆盖：特写倾向 / 全景倾向
_BEAT_SCALE_CLOSE = ("瞪", "眼神", "泪", "表情", "特写", "颤抖", "捏", "细节", "伤口", "血迹", "戒指", "怀表", "脸", "手", "喘", "刺入", "贯穿", "剑尖", "指尖", "睫毛", "喉结", "嘴角", "瞳孔", "血珠", "拔剑", "拔下", "绷紧", "咬牙")
_BEAT_SCALE_WIDE = ("矗立", "树林", "森林", "小木屋", "环境", "空镜", "城市", "旷野", "空地", "全景", "远", "山谷", "城堡", "拉远", "退出", "后拉", "向后拉", "倒地", "抛起", "抛飞", "整个", "人群", "分列", "穹顶", "广场", "扫场", "大厅")
# 角度兜底触发（节拍内容 -> 角度；缺省平视）
_BEAT_ANGLE_OVERRIDES: dict[str, tuple[str, ...]] = {
    "low": ("仰头", "仰望", "抬头看", "高塔", "压迫", "跪", "匍匐", "仰视"),
    "high": ("俯瞰", "俯视", "鸟瞰", "高机位", "从上方", "屋顶", "全城", "山谷", "低头看"),
    "dutch": ("荷兰角", "倾斜", "失衡", "不安"),
    "over_shoulder": ("耳语", "贴耳", "过肩", "对峙", "质问", "凑近", "俯身低语", "窃窃私语"),
    "pov": ("主观", "第一人称", "窥视", "透过"),
}

# focus → 允许的运动显示名（2026-08-21 批次4：减少 LLM 随机；冲突按此回退）
_FOCUS_MOTION_ALLOW: dict[str, tuple[str, ...]] = {
    "反应情绪": ("固定", "缓推"),
    "细节": ("固定", "缓推"),
    "环境": ("缓推", "固定"),
}


class ShotSelector:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or load_shot_language()

    def _beat_group(self, text: str) -> dict | None:
        """动作块内容 → 命中角度组（配置 beat_angle_groups，顺序优先；无命中返回 None）。"""
        for grp in self.data.get("beat_angle_groups", []) or []:
            if any(k in (text or "") for k in (grp.get("keywords", []) or [])):
                return grp
        return None

    def beat_group_size(self, text: str) -> int:
        """动作块 → 镜头数（引擎驱动：命中角度组=组内角度数；无命中=默认组角度数）。"""
        grp = self._beat_group(text)
        if grp:
            return len(grp.get("angles", []) or [])
        return len((self.data.get("beat_angle_default", {}) or {}).get("angles", []) or [])

    @staticmethod
    def _motion_compat_token(motion: str) -> str | None:
        """把运动显示名归一到 motion_compat 的兼容短名（固定/缓推/横移/跟拍/航拍）。"""
        m = (motion or "").strip()
        if not m:
            return None
        for token in ("固定", "缓推", "横移", "跟拍", "航拍"):
            if token in m:
                return token
        return None

    def _is_known_movement(self, motion: str) -> bool:
        """LLM motion 必须在 movements 表中可识别（按 name 或 key 匹配，兼容短名固定/固定机位）。"""
        m = (motion or "").strip()
        if not m:
            return False
        for key, v in (self.data.get("movements", {}) or {}).items():
            name = str(v.get("name") or "").strip()
            if m == key or m == name or (name and (m in name or name in m)):
                return True
        return self._motion_compat_token(m) is not None

    def _movement_stability(self, motion: str) -> str:
        m = (motion or "").strip()
        for v in (self.data.get("movements", {}) or {}).values():
            name = str(v.get("name") or "").strip()
            if name and (m == name or m in name or name in m):
                return str(v.get("stability") or "")
        if "固定" in m:
            return str((self.data.get("movements", {}) or {}).get("static", {}).get("stability") or "稳")
        return ""

    def _focus_compat_list(self, scale: str) -> list[str]:
        mc = self.data.get("motion_compat", {}) or {}
        if scale in mc:
            return list(mc[scale])
        # 仅补齐 focus_map 可能产出但未在 motion_compat 显式列出的同义景别；不新增规格外词表
        if scale == "大特写":
            return list(mc.get("特写", []))
        if scale == "大远景":
            return list(mc.get("远景", []))
        return []

    def _resolve_focus_motion(self, focus: str, llm_motion: str, scale: str, default_motion: str, occ: int) -> str:
        """focus 运动裁决：focus 允许集合优先；LLM motion 合法且与景别兼容→采纳，否则回退安全缺省。"""
        compat = self._focus_compat_list(scale)
        allowed = _FOCUS_MOTION_ALLOW.get(focus) if focus else None
        if allowed:
            llm_token = self._motion_compat_token(llm_motion) if llm_motion else None
            if llm_motion and self._is_known_movement(llm_motion) and llm_token in allowed:
                if not compat or llm_token in compat:
                    return llm_motion
            candidates = [m for m in allowed if not compat or self._motion_compat_token(m) in compat]
            if candidates:
                return candidates[occ % len(candidates)]
            return default_motion
        if llm_motion and self._is_known_movement(llm_motion):
            if self._motion_compat_token(llm_motion) in compat:
                return llm_motion
        if compat:
            motion = compat[occ % len(compat)]
        else:
            motion = default_motion
        # 兼容性硬拦：最终仍违反则强制轮换值（如近景+横移）
        if compat and self._motion_compat_token(motion) not in compat:
            motion = compat[occ % len(compat)]
        return motion

    _STATIC_CLASS_IDS = ("static", "push_slow", "pull_slow")
    _RHYTHM_PREFER_IDS = ("track", "handheld", "push_fast", "pan", "orbit", "tilt", "crane",
                          "whip_pan", "fpv_drone", "aerial", "pull_fast", "steadicam")

    def _movement_key(self, motion: str) -> str:
        """把运动显示名/兼容短名/LLM 提议归一到 movements 的 id（固定→static，缓推→push_slow）。"""
        m = (motion or "").strip()
        if not m:
            return ""
        for key, v in (self.data.get("movements", {}) or {}).items():
            name = str(v.get("name") or "").strip()
            if m == key or m == name or (name and (m in name or name in m)):
                return key
        for token, key in (("固定", "static"), ("缓推", "push_slow"), ("急推", "push_fast"),
                           ("缓拉", "pull_slow"), ("急拉", "pull_fast"), ("横移", "pan"),
                           ("摇", "tilt"), ("跟拍", "track"), ("升降", "crane"),
                           ("环绕", "orbit"), ("甩镜", "whip_pan"), ("手持晃动", "handheld"),
                           ("斯坦尼康", "steadicam"), ("航拍", "aerial"), ("穿越机", "fpv_drone")):
            if token in m:
                return key
        return ""

    def movement_id(self, motion: str) -> str:
        """运动显示名/LLM 提议 → movements 表中的运动 id（供调用方维护 prev_motions）。"""
        return self._movement_key(motion)

    def _compat_motion_ids(self, scale: str) -> list[str]:
        """按景别把 motion_compat/focus 兼容集合转换为 movements 运动 id 列表。"""
        ids: list[str] = []
        for token in self._focus_compat_list(scale):
            key = self._movement_key(token)
            if key and key not in ids:
                ids.append(key)
        return ids

    def _motion_rhythm_adjust(self, movement_id: str, scale: str, prev_motions: list[str] | None) -> str:
        """连续节奏约束：连续 3 镜同为静态类（static/push_slow/pull_slow）时，
        从景别兼容集合中换一个不同运动（优先非静态）；兼容集合仅静态项时在集合内轮换。"""
        if not movement_id or not prev_motions or len(prev_motions) < 2:
            return movement_id
        prev = [self._movement_key(x) for x in prev_motions[-2:]]
        if not prev[0] or not prev[1]:
            return movement_id
        if movement_id != prev[-1] or movement_id != prev[-2]:
            return movement_id
        if movement_id not in self._STATIC_CLASS_IDS:
            return movement_id
        compat = self._compat_motion_ids(scale)
        if not compat:
            return movement_id
        candidates = [x for x in compat if x != movement_id]
        if not candidates:
            return movement_id
        for cand in self._RHYTHM_PREFER_IDS:
            if cand in candidates:
                return cand
        return candidates[0]

    def select(self, scene: SceneInput) -> ShotPlan:
        if not scene.scene_type:
            scene.scene_type = SceneClassifier().classify(scene)

        st = self.data["scene_types"].get(scene.scene_type)
        warnings: list[str] = []
        if st is None:
            warnings.append(f"未知场景类型 {scene.scene_type}，回退到 dialogue 默认方案")
            st = self.data["scene_types"]["dialogue"]

        pattern_key, pattern, variant_note = self._pick_pattern(st, scene)
        shots = self._expand(st, pattern, scene, variant_note)
        return ShotPlan(
            scene_id=scene.scene_id,
            scene_type=scene.scene_type,
            pattern_key=pattern_key,
            shots=shots,
            warnings=warnings,
        )

    def select_beat(self, scene: SceneInput, index: int = 0, total: int = 1,
                    scene_summary: str = "", has_dialogue: bool = False,
                    beat_dialogue: str = "",
                    beat_angle_idx: int | None = None,
                    dialogue_turn_idx: int = 0,
                    focus: str = "",
                    motion: str = "",
                    focus_seq: int | None = None,
                    prev_motions: list[str] | None = None) -> Shot:
        """按节拍内容选单镜（2026-08-13）：用节拍的动作/情绪/主体驱动 运动与景别，
        并复用书知识（staging/sound/tone/edit）生成该拍的镜头提示。
        """
        if not scene.scene_type:
            scene.scene_type = SceneClassifier().classify(scene)
        st = self.data["scene_types"].get(scene.scene_type)
        if st is None:
            st = self.data["scene_types"]["dialogue"]
        _pk, pattern, variant_note = self._pick_pattern(st, scene)
        item = pattern[0] if pattern else {"shot": "medium", "movement": "static"}
        text = f"{scene.summary} {scene.emotion}"
        bf = self.data.get("beat_fallback", {}) or {}
        scale_close = tuple(bf.get("scale_close", []) or _BEAT_SCALE_CLOSE)
        scale_wide = tuple(bf.get("scale_wide", []) or _BEAT_SCALE_WIDE)
        angle_rules = bf.get("angle", {}) or _BEAT_ANGLE_OVERRIDES
        move_rules = (_parse_move_rules(bf.get("move", [])) or _BEAT_MOVE_OVERRIDES)
        move_rules = move_rules + _skill_move_rules()  # 技能决策补充（2026-08-24）
        movement_id = item.get("movement", "static")
        for keys, mv in move_rules:
            if any(k in text for k in keys):
                movement_id = mv
                break
        seq = _SCALE_BY_TYPE.get(scene.scene_type, ["medium"])
        pos = (index / (total - 1)) if total > 1 else 0.5
        scale_key = seq[min(round(pos * (len(seq) - 1)), len(seq) - 1)]
        if any(k in text for k in scale_close):
            scale_key = "closeup"
        elif any(k in text for k in scale_wide):
            scale_key = "long"
        # 多人反应镜：画面含 ≥2 个参与者 → 景别放宽（双人=中景/多人=全景），近景/特写不可能多人同框（2026-08-21 豆包问题3）
        _present = [p for p in scene.participants if p and p in text]
        if len(_present) >= 2 and scale_key in ("closeup", "big_closeup", "extreme_closeup", "medium_close", "over_shoulder"):
            scale_key = "medium" if len(_present) == 2 else "full"
        shot_def = self.data["shots"].get(scale_key, {})
        mov_def = self.data["movements"].get(movement_id, {})
        pace = st.get("pace_sec", [3, 6])
        duration = scene.duration_hint_sec or ((pace[0] + pace[1]) / 2)
        # 镜头活性长效机制：长静镜自动给运镜（防PPT）
        if movement_id == "static" and duration > float(
                load_motion_guard().get("data", {}).get("static_max_sec", 3.5)):
            movement_id = "push_slow"
            mov_def = self.data["movements"].get(movement_id, {})
        subjects = "、".join(scene.participants) if scene.participants else "主要人物"
        content = f"{item.get('note', '')}；主体：{subjects}；地点：{scene.location or '（未指定）'}"
        angle_key = item.get("angle", "eye")
        for ak, keys in angle_rules.items():
            if any(k in text for k in keys):
                angle_key = ak
                break
        camera_pos = self._beat_camera_pos(angle_key, scale_key, movement_id, scene.participants)
        inferred_emo = ""
        emo_for_motivation = scene.emotion
        if not emo_for_motivation:
            inferred_emo = infer_emotion(scene.summary, beat_dialogue, scene.scene_type).emotion
            emo_for_motivation = inferred_emo
        motivation = derive_motivation(
            summary=scene_summary, action=scene.summary, emotion=emo_for_motivation,
            scene_type=scene.scene_type, has_dialogue=has_dialogue or bool(beat_dialogue),
            index=index, total=total, location=scene.location,
        )
        edit_hint = self._edit_hint(scene, 0, 1)
        if motivation.jl_cut:
            edit_hint += "；" + motivation.jl_cut
        # focus 表现重点机制（2026-08-21，优先于旧 beat_angle_groups）：
        # 合法 focus → 直接按 focus_map 查表定 景别/机位/角度，运动按 LLM motion 兼容裁决或同 focus 序号轮换。
        _focus = (focus or "").strip()
        if _focus and _focus in (self.data.get("focus_map", {}) or {}):
            _fm = self.data["focus_map"][_focus]
            _focus_occ = focus_seq if focus_seq is not None else index
            _fscale = str(_fm.get("scale") or shot_def.get("scale", scale_key))
            _fangle = str(_fm.get("angle") or self.data["angles"].get(angle_key, "平视"))
            _default_motion = str(_fm.get("default_motion") or "固定")
            _fmotion = self._resolve_focus_motion(_focus, motion, _fscale, _default_motion, _focus_occ)
            _fmotion_key = self._movement_key(_fmotion)
            _new_fmotion_key = self._motion_rhythm_adjust(_fmotion_key, _fscale, prev_motions)
            if _new_fmotion_key and _new_fmotion_key != _fmotion_key:
                _fmotion = str((self.data.get("movements", {}) or {}).get(_new_fmotion_key, {}).get("name") or _fmotion)
            _fsubj = scene.participants[0] if scene.participants else "主体"
            _fcam = str(_fm.get("camera") or camera_pos)
            try:
                _fcam = _fcam.format(subj=_fsubj)
            except Exception:  # noqa: BLE001
                _fcam = _fcam
            return Shot(
                index=1,
                scale=_fscale,
                angle=_fangle,
                movement=_fmotion,
                stability=self._movement_stability(_fmotion),
                duration_sec=float(duration),
                content=content,
                emotion=scene.emotion,
                emotion_inferred=inferred_emo,
                staging=self._staging_hint(scene),
                sound=self._sound_hint(scene),
                tone=self._tone_hint(scene),
                edit=edit_hint,
                note=variant_note,
                camera_pos=_fcam,
                motivation=motivation_zh(motivation),
                motivation_en=motivation_en(motivation),
            )

        # 引擎驱动（2026-08-20，复用既有轮子）：动作块角度组 / 对话说话人轮换
        # promptgen 只传内容（beat_angle_idx / dialogue_turn_idx），机位/景别/角度/运动由此裁决；
        # 不传参数（既有调用）行为不变，走上面的内容驱动推导。
        _grp = self._beat_group(text)
        if _grp is None and beat_angle_idx is not None:
            _grp = self.data.get("beat_angle_default", {}) or {}
        if _grp and beat_angle_idx is not None:
            _angles = _grp.get("angles", []) or []
            if _angles:
                _entry = _angles[beat_angle_idx % len(_angles)]
                _shot_cam = _entry.get("camera") or camera_pos
                _shot_scale = _entry.get("scale") or shot_def.get("scale", scale_key)
                _shot_angle = _entry.get("angle") or self.data["angles"].get(angle_key, "平视")
                _shot_mov = _entry.get("movement") or mov_def.get("name", item.get("movement", ""))
                _shot_mov_key = self._movement_key(_shot_mov)
                _new_shot_mov_key = self._motion_rhythm_adjust(_shot_mov_key, _shot_scale, prev_motions)
                if _new_shot_mov_key and _new_shot_mov_key != _shot_mov_key:
                    _shot_mov = str((self.data.get("movements", {}) or {}).get(_new_shot_mov_key, {}).get("name") or _shot_mov)
                    _shot_mov_def = self.data["movements"].get(_new_shot_mov_key, {})
                else:
                    _shot_mov_def = mov_def
                _shot = Shot(
                    index=1,
                    scale=_shot_scale,
                    angle=_shot_angle,
                    movement=_shot_mov,
                    stability=_shot_mov_def.get("stability", ""),
                    duration_sec=float(duration),
                    content=content,
                    emotion=scene.emotion,
                    emotion_inferred=inferred_emo,
                    staging=self._staging_hint(scene),
                    sound=self._sound_hint(scene),
                    tone=self._tone_hint(scene),
                    edit=edit_hint,
                    note=variant_note,
                    camera_pos=_shot_cam,
                    motivation=motivation_zh(motivation),
                    motivation_en=motivation_en(motivation),
                )
                return _shot
        elif beat_dialogue or dialogue_turn_idx:
            # 对白镜 / 对白场景画面切换镜 / 旁白或动作镜（promptgen 按镜序号传 turn_idx）
            # → 按 turn_idx 轮换机位，避免同内容同机位重复
            _cycle = self.data.get("dialogue_turn_cycle", []) or []
            if _cycle:
                _e = _cycle[dialogue_turn_idx % len(_cycle)]
                _subj = scene.participants[0] if scene.participants else "主体"
                # 2026-08-21：多人放宽时【机位与景别一起调整】——贴脸/近身机位不能配全景（豆包问题一）
                _cyc_scale = _e.get("scale") or shot_def.get("scale", scale_key)
                _present = [pp for pp in scene.participants if pp and pp in text]
                _cyc_cam = _e.get("camera", camera_pos)
                if len(_present) >= 2 and _cyc_scale in ("近景", "特写", "大特写", "中近景", "过肩中近景"):
                    if len(_present) == 2:
                        _cyc_scale = "中景"
                        if "贴脸" in _cyc_cam or "近身" in _cyc_cam:
                            _cyc_cam = "双人中景机位（看{subj}）"
                    else:
                        _cyc_scale = "全景"
                        _cyc_cam = "远位定场机位（{subj}与众人）"
                _cyc_mov = _e.get("movement") or mov_def.get("name", item.get("movement", ""))
                _cyc_mov_key = self._movement_key(_cyc_mov)
                _new_cyc_mov_key = self._motion_rhythm_adjust(_cyc_mov_key, _cyc_scale, prev_motions)
                if _new_cyc_mov_key and _new_cyc_mov_key != _cyc_mov_key:
                    _cyc_mov = str((self.data.get("movements", {}) or {}).get(_new_cyc_mov_key, {}).get("name") or _cyc_mov)
                    _cyc_mov_def = self.data["movements"].get(_new_cyc_mov_key, {})
                else:
                    _cyc_mov_def = mov_def
                _shot = Shot(
                    index=1,
                    scale=_cyc_scale,
                    angle=_e.get("angle") or self.data["angles"].get(angle_key, "平视"),
                    movement=_cyc_mov,
                    stability=_cyc_mov_def.get("stability", ""),
                    duration_sec=float(duration),
                    content=content,
                    emotion=scene.emotion,
                    emotion_inferred=inferred_emo,
                    staging=self._staging_hint(scene),
                    sound=self._sound_hint(scene),
                    tone=self._tone_hint(scene),
                    edit=edit_hint,
                    note=variant_note,
                    camera_pos=_cyc_cam.format(subj=_subj),
                    motivation=motivation_zh(motivation),
                    motivation_en=motivation_en(motivation),
                )
                return _shot
        _fallback_scale = shot_def.get("scale", scale_key)
        movement_id = self._motion_rhythm_adjust(movement_id, _fallback_scale, prev_motions)
        mov_def = self.data["movements"].get(movement_id, mov_def)
        return Shot(
            index=1,
            scale=shot_def.get("scale", scale_key),
            angle=self.data["angles"].get(angle_key, "平视"),
            movement=mov_def.get("name", item.get("movement", "")),
            stability=mov_def.get("stability", ""),
            duration_sec=float(duration),
            content=content,
            emotion=scene.emotion,
            emotion_inferred=inferred_emo,
            staging=self._staging_hint(scene),
            sound=self._sound_hint(scene),
            tone=self._tone_hint(scene),
            edit=edit_hint,
            note=variant_note,
            camera_pos=camera_pos,
            motivation=motivation_zh(motivation),
            motivation_en=motivation_en(motivation),
        )

    @staticmethod
    def _beat_camera_pos(angle_key: str, scale_key: str, movement_id: str, participants: list | None = None) -> str:
        """机位兜底（2026-08-14 升级）：按 角度/景别/运动 + 人物 给出带主体与朝向的默认摄影机位置。"""
        parts = list(participants or [])
        subj = parts[0] if parts else "主体"
        oth = next((p for p in parts[1:] if p != subj), "另一方")
        if angle_key == "low":
            return f"低机位仰拍（看{subj}）"
        if angle_key in ("high", "aerial_view"):
            return f"高机位俯拍（俯瞰{subj}）"
        if angle_key == "dutch":
            return f"倾斜机位（荷兰角，{subj}）"
        if angle_key == "over_shoulder":
            return f"过肩机位（前景带{oth}肩部，主体{subj}居中）"
        if angle_key == "pov":
            return f"主观机位（第一人称，{subj}视线）"
        if scale_key in ("closeup", "big_closeup", "extreme_closeup"):
            return f"贴脸/近身机位（{subj}面部与细节）"
        if scale_key in ("long", "extreme_long"):
            return f"远位定场机位（{subj}与环境关系）"
        if movement_id in ("track", "steadicam"):
            return f"侧面跟随机位（平行跟随{subj}）"
        if movement_id == "handheld":
            return f"手持机位（近身随{subj}晃动）"
        return f"正面平视机位（看{subj}）"

    def _pick_pattern(self, st: dict, scene: SceneInput) -> tuple[str, list, str]:
        text = f"{scene.summary} {scene.emotion}"
        for name, variant in st.get("variants", {}).items():
            trigs = VARIANT_TRIGGERS.get(name, [])
            if any(t in text for t in trigs) and variant.get("pattern"):
                return name, variant["pattern"], variant.get("note", "")
        # 多人对话自动命中"多人"变体（合并简化：动作轴线+180°工作区）
        if scene.scene_type == "dialogue" and len(scene.participants) >= 3:
            multi = st.get("variants", {}).get("多人")
            if multi and multi.get("pattern"):
                return "多人", multi["pattern"], multi.get("note", "")
        return "default", st["default_pattern"], ""

    def _staging_hint(self, scene: SceneInput) -> str:
        """按场景类型、参与人数与情绪/关系给出场面调度提示（v0.2 形态位置 + v0.3 变奏技巧）。"""
        text = f"{scene.summary} {scene.emotion}"
        if scene.scene_type == "dialogue":
            n = len(scene.participants)
            if n == 2:
                hint = "I 形态｜位置一：面对面（或位置三：90°角）｜主镜头先行+外反拍/内反拍（数量对比 2→1→2，接近与远离）+视线匹配"
            elif n == 3:
                hint = "A 或 L 形态｜主镜头定位→双人过肩（说话者+倾听者）→单人反应｜数量对比 2→1；注意中心主宰者用头部转动引导转移"
            elif n >= 4:
                hint = "合并简化｜主要二人动作轴线+180°工作区｜主持人引导注意中心；人群全景+注意中心近景双轨"
            else:
                hint = "I 形态｜主镜头先行+外反拍/内反拍"
            variations = self.data.get("dialogue_variations", [])
            matched = [v["name"] for v in variations if any(t in text for t in v.get("triggers", []))]
            if matched:
                hint += f"｜变奏技巧：{'、'.join(matched[:2])}"
            return hint
        if scene.scene_type == "action":
            hint = "人物运动引导注意力（动机为先）；关键打击点升格；定期全景定位恢复空间感"
            variations = self.data.get("action_variations", [])
            matched = [v["name"] for v in variations if any(t in text for t in v.get("triggers", []))]
            if matched:
                hint += f"｜变奏技巧：{'、'.join(matched[:2])}"
            return hint
        if scene.scene_type == "emotion":
            return "纵深/近景调度：特写+缓推+留白，避免无动机镜头运动"
        return ""

    def _sound_hint(self, scene: SceneInput) -> str:
        """按场景类型生成声音轨提示（v0.5，源自《Sound Design》听觉层级）。"""
        if scene.scene_type == "dialogue":
            return "对白优先；环境音铺垫；音乐压低到对白收尾后升起"
        if scene.scene_type == "action":
            return "音效主导（打击/碰撞/运动）；节奏音乐；关键点可撤乐留噪"
        if scene.scene_type == "suspense":
            return "静默+低频环境（陌生声响）；惊吓点突发音效"  # 2026-08-21 问题九：不写死"夜晚"（白天教堂也会命中 suspense）
        if scene.scene_type == "emotion":
            return "音乐情绪主导（移情）；环境音弱化；对白/叹息细节突出"
        if scene.scene_type == "reveal":
            return "揭示前压低/静默；揭示瞬间音乐或音效骤起"
        if scene.scene_type == "fantasy":
            return "声音与主线区分（混响/失真/环境异常）；音乐梦幻化"
        return "环境音建立空间；音乐随情绪变化"

    def _tone_hint(self, scene: SceneInput) -> str:
        """按场景类型与情绪生成色调提示（v0.6，源自《以眼说话》色彩搭配 + 《电影色彩学》色调）。"""
        text = f"{scene.summary} {scene.emotion}"
        if any(k in text for k in ("威胁", "愤怒", "对峙", "紧张", "压迫")):
            return "冷调低饱和（蓝青）或高反差暗调｜对比与亲和：强对比"
        if any(k in text for k in ("亲密", "温馨", "温柔", "依偎", "告白")):
            return "暖调低饱和（橙/暖黄）｜对比与亲和：高亲和"
        if scene.scene_type == "action":
            return "高饱和+互补色（冷蓝/暖橙）｜强对比强调动感"
        if scene.scene_type == "suspense":
            return "低饱和蓝青暗调｜弱光+高光比"
        if scene.scene_type == "emotion":
            return "暖调或冷暖对比（色相随情绪渐变）"
        if scene.scene_type == "reveal":
            return "冷暖对比：揭示前低饱和→揭示瞬间高饱和/偏色"
        if scene.scene_type == "fantasy":
            return "主观色调（偏色/高饱和/异色）"
        if scene.scene_type == "transition":
            return "光影/季节色调变化（昼夜、四季、冷暖切换）"
        if scene.scene_type == "establishing":
            return "环境固有色+季节色调（客观色调）"
        return "中性/环境色调（客观）"

    def _edit_hint(self, scene: SceneInput, index: int, total: int) -> str:
        """按场景类型生成剪切动机/转场提示（v0.7，源自默奇剪辑六原则/剪辑语法）。"""
        if scene.scene_type == "dialogue":
            if index == 0:
                return "切（建立空间/关系）"
            if index == total - 1:
                return "切（反应收束，情感动机）"
            return "切（过肩/正反打，视线匹配；反应镜头位置）"
        if scene.scene_type == "action":
            return "在动作中剪（动作匹配）｜打击点升格处切"
        if scene.scene_type == "suspense":
            return "延迟剪切（静默延长）→ 惊吓点突发切"
        if scene.scene_type == "emotion":
            return "切（情感动机：推近/反应）"
        if scene.scene_type == "reveal":
            return "在揭示瞬间剪（先局部后全貌）"
        if scene.scene_type == "transition":
            return "叠化/匹配剪辑（光影或形状匹配）"
        if scene.scene_type == "establishing":
            return "切（定场镜头→中景/近景）"
        if scene.scene_type == "fantasy":
            return "跳切/闪白转场（与主线区分）"
        return "切（新信息动机）"

    def _expand(self, st: dict, pattern: list, scene: SceneInput, variant_note: str) -> list[Shot]:
        shots_vocab = self.data["shots"]
        pace_min, pace_max = st.get("pace_sec", [3, 6])
        n = len(pattern)
        subjects = "、".join(scene.participants) if scene.participants else "主要人物"
        staging_hint = self._staging_hint(scene)
        sound_hint = self._sound_hint(scene)
        tone_hint = self._tone_hint(scene)
        inferred_emo = ""
        emo_for_motivation = scene.emotion
        if not emo_for_motivation:
            inferred_emo = infer_emotion(scene.summary, "", scene.scene_type).emotion
            emo_for_motivation = inferred_emo
        out: list[Shot] = []
        for i, item in enumerate(pattern):
            shot_def = shots_vocab.get(item["shot"], {})
            mov_def = self.data["movements"].get(item.get("movement", "static"), {})
            if n <= 1:
                duration = round((pace_min + pace_max) / 2, 1)
            else:
                ratio = i / (n - 1)
                duration = round(pace_max - (pace_max - pace_min) * ratio, 1)
            content = f"{item.get('note', '')}；主体：{subjects}；地点：{scene.location or '（未指定）'}"
            motivation = derive_motivation(
                summary=scene.summary, action=scene.summary, emotion=emo_for_motivation,
                scene_type=scene.scene_type, has_dialogue=(scene.scene_type == "dialogue"),
                index=i, total=n, location=scene.location,
            )
            edit_hint = self._edit_hint(scene, i, n)
            if motivation.jl_cut:
                edit_hint += "；" + motivation.jl_cut
            out.append(
                Shot(
                    index=i + 1,
                    scale=shot_def.get("scale", item["shot"]),
                    angle=self.data["angles"].get(item.get("angle", "eye"), "平视"),
                    movement=mov_def.get("name", item.get("movement", "")),
                    stability=mov_def.get("stability", ""),
                    duration_sec=duration,
                    content=content,
                    emotion=scene.emotion,
                    emotion_inferred=inferred_emo,
                    staging=staging_hint,
                    sound=sound_hint,
                    tone=tone_hint,
                    edit=edit_hint,
                    note=variant_note,
                    motivation=motivation_zh(motivation),
                    motivation_en=motivation_en(motivation),
                )
            )
        return out
