"""时长合理性长效机制（2026-08-14，2026-08-14 修订）：台词/旁白占时长 + 时长不均匀检查。

- 台词只取英文部分：忠实原文=试稿英文对白；括号内的中文译不进入提示词，也不参与时长估算
- 对白按英文语速折算秒数，计入该镜总时长
- 旁白·原文 只允许来自试稿（禁止大纲内容混入提示词）
- 同场景时长 std/mean < min_std_ratio → 告警（时长过平均）
配置：config/storyboard/duration_rule.json
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.paths import data_root

DURATION_PATH = data_root() / "config" / "storyboard" / "duration_rule.json"

_PAIR_RE = re.compile(r"[（(][^）)]*[）)]")
_ELLIPSIS_RE = re.compile(r"…|\.\.\.")


@lru_cache(maxsize=1)
def load_duration_rule() -> dict:
    if not DURATION_PATH.exists():
        return {"data": {}}
    try:
        return json.loads(DURATION_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"data": {}}


def strip_parens(text: str) -> str:
    """去掉所有括号（半角/全角）内容：中文译与表演指示都不属于台词正文。"""
    return _PAIR_RE.sub("", text or "")


def strip_dialogue_translation(dialogue: str) -> str:
    """提示词【对白】展示用：去掉行尾括号里的中文译，保留 说话人+表演指示+英文台词。"""
    if not dialogue:
        return ""
    for sep in ("：", ":"):
        if sep in dialogue:
            head, rest = dialogue.split(sep, 1)
            return f"{head}：{strip_parens(rest)}".strip()
    return strip_parens(dialogue).strip()


_OS_LEAD = ("OS：", "VO：", "画外音：", "内心独白：", "OS:", "VO:", "旁白：")


def extract_dialogue_en(dialogue: str) -> str:
    """只取英文台词正文：去括号（中文译/表演指示）与说话人前缀。供时长/忠实检验/LLM输入。"""
    s = strip_parens(dialogue or "")
    for sep in ("：", ":"):
        if sep in s:
            s = s.split(sep, 1)[1]
            break
    s = s.strip()
    # 2026-08-21：LLM 可能把"OS："写进台词正文（说话人清洗后残留）→ 再剥一层类型标记前缀
    for pre in _OS_LEAD:
        if s.startswith(pre):
            s = s[len(pre):].strip()
            break
    return s


def _ellipsis_pause_seconds(text: str) -> int:
    """省略号段数：每个省略号（…… 或 ...）拖长音 +1s。"""
    return len(_ELLIPSIS_RE.findall(text or ""))


def dialogue_seconds(dialogue: str) -> float:
    """对白时长：按主导语言折算（中文/英文分别计价），避免中文对白含英文人名（如 Ada）被误走英文分支而严重低估。

    规则：中文字符数 > 2×英文词数 → 中文为主按中文折算；否则英文为主按英文折算（均含 min_line 底）。
    """
    if not dialogue:
        return 0.0
    d = load_duration_rule().get("data", {})
    en = extract_dialogue_en(dialogue)
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", en)
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", en))
    min_line = float(d.get("min_line", 0.6))
    # min_line 为单句下限（max），不是叠加——避免多镜累加虚增总时长
    if zh_chars > len(words) * 2:
        base = max(min_line, zh_chars * float(d.get("speech_rate_zh", 0.35)))
    else:
        base = max(min_line, len(words) * float(d.get("speech_rate_en", 0.5)))
    return base + _ellipsis_pause_seconds(en)


def vo_seconds(sound: str) -> float:
    """旁白·原文 时长：中文按字数×vo_rate，英文按词数×speech_rate_en，避免英文长句按字符虚增。"""
    if not sound or "旁白·原文" not in sound:
        return 0.0
    m = re.search(r"画外音（旁白·原文）：([^；]+)", sound)
    if not m:
        return 0.0
    body = m.group(1).strip()
    d = load_duration_rule().get("data", {})
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", body)
    zh_chars = len(re.findall(r"[一-鿿]", body))
    if zh_chars > len(words) * 2:
        secs = zh_chars * float(d.get("vo_rate", d.get("speech_rate_zh", 0.3)))
    else:
        secs = len(words) * float(d.get("speech_rate_en", 0.4))
    return max(float(d.get("min_line", 0.6)), secs) + _ellipsis_pause_seconds(body)


def effective_duration(visual_dur: float, dialogue: str = "", sound: str = "") -> float:
    """该镜总时长：
    - 有对白：视觉 + 对白 + 旁白（台词占时长）。
    - 纯旁白（无对白）：时长 = 旁白时长（min 4s）——旁白镜视觉随旁白连续切换，不叠加，防 PPT
      （长旁白按 3-5s/镜 由镜头数机制切分，旁白时长即该镜时长）。
    - 无台词：视觉时长。
    """
    vo = vo_seconds(sound)
    if dialogue:
        return float(visual_dur) + dialogue_seconds(dialogue) + vo
    if vo:
        return max(float(vo), 4.0)
    return float(visual_dur)


def parse_prompt_duration(zh: str) -> float:
    """从提示词【镜头】行解析显示时长。"""
    cam = next((l for l in zh.splitlines() if l.startswith("【镜头】")), "")
    m = re.search(r"（约(\d+(?:\.\d+)?)s", cam)
    return float(m.group(1)) if m else 0.0


def check_uniformity(durations: list[float], scene_id: str = "") -> list[str]:
    """同场景时长过平均检查。"""
    if len(durations) < 4:
        return []
    mean = sum(durations) / len(durations)
    if mean <= 0:
        return []
    std = (sum((x - mean) ** 2 for x in durations) / len(durations)) ** 0.5
    ratio = std / mean
    min_r = float(load_duration_rule().get("data", {}).get("min_std_ratio", 0.15))
    if ratio < min_r:
        return [f"{scene_id}: 时长过平均（std/mean={ratio:.2f}<{min_r}），像PPT流水账；应按内容张力错落（对话/爆点/收束长短不一）"]
    return []
