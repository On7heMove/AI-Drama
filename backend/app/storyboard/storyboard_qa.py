"""分镜产出质量门（storyboard QA）：交付前强制校验完整性与规范性。

完整性：场景覆盖（缺场景告警）、每场景镜数>0
规范性：镜头字段非空、机位无景别污染、运动合法、标准标签齐全、时长合理
输出：QualityReport（每场景通过/问题清单 + 汇总）

挂载：run_storyboard_llm.py 落盘前自动调用；也支持单独跑：python -m app.storyboard.storyboard_qa --file xxx.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCALE_WORDS = ("大远景", "远景", "全景", "中景", "近景", "特写", "大特写", "极特写", "微距",
                 "过肩", "全屏", "空镜", "全貌", "全身", "半身", "面部", "主观")
VALID_MOVEMENT = ("固定", "静止", "推近", "推", "拉", "摇", "甩", "移", "横移", "滑轨", "跟", "跟拍",
                  "环绕", "升降", "降", "升", "手持", "晃动", "微晃", "抖动", "航拍", "前推", "缓推",
                  "急推", "慢动作", "快切", "焦点转换", "呼吸感", "贴地", "上摇", "下摇", "停留", "移动", "快速")
REQUIRED_TAGS = ("【风格锁定】", "【画面】", "【光线】", "【声音】", "【情绪】", "【负面】")
REQUIRED_FIELDS = ("camera", "scale", "angle", "movement", "duration_sec", "content", "sound")


def check_shot(sh: dict, scene_id: str) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    # 字段非空
    for f in REQUIRED_FIELDS:
        v = sh.get(f)
        if v is None or str(v).strip() in ("", "无", "None"):
            issues.append(f"字段缺失/空: {f}")
    cam = str(sh.get("camera", ""))
    mov = str(sh.get("movement", ""))
    scale = str(sh.get("scale", ""))
    # 机位混入景别词
    if any(w in cam for w in SCALE_WORDS):
        issues.append(f"机位含景别词: {cam}")
    # 运动非法
    if mov and not any(v in mov for v in VALID_MOVEMENT):
        issues.append(f"运动值可疑: {mov}")
    # 景别为空或非法
    if scale and not any(w in scale for w in SCALE_WORDS):
        issues.append(f"景别可疑: {scale}")
    # 时长：<0.5s error（过短不可生成）；>15s warning（超长单镜 PPT 风险，提示切分）
    dur = sh.get("duration_sec")
    try:
        d = float(dur)
        if d < 0.5:
            issues.append(f"时长过短: {dur}s")
        elif d > 15:
            warnings.append(f"超长镜 {dur}s（PPT 风险，建议切分）")
    except (TypeError, ValueError):
        issues.append(f"时长非数值: {dur}")
    # 完整提示词标签
    prompt = str(sh.get("prompt", ""))
    if prompt:
        for tag in REQUIRED_TAGS:
            if tag not in prompt:
                issues.append(f"提示词缺标签: {tag}")
    return issues, warnings


def check_scene(scene: dict) -> dict:
    sid = scene.get("scene_id", "?")
    plan = scene.get("plan", {}) or {}
    shots = plan.get("shots") or []
    issues: list[str] = []
    if not shots:
        issues.append("场景无分镜（0 镜）")
    warnings: list[str] = []
    for sh in shots:
        _i, _w = check_shot(sh, sid)
        issues.extend(_i)
        warnings.extend(_w)
    return {"scene_id": sid, "n_shots": len(shots), "issues": issues[:20],
            "warnings": warnings[:10], "ok": not issues}


def run_qa(data: dict) -> dict:
    scenes = data.get("scenes") or []
    if not scenes:
        return {"ok": False, "summary": "无场景数据", "scenes": []}
    reports = [check_scene(s) for s in scenes]
    ok_scenes = [r for r in reports if r["ok"]]
    total_issues = sum(len(r["issues"]) for r in reports)
    return {
        "ok": len(ok_scenes) == len(reports) and total_issues == 0,
        "summary": f"{len(ok_scenes)}/{len(reports)} 场景通过；{total_issues} 个问题",
        "total_shots": sum(r["n_shots"] for r in reports),
        "scenes": reports,
    }


def main():
    path = Path(sys.argv[sys.argv.index("--file") + 1] if "--file" in sys.argv else "")
    data = json.loads(path.read_text(encoding="utf-8"))
    report = run_qa(data)
    print(f"QA: {'✅ 通过' if report['ok'] else '❌ 有问题'} | {report['summary']} | 共{report['total_shots']}镜")
    for r in report["scenes"]:
        mark = "✅" if r["ok"] else "❌"
        print(f"  {mark} {r['scene_id']}: {r['n_shots']}镜" + (f" | {'; '.join(r['issues'][:5])}" if r["issues"] else ""))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
