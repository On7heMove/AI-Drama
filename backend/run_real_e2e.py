# -*- coding: utf-8 -*-
"""真实模型端到端（3 次固定梗概，量化）。

- 密钥：仅从 _瀑布流_提示词模块\\backend\\.env 注入内存（不打印/不落盘）
- 梗概：01_故事梗概_破芯人间重燃.md（固定）
- 3 次 run_inverse（eps 1-1，deepseek-v4-flash）
- 量化：结构达标率 / 逻辑错误数 / 伏笔回收率 / 剧本落盘
"""
import asyncio, json, os, sys, re, time, datetime

BE = os.path.abspath(r"backend")
sys.path.insert(0, BE)

# 1) 密钥注入（仅内存，不落盘/不打印）
def _inject_key():
    env_path = r"_瀑布流_提示词模块\backend\.env"
    if not os.path.isfile(env_path):
        raise SystemExit("密钥文件缺失")
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k in ("deepseek_api_key", "DEEPSEEK_API_KEY"):
                os.environ["DEEPSEEK_API_KEY"] = v.strip().strip('"').strip("'")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("未读取到 DEEPSEEK_API_KEY")

_inject_key()

from app.inverse.service import run_inverse
from app.production.llm_client import DeepSeekClient


class RecordingClient(DeepSeekClient):
    """包装真实 client：记录每次 chat 的 system/user/assistant 原文（含 json_mode 的原始响应）。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.turns = []

    async def chat(self, system, user, **kw):
        text = await super().chat(system, user, **kw)
        self.turns.append({"system": system, "user": user, "assistant": text})
        return text

SYNOPSIS = """公元4000年，人类进入芯片时代。每个人在出生一个月后便要被植入芯片，芯片记录着这个人的社会分工、必要知识、意识形态与社会交往，人与人之间不再具有亲情友情爱情。某一天一颗彗星掠过地球造成磁场巨变，所有硅基芯片与存储设备失效，人类突然失去芯片制约，以自然属性人状态重新摸索生存，在互助与矛盾中重新产生亲情友情爱情。在人类逐渐恢复科学技术与生产力后，巨大问题摆在面前：回到最高效的芯片时代，还是保持情感维系但有局限的自然状态。男主角陈思扬在这个过程中与女主孙丽莎建立深厚感情，在人类抉择最后时刻，陈思扬选择拿出芯片摔在地上，选择继续有爱的生活。"""

OUT = os.path.join(BE, "_e2e_out")
os.makedirs(OUT, exist_ok=True)


def quantify(result, run_id):
    episodes = result["episodes"]
    items = result.get("quality_items") or []
    logic_bad = [i for i in items if i.get("dimension") == "logic" and i.get("severity") in ("fatal", "error")]
    fatal_total = [i for i in items if i.get("severity") == "fatal"]
    ledger = result.get("ledger") or []
    total = len(ledger)
    paid = sum(1 for it in ledger if it.get("status") == "payoff" and it.get("payoff_ep"))
    structure_ok = bool(result.get("spine", {}).get("spine_title")) and bool(result.get("beats")) and bool(episodes) and bool(episodes[0].get("scenes"))
    return {
        "run_id": run_id,
        "structure_ok": structure_ok,
        "n_episodes": len(episodes),
        "n_scenes": len(episodes[0].get("scenes", [])) if episodes else 0,
        "logic_errors": len(logic_bad),
        "fatal_issues": len(fatal_total),
        "quality_items_total": len(items),
        "foreshadow_planted": total,
        "foreshadow_paid": paid,
        "foreshadow_recovery_rate": round(paid / total, 3) if total else None,
        "screenplay_chars": len(json.dumps(episodes, ensure_ascii=False)),
        "quality_summary": result.get("quality"),
    }


async def main():
    rounds = []
    n_rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for i in range(1, n_rounds + 1):
        run_id = "real_e2e_%d" % i
        print("=== round %d start ===" % i, flush=True)
        t0 = time.monotonic()
        client = RecordingClient()   # model 默认 deepseek-v4-flash，记录全部对话
        result = await run_inverse(
            title="破芯·人间重燃", genre="科幻", synopsis=SYNOPSIS,
            brief={"theme": "带感情的人才是人"},
            eps_start=1, eps_end=1, client=client)
        elapsed = round(time.monotonic() - t0, 1)
        q = quantify(result, run_id)
        q["elapsed_sec"] = elapsed
        rounds.append(q)
        # 落盘本轮完整结果 + 剧本
        json.dump(result, open(os.path.join(OUT, run_id + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        # 落盘完整 LLM 对话（system/user/assistant 原文）
        json.dump(client.turns, open(os.path.join(OUT, run_id + "_llm_turns.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        # 生成含 LLM 回复的瀑布流 md
        wf = ["# Round %d prompt 对话瀑布流（含 LLM 回复）" % i, ""]
        for idx, t in enumerate(client.turns, 1):
            wf.append("## 调用 %d" % idx)
            wf.append("### system")
            wf.append(t["system"])
            wf.append("### user（%d 字符）" % len(t["user"]))
            wf.append(t["user"])
            wf.append("### assistant（LLM 回复，%d 字符）" % len(t["assistant"]))
            wf.append(t["assistant"])
            wf.append("")
        io_open = open(os.path.join(OUT, "prompt_waterfall_%d_with_replies.md" % i),
                       "w", encoding="utf-8")
        io_open.write("\n".join(wf))
        io_open.close()
        with open(os.path.join(OUT, run_id + "_screenplay.md"), "w", encoding="utf-8") as f:
            for ep in result["episodes"]:
                f.write("## 第%s集 %s\n\n" % (ep.get("ep"), ep.get("title", "")))
                for sc in ep.get("scenes", []):
                    f.write("### %s %s\n" % (sc.get("location", ""), sc.get("time", "")))
                    for a in sc.get("action_blocks", []):
                        f.write("（%s）\n" % a)
                    for d in sc.get("dialogues", []):
                        f.write("%s：%s\n" % (d.get("speaker", ""), d.get("line", "")))
        print("=== round %d done: structure=%s logic_err=%d fatal=%d foreshadow=%s ==="
              % (i, q["structure_ok"], q["logic_errors"], q["fatal_issues"], q["foreshadow_recovery_rate"]), flush=True)

    structure_rate = round(sum(1 for r in rounds if r["structure_ok"]) / len(rounds), 3)
    avg_logic = round(sum(r["logic_errors"] for r in rounds) / len(rounds), 2)
    avg_fatal = round(sum(r["fatal_issues"] for r in rounds) / len(rounds), 2)
    rates = [r["foreshadow_recovery_rate"] for r in rounds if r["foreshadow_recovery_rate"] is not None]
    avg_fr = round(sum(rates) / len(rates), 3) if rates else None
    summary = {
        "input": "01_故事梗概_破芯人间重燃.md（固定）",
        "model": "deepseek-v4-flash", "rounds": rounds,
        "aggregate": {
            "structure_ok_rate": structure_rate,
            "avg_logic_errors": avg_logic,
            "avg_fatal_issues": avg_fatal,
            "avg_foreshadow_recovery_rate": avg_fr,
            "generated_at": datetime.datetime.now().isoformat(),
        },
        "production_v1": "not_claimed",
    }
    json.dump(summary, open(os.path.join(OUT, "real_e2e_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


asyncio.run(main())
