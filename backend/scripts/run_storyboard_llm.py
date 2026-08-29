"""产线二 LLM 分镜生成流水线（新架构，可复用）。

剧本 → ① LLM 数据整理（特征卡：类型/情绪/时长/画面需求/意象）
     → ② 本地技能路由（特征卡→技能清单）
     → ③ LLM 分镜生成（shots 含 lighting/emotion，按技能）
     → ④ 本地补全（风格锁定/负面/竖屏固定模板）→ 标准标签提示词
     → 落盘 json + txt

设计见 docs/产线二LLM分镜生成架构.md
用法：python scripts/run_storyboard_llm.py --script <剧本路径> --out <输出目录> [--max-tokens-profile 20000 --max-tokens-shots 24000]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from app.production.llm_client import DeepSeekClient
from app.storyboard.storyboard_qa import run_qa

STYLE_LOCK = "真人风，真实自然的人物与表演，日常真实生活质感，竖屏9:16"
NEGATIVE = "NOT 塑料质感、廉价、轻飘飘、CG感、穿模、光污染"

# (book, skill_slug, 标签) —— 注入用
SKILLS = {
    "speech-image-design": ("chion-audiovision", "speech-image-design", "希翁·言语与影像关系"),
    "montage-and-parallel": ("dancyger-editing", "montage-and-parallel", "丹西格·蒙太奇谱系"),
    "cut-rate-and-pacing": ("blink-of-an-eye", "cut-rate-and-pacing", "默奇·切镜率"),
    "blink-cut-point": ("blink-of-an-eye", "blink-cut-point", "默奇·眨眼切点"),
    "axis-triangle-crossline": ("katz-film-directing", "axis-triangle-crossline", "卡茨·轴线与三角机位"),
    "dialogue-anti-shot-reverse": ("master-shots-2", "dialogue-anti-shot-reverse", "大师镜头·对话反打"),
    "action-editing": ("dancyger-editing", "action-editing", "丹西格·动作剪辑"),
    "speed-formula": ("master-shots-3", "speed-formula", "大师镜头·速度公式"),
    "push-in-and-retreat": ("master-shots-2", "push-in-and-retreat", "大师镜头·推近拉远"),
    "silence-design": ("sound-design", "silence-design", "声音设计·静音留白"),
}
COMPACT = "输出紧凑 JSON：无缩进、无多余换行，字符串用双引号，不要 Markdown。"


def skill_I(book, skill):
    p = Path(rf"D:\AI短剧逻辑校验\books\{book}\{skill}\SKILL.md")
    if not p.exists():
        return ""
    txt = p.read_text(encoding="utf-8")
    m = re.search(r"## I — 方法论骨架.*?\n(.*?)(?=\n## A1)", txt, re.DOTALL)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:260] if m else ""


def route(seg):
    need = seg.get("image_need", ""); dur = seg.get("duration_sec") or 0; t = seg.get("type", "")
    if need == "montage":
        return ["speech-image-design", "montage-and-parallel", "cut-rate-and-pacing", "blink-cut-point"]
    if t == "dialogue":
        return ["axis-triangle-crossline", "dialogue-anti-shot-reverse", "cut-rate-and-pacing"]
    if t == "action":
        return ["action-editing", "speed-formula", "cut-rate-and-pacing"]
    if need == "speaker" or dur > 8:
        return ["cut-rate-and-pacing", "blink-cut-point"]
    return []


def split_scenes(text):
    """按「### 第X章」或「### 第X场」分割；返回 [(scene_id, 文本)]。"""
    parts = re.split(r"(?=### 第[一二三四五六七八九十]+[章场])", text)
    out = []
    for s in parts:
        s = s.strip()
        if not s:
            continue
        m = re.match(r"### 第([一二三四五六七八九十]+)([章场])", s)
        if m:
            num = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}.get(m.group(1), 0)
            sid = f"c{num}" if m.group(2) == "章" else f"e1_s{num}"
            out.append((sid, s))
    return out



_SCALE_WORDS = ["大远景","远景","全景","中景","近景","特写","大特写","极特写","微距",
                 "过肩","全屏","空镜","全貌","全身","半身","面部","主观"]


def clean_shot(sh: dict) -> dict:
    """本地清洗 LLM 镜头字段：机位去景别词、运动归一化、景别提取（LLM 字段污染兜底）。"""
    cam = str(sh.get("camera", "") or "").strip()
    scale = str(sh.get("scale", "") or "").strip()
    mov = str(sh.get("movement", "") or "").strip()
    for w in _SCALE_WORDS:
        if w in cam:
            if w not in scale:
                scale = w
            cam = cam.replace(w, "").strip()
    cam = re.sub(r"[｜|\s]{1,}$", "", cam).strip()
    if not cam or cam in ("固定",):
        cam = "固定机位"
    if mov in ("无", "静止", "none", "None", "", "-"):
        mov = "固定"
    sh["camera"] = cam
    sh["scale"] = scale or "中景"
    sh["movement"] = mov
    return sh


def assemble(shot) -> str:
    """④ 本地补全固定标签，组装标准提示词（LLM 给 lighting/emotion）。"""
    clean_shot(shot)
    cam = shot.get("camera", ""); scale = shot.get("scale", ""); angle = shot.get("angle", "")
    mov = shot.get("movement", ""); dur = shot.get("duration_sec", "")
    content = shot.get("content", ""); sound = shot.get("sound", "")
    lighting = shot.get("lighting", "") or "自然光"
    emotion = shot.get("emotion", "") or "中性"
    return (f"【风格锁定】{STYLE_LOCK}\n"
            f"【画面】机位：{cam}｜景别：{scale}｜角度：{angle}｜运动：{mov}（{dur}s）｜{content}\n"
            f"【光线】{lighting}\n【声音】{sound}\n【情绪】{emotion}\n【负面】{NEGATIVE}")


async def run(script_path: Path, out_dir: Path, mt_profile: int, mt_shots: int) -> dict:
    client = DeepSeekClient()
    t0 = time.time()
    text = script_path.read_text(encoding="utf-8")
    scenes_raw = []
    for sid, ch in split_scenes(text):
        # 章内按【场景：】细分（小说体大段，避免 LLM 推理占满）
        scene_parts = re.split(r"(?=\*\*【场景：)", ch)
        _i = 0
        for sp in scene_parts:
            sp = sp.strip()
            if sp and ("【场景：" in sp or _i == 0):
                _i += 1
                scenes_raw.append((f"{sid}_s{_i}", sp))
            elif sp:
                scenes_raw[-1] = (scenes_raw[-1][0], scenes_raw[-1][1] + "\n" + sp)
    SYS1 = ("你是短剧分镜前的段落分析器。把给定场景剧本整理为特征卡数组，输出 JSON："
            '{"segments":[{"seg_id","type","speaker","text","emotion","duration_sec","image_need","elements","scene_type","summary"}]} '
            "type ∈ os/dialogue/action/vo/montage/flashback；image_need ∈ montage(叙述型旁白→画面演意象)/speaker(感受型→说话人)/action/scenery。"
            "duration_sec 中文旁白0.3秒/字、对白0.25秒/字。" + COMPACT)
    SYS2 = ("你是资深短剧分镜导演。根据段落特征卡和适用技能，生成镜头方案，输出 JSON："
            '{"shots":[{"seg_id","n","duration_sec","camera","scale","angle","movement","content","sound","lighting","emotion"}]} '
            "lighting=该镜光线设计（语义判断），emotion=该镜情绪。必须遵守注入技能。" + COMPACT)

    scenes = []
    for sid, raw in scenes_raw:
        if len(raw) < 50:
            continue  # 跳过章头空块
        try:
            card = await client.chat_json(SYS1, f"场景剧本：\n{raw}", max_tokens=mt_profile, temperature=0.2)
            segs = card.get("segments") or []
            for seg in segs:
                seg["route"] = route(seg)
            scenes.append({"scene_id": sid, "segments": segs})
            print(f"① {sid} → {len(segs)} 段", flush=True)
        except Exception as e:  # noqa: BLE001 单场景失败不中断
            scenes.append({"scene_id": sid, "segments": [], "error": str(e)[:120]})
            print(f"① {sid} ✗ {str(e)[:60]}", flush=True)

    result = {"scenes": [], "prompts": []}
    for s in scenes:
        sid = s["scene_id"]; segs = s.get("segments") or []
        if not segs:
            result["scenes"].append({"scene_id": sid, "error": s.get("error", ""), "plan": {}})
            continue
        seg_cards = [{k: v for k, v in x.items() if k != "route"} for x in segs]
        skills = []
        for x in segs:
            for r in x.get("route", []):
                if r not in skills:
                    skills.append(r)
        inj = []
        for sk in skills:
            if sk in SKILLS:
                book, slug, label = SKILLS[sk]
                inj.append(f"- [{label}] {skill_I(book, slug)}")
        user2 = f"场景 {sid} 特征卡：\n{json.dumps(seg_cards, ensure_ascii=False)}\n\n适用技能：\n" + "\n".join(inj)
        try:
            plan = await client.chat_json(SYS2, user2, max_tokens=mt_shots, temperature=0.3)
            shots = plan.get("shots") or []
            for sh in shots:
                sh["prompt"] = assemble(sh)
            result["scenes"].append({"scene_id": sid, "skills": skills, "plan": plan})
            result["prompts"].extend([{"scene_id": sid, "n": sh.get("n"), "prompt": sh["prompt"]} for sh in shots])
            print(f"③ {sid} → {len(shots)} 镜（技能{len(skills)}）", flush=True)
        except Exception as e:  # noqa: BLE001 单场景失败不中断
            result["scenes"].append({"scene_id": sid, "skills": skills, "error": str(e)[:120], "plan": {}})
            print(f"③ {sid} ✗ {str(e)[:60]}", flush=True)

    result["meta"] = {"source": script_path.name, "n_scenes": len(scenes), "elapsed_sec": round(time.time() - t0)}
    out_dir.mkdir(parents=True, exist_ok=True)
    # 交付质量门：QA 报告落盘（fail 时明确提示，不阻止落盘但强制可见）
    qa = run_qa(result)
    (out_dir / "qa_report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=1), encoding="utf-8")
    _qmark = "PASS" if qa["ok"] else "FAIL"
    print(f"[QA] {_qmark}: {qa['summary']}（详见 qa_report.json）")
    (out_dir / "storyboard.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    txt = "\n\n".join(f"=== {p['scene_id']} 镜{p['n']} ===\n{p['prompt']}" for p in result["prompts"])
    (out_dir / "storyboard_prompts.txt").write_text(txt, encoding="utf-8")
    print(f"\n✅ 落盘 {out_dir}（json + prompts.txt）耗时{round(time.time() - t0)}s，共 {len(result['prompts'])} 镜")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", default=r"D:\AI短剧逻辑校验\delivery\storyboard_llm_out")
    ap.add_argument("--max-tokens-profile", type=int, default=20000)
    ap.add_argument("--max-tokens-shots", type=int, default=24000)
    args = ap.parse_args()
    asyncio.run(run(Path(args.script), Path(args.out), args.max_tokens_profile, args.max_tokens_shots))


if __name__ == "__main__":
    main()
