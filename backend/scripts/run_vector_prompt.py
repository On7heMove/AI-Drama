"""产线二提示词生成（20维向量路径）入口：剧本 → 场景 → 向量 → 渲染 → 技能 → 提示词。

用法（backend）：
    python scripts/run_vector_prompt.py --script <剧本.txt> --out <输出目录>

对齐 docs/20维向量接入产线二.md：LLM 只出 20 维向量，本地校验/渲染/技能/组装。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from app.production.llm_client import DeepSeekClient
from app.storyboard.vector_pipeline import render_scene_prompt


def split_scenes(text: str) -> list[tuple[str, str]]:
    """按【场景：】或 ### 第X场 切分；返回 [(scene_id, 文本)]。"""
    parts = re.split(r"(?=\*\*【场景：|### 第[一二三四五六七八九十]+[章场])", text)
    out = []
    for i, s in enumerate(parts):
        s = s.strip()
        if len(s) < 20:
            continue
        sid = f"scene{i+1}"
        out.append((sid, s))
    return out


async def run(script: Path, out_dir: Path, max_scenes: int = 0) -> dict:
    client = DeepSeekClient()
    t0 = time.time()
    text = script.read_text(encoding="utf-8")
    scenes = split_scenes(text)
    if max_scenes:
        scenes = scenes[:max_scenes]
    result = {"scenes": []}
    for sid, sc in scenes:
        print(f"① {sid}（{len(sc)}字符）...", flush=True)
        r = await render_scene_prompt(sc, client=client, scene_id=sid)
        result["scenes"].append({
            "scene_id": sid, "branch": r["render"]["branch"],
            "skills": r["skills"]["hit_skills"], "confidence": r["profile"]["confidence"],
            "prompt": r["prompt"],
        })
        print(f"   → {r['render']['branch']} 技能{len(r['skills']['hit_skills'])}", flush=True)
    result["meta"] = {"source": script.name, "n_scenes": len(scenes), "elapsed_sec": round(time.time() - t0)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vector_prompts.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    txt = "\n\n".join(f"=== {s['scene_id']} ===\n{s['prompt']}" for s in result["scenes"])
    (out_dir / "vector_prompts.txt").write_text(txt, encoding="utf-8")
    print(f"✅ 落盘 {out_dir}（{len(result['scenes'])} 场景）耗时{round(time.time() - t0)}s")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", default=r"D:\AI短剧逻辑校验\delivery\vector_prompts")
    ap.add_argument("--max-scenes", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(run(Path(args.script), Path(args.out), args.max_scenes))


if __name__ == "__main__":
    main()
