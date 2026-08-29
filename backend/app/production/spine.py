"""主线骨架与倒推闭环（v0.1 pilot）：梗概 → 大事件骨架 → 选角调度 → 需求-供给闭环 → 螺旋校验 → 报告。

设计基准见 docs/主线骨架与倒推闭环.md。本模块为自包含 pilot：
- 骨架/选角由 LLM 一次推导（带硬规则约束），
- 供需闭环与螺旋校验由本地确定性逻辑完成（可单测、可追溯）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from app import prompt_factory

NODE_TYPES = ("上升", "挫折", "蓄力", "爆发", "收束")
SPIKE_TYPES = ("反转", "强冲突", "情绪顶点", "信息炸弹", "身份揭露", "威胁升级", "关系破裂")
NEED_GROUPS = ("present", "relation", "emotion", "environment", "ability", "consumption")
ROLE_NAMES = {"主角", "女主", "反派", "重要配角", "配角", "工具人"}

# 高门槛行为 → (驱动维度, 阈值, 该维度专用铺垫关键词)（规则2：同型匹配）
HIGH_GATE_KEYWORDS: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "接吻": ("love", 6, ("相处", "心动", "暧昧", "合奏", "陪伴", "同行", "并肩")),
    "吻": ("love", 6, ("相处", "心动", "暧昧", "合奏", "陪伴", "同行", "并肩")),
    "舍命": ("love", 5, ("同行", "共患难", "相救", "守护", "信任", "陪伴", "相处", "并肩", "托付")),
    "以命相救": ("love", 5, ("同行", "共患难", "相救", "守护", "信任", "陪伴", "相处", "并肩", "托付")),
    "舍身": ("love", 5, ("同行", "共患难", "相救", "守护", "信任", "陪伴", "相处", "并肩", "托付")),
    "告白": ("love", 5, ("相处", "心动", "暧昧", "合奏", "陪伴", "同行", "并肩")),
    "背叛": ("hate", 4, ("恶意", "嫉妒", "恨", "欺压", "霸凌", "羞辱", "打压", "伤害", "仇", "积怨", "折磨")),
    "决裂": ("hate", 4, ("恶意", "嫉妒", "恨", "欺压", "霸凌", "羞辱", "打压", "伤害", "仇", "积怨", "折磨")),
    "结盟": ("trust", 4, ("信任", "托付", "并肩", "合作", "交付", "同行", "相救")),
    "托付": ("trust", 4, ("信任", "并肩", "合作", "交付", "相救", "守护")),
    "共死": ("trust", 5, ("同行", "共患难", "相救", "守护", "信任", "陪伴", "并肩")),
}


class SpineNode(BaseModel):
    node_id: str = ""
    type: str = ""                 # 上升/挫折/蓄力/爆发/收束
    title: str = ""
    event: str = ""
    function: str = ""             # 剧情功能
    characters: list[str] = Field(default_factory=list)
    spike: str = ""                # 爆点类型
    hook: str = ""                 # 结尾钩子方向
    ep_range: list[int] = Field(default_factory=list)   # [start, end]
    source: str = "倒推"           # 公式/随机/固定/倒推
    needs: dict[str, list[str]] = Field(default_factory=dict)

    def ep_start(self) -> int:
        return int(self.ep_range[0]) if self.ep_range else 0

    def ep_end(self) -> int:
        return int(self.ep_range[1]) if len(self.ep_range) > 1 else self.ep_start()


class CastingEntry(BaseModel):
    name: str = ""
    role: str = ""                 # 主角/女主/反派/重要配角/配角
    origin: str = "brief"          # brief / derived
    desire: str = ""
    obstacle: str = ""
    arc: str = ""
    mini_line: str = ""            # 派生角色必须带 mini 线
    nodes: list[str] = Field(default_factory=list)      # 交汇的节点 id
    position_reason: dict = Field(default_factory=dict) # 节点 -> 位置理由


class SupplyItem(BaseModel):
    node_id: str = ""
    need_group: str = ""
    need: str = ""
    status: str = "needs_review"   # supplied / missing / needs_review
    supply: str = ""               # 由什么供给
    via: str = ""                  # 供给来源（节点/角色/铺垫链）


class SpinePlan(BaseModel):
    spine_title: str = ""
    nodes: list[SpineNode] = Field(default_factory=list)
    casting: list[CastingEntry] = Field(default_factory=list)
    supply_checks: list[SupplyItem] = Field(default_factory=list)
    spiral_issues: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    expected_emotion_arc: dict | None = None      # §14 C7 前置：期望情绪弧线（形状/峰谷锚点）

    def node(self, nid: str) -> SpineNode | None:
        return next((n for n in self.nodes if n.node_id == nid), None)

    def char(self, name: str) -> CastingEntry | None:
        return next((c for c in self.casting if c.name == name), None)


# ---------------------------------------------------------------- 系统提示与规则
SYSTEM = (
    "你是横屏 16:9 短剧主架构师（主线骨架师）。任务：从故事梗概与固定设定推导「主线骨架（8~15 个大事件节点）+ 人物池选角」。"
    "只输出 JSON，不要 Markdown、不要注释、不要多余文字。"
)

RULES = """# 硬规则（不可违背）
1. 大事件判定三原则：① 使主角在 能力/认知/关系/地位 至少一维发生不可逆阶跃/断裂；
   ② 必须被后续剧情消化（是后续事件的因）；③ 螺旋包络——整体螺旋上升，挫折后必有回升。
2. 节点类型限：上升/挫折/蓄力/爆发/收束。收束节点必须为正/留白收尾（本剧为正向）。
3. 每节点至少服务一个爆点类型：反转/强冲突/情绪顶点/信息炸弹/身份揭露/威胁升级/关系破裂。
4. 节点给 ep_range（1~80 集），整体大致覆盖 80 集；不锁死单集。
5. 每个节点的 needs 必须按组声明（可空）：
   present=谁必须在场；relation=需要什么关系/信任度；emotion=需要什么情绪铺垫（如 love≥6，写明阈值）；
   environment=需要什么环境/场景烘托；ability=需要主角具备什么能力/资源；consumption=本节点的后果必须在后续被消化（写明被谁消化）。
6. 选角：配角=可复用人物池。禁止一次性工具人。节点需要角色时，先复用池内角色（给位置合理性：为什么他此刻在此）。
   必须引入新角色时，必须自带 mini 线（desire/obstacle/arc），注册入池供后续复用。
7. 人物初遇/关系建立要给出位置理由（婚礼→共同朋友；医院→家属/医生；公司→同事 这类逻辑）。
8. 若剧情需要高门槛情绪行为（接吻/舍命/背叛/结盟等），必须在 needs.emotion 声明所需阈值与铺垫方向。
9. 严格遵守梗概事实与固定设定；不得杜撰未定的关键事实（如关键角色姓名未定，用其身份指代并在 open_questions 列出）。
10. 动机类型显式化：任何高代价行为（舍命/背叛/弃文明/告发/结盟/交付）必须声明驱动类型（价值观/情感/义务/利益），
    写进 needs.emotion 或 needs.relation；不声明驱动类型的行为视为推导缺陷。
11. 动机与供给同型匹配：声明情感驱动→配对应向量铺垫链；声明价值观驱动→配价值观锚点（历史事件/教训/承诺/对照者）。
    类型不匹配的铺垫无效（love 铺垫救不了价值观驱动的行为）。
12. 因果合理性（骨架是纲领）：允许为剧情需要引入新人物/新情节（这是骨架的正当职能）；但被引入的人物与情节一旦成立，其行为与事件必须在「合理初始向量 + 合理情节推动」下自洽——
    每个行为要么被初始状态支撑，要么被先前情节推动，不得凭空。额外效果（彩蛋）不要求，出现是惊喜。
13. 汇聚点必须产出因果资产：≥2 条线汇聚的节点，必须声明碰撞产物（关系变化/信任转移/立场转换）及其后续消化位置；
    没有产物的汇聚是无效汇聚。
14. 历史镜像锚点（可选增强，非强制）：主角的高价值选择如能找到配角/反派在相似情境下的先行选择做对照，可同时立动机、深化主题、给配角线赋因果；当前题材优先保证合理性，玄幻等无自然对照者的题材不要求。
15. 人物池规模（硬约束）：重要配角（含反派）至少 4 个具名角色，禁止把人物网过度精简；反派必须具名并自带 mini 线（desire/obstacle/arc），不得用"XX领袖"这类群体化身代替具名反派；角色线须覆盖 亲情线/友情线/对立线 至少各一条承载（如同伴/家人/对手），禁止整体砍掉家庭或同伴线；所有角色一律注册入池复用，禁止一次性工具人。"""


def _constraints_text(constraints: list[str]) -> str:
    return "\n".join(f"- {c}" for c in constraints if c.strip())


# ---------------------------------------------------------------- LLM 推导
async def build_spine(
    brief_text: str,
    constraints: list[str],
    client: object,
    max_tokens: int = 24000,
    cache_path: str = "",
) -> SpinePlan:
    user = (
        "## 故事梗概\n" + brief_text
        + "\n\n## 固定设定（硬约束，不可违背）\n" + (_constraints_text(constraints) or "（无）")
        + "\n\n## 推导规则\n" + RULES
        + "\n\n## 输出 JSON 结构\n"
        + """{
  "spine_title": "标题",
  "nodes": [{"node_id":"n01","type":"挫折","title":"…","event":"…","function":"…",
             "characters":["…"],"spike":"反转","hook":"…","ep_range":[1,8],
             "source":"公式|随机|固定|倒推",
             "needs":{"present":[],"relation":[],"emotion":[],"environment":[],"ability":[],"consumption":[]}}],
  "casting": [{"name":"…","role":"主角|女主|反派|重要配角|配角","origin":"brief|derived",
               "desire":"…","obstacle":"…","arc":"…","mini_line":"（派生角色必填）",
               "nodes":["n01"],"position_reason":{"n01":"为什么他此刻在此"}}],
  "open_questions": []
}"""
        + ("\r\n\r\n" + prompt_factory.inject_skill("build_spine") if prompt_factory.inject_skill("build_spine") else "")
        + "\r\n\r\n" + prompt_factory.default_negatives()
    )
    if cache_path:
        _cp = Path(cache_path)
        if _cp.exists():
            data = json.loads(_cp.read_text(encoding="utf-8"))
            print(f"[spine] 使用缓存推导结果：{_cp}", flush=True)
        else:
            data = await client.chat_json(SYSTEM, user, max_tokens=max_tokens)
            _cp.parent.mkdir(parents=True, exist_ok=True)
            _cp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[spine] 已缓存推导结果：{_cp}", flush=True)
    else:
        data = await client.chat_json(SYSTEM, user, max_tokens=max_tokens)
    plan = _coerce(data)
    _run_closure(plan)
    _check_spiral(plan)
    return plan


_TERM_CLEAN = [
    ("美南旅途", "北美→南美旅途"),
    ("美南同行", "北美→南美旅途同行"),
    ("美南", "北美-南美"),
]


def _clean(text: object) -> object:
    """术语清洗：把初稿遗留的歧义缩略（美南=北美→南美旅途）改写为明确表达。"""
    if isinstance(text, str):
        for old, new in _TERM_CLEAN:
            text = text.replace(old, new)
        return text
    if isinstance(text, list):
        return [_clean(x) for x in text]
    if isinstance(text, dict):
        return {k: _clean(v) for k, v in text.items()}
    return text


def _coerce(data: dict) -> SpinePlan:
    """容错清洗：字符串→列表、ep_range 数字→int、缺失组补空。"""
    nodes: list[SpineNode] = []
    for i, n in enumerate(data.get("nodes") or [], 1):
        n = dict(n)
        for _k in ("title", "event", "function", "hook", "characters", "needs"):
            if _k in n:
                n[_k] = _clean(n[_k])
        n["node_id"] = str(n.get("node_id") or f"n{i:02d}")
        n["type"] = str(n.get("type") or "上升")
        if n["type"] not in NODE_TYPES:
            n["type"] = "上升"
        for k in ("characters",):
            if not isinstance(n.get(k), list):
                n[k] = [str(n[k])] if n.get(k) else []
        for k in ("spike", "hook"):
            n.setdefault(k, "")
        er = n.get("ep_range") or []
        n["ep_range"] = [int(x) for x in er if str(x).lstrip("-").isdigit()][:2]
        needs = {}
        for g in NEED_GROUPS:
            v = n.get("needs", {}).get(g) if isinstance(n.get("needs"), dict) else None
            needs[g] = [str(x) for x in (v or []) if str(x).strip()]
        n["needs"] = needs
        nodes.append(SpineNode(**n))
    casting: list[CastingEntry] = []
    for c in data.get("casting") or []:
        c = dict(c)
        for _k in ("name", "desire", "obstacle", "arc", "mini_line", "position_reason"):
            if _k in c:
                c[_k] = _clean(c[_k])
        c["name"] = str(c.get("name") or "").strip()
        if not c["name"]:
            continue
        c["role"] = str(c.get("role") or "配角")
        c["origin"] = str(c.get("origin") or "derived")
        for k in ("nodes",):
            if not isinstance(c.get(k), list):
                c[k] = [str(c[k])] if c.get(k) else []
        c["position_reason"] = c.get("position_reason") or {}
        casting.append(CastingEntry(**c))
    return SpinePlan(
        spine_title=str(data.get("spine_title") or ""),
        nodes=nodes,
        casting=casting,
        open_questions=[str(x) for x in (data.get("open_questions") or [])],
    )


# ---------------------------------------------------------------- 需求-供给闭环（确定性）
def _run_closure(plan: SpinePlan) -> None:
    checks: list[SupplyItem] = []
    for node in plan.nodes:
        for g in NEED_GROUPS:
            for need in node.needs.get(g, []):
                status, supply, via = _judge(plan, node, g, need)
                checks.append(SupplyItem(node_id=node.node_id, need_group=g, need=need,
                                         status=status, supply=supply, via=via))
    plan.supply_checks = checks


def _judge(plan: SpinePlan, node: SpineNode, group: str, need: str) -> tuple[str, str, str]:
    earlier = [n for n in plan.nodes if n.ep_end() < node.ep_start()]
    later = [n for n in plan.nodes if n.ep_start() > node.ep_end()]
    node_text = node.event + node.function + node.title
    if group == "present":
        # 需求里提到的角色是否在人物池（按名字/身份匹配）
        hits = [c for c in plan.casting if c.name and c.name in need]
        if hits:
            return "supplied", f"人物池已有：{', '.join(c.name for c in hits)}", "casting"
        for c in plan.casting:
            if c.role and c.role in need:
                return "supplied", f"人物池匹配角色：{c.name}（{c.role}）", "casting"
        return "missing", "需求角色不在人物池（或为未引入新角色）", "casting"
    if group == "environment":
        # 环境/场景是场景级细节：节点自身设定即供给；骨架层不判 fatal
        if need in node_text or len(need) <= 8:
            return "supplied", "节点自身环境/场景", "节点内"
        return "needs_review", "场景级细节，留待大纲/分镜层供给", "场景供给"
    if group == "ability":
        # 资源/能力：本节点内获得即供给；否则待大纲层补足（不判 fatal）
        if need in node_text:
            return "supplied", "本节点内获得/使用", "节点内"
        hits = [n for n in earlier if n.type in ("上升", "蓄力") and need in (n.event + n.function)]
        if hits:
            return "supplied", "更早节点提供：" + "、".join(h.node_id for h in hits[-2:]), "前置节点"
        return "needs_review", "能力/资源前置待大纲层补足", "前置节点"
    if group in ("relation", "emotion"):
        # 驱动类型判定：声明"不牺牲/原则/价值观/底线/义务/职责"=价值观/义务驱动；否则默认情感驱动
        value_driven = any(k in node_text for k in ("价值观", "不牺牲", "原则", "底线", "义务", "职责", "承诺"))
        # 高门槛行为预检：接吻/舍命/背叛/结盟等，必须已有铺垫链
        for hw, (dim, thr, kws) in HIGH_GATE_KEYWORDS.items():
            if hw in need or hw in node.event:
                if value_driven:
                    anchors = ("父亲", "托付", "承诺", "遗孤", "独行", "守护", "母亲", "教训")
                    hits = [n for n in earlier if any(a in (n.event + n.function) for a in anchors)]
                    if hits:
                        return "supplied", f"「{hw}」为价值观驱动，锚点由更早节点供给：" + "、".join(h.node_id for h in hits[-2:]), "前置节点"
                    return "missing", f"「{hw}」声明价值观驱动，但更早节点未见价值观锚点（父亲之死/托付/承诺/独行）", "前置节点"
                hits = [n for n in earlier if any(k in (n.event + n.function) for k in kws)]
                if hits:
                    return "supplied", f"高门槛行为「{hw}」铺垫链已由更早节点提供：" + "、".join(h.node_id for h in hits[-2:]), "前置节点"
                return "missing", f"高门槛行为「{hw}」需要 {dim}≥{thr} 的铺垫链（{dim} 维度专用铺垫），但前置节点未见明确铺垫", "派生账本预检"
        if not earlier:
            # 开局节点：初始状态/初始关系由梗概前提供给，骨架层无法验证
            return "needs_review", "开局节点初始状态由梗概前提供给，骨架层不判", "梗概前提"
        kw = ("信任", "同行", "共患难", "相救", "相处", "并肩", "守护", "陪伴", "铺垫", "拉近", "互助", "交付", "托付", "合奏")
        hits = [n for n in earlier if any(k in (n.event + n.function) for k in kw)]
        if hits:
            return "supplied", "更早节点已提供关系/情绪铺垫：" + "、".join(n.node_id for n in hits[-2:]), "前置节点"
        return "needs_review", "未能确定前置铺垫是否存在（建议编剧复核）", "派生账本"
    if group == "consumption":
        # 末尾收束节点：主线在此收束，无需后续消化
        if not later:
            return "supplied", "末尾收束节点，无需后续消化", "闭包完整性"
        # 需求若自述消化路径，或提到后续站/事件名且后续节点出现 → 视为已计划
        if any(k in need for k in ("由后续", "必须被", "在后续", "被后续", "由最后", "由欧洲", "由南美", "由北美", "由澳大利亚")):
            return "supplied", "需求已自述消化路径", "需求声明"
        stations = ("北美", "南美", "澳大利亚", "非洲", "欧洲", "南充", "合体", "真相", "激活", "回归", "反扑", "重逢", "执政者")
        need_stations = [s for s in stations if s in need]
        if need_stations:
            hits = [n for n in later if any(s in (n.event + n.function) for s in need_stations)]
            if hits:
                return "supplied", "后续节点消化：" + "、".join(h.node_id for h in hits[:2]), "后续节点"
        keys = re.findall(r"[\u4e00-\u9fff]{2,6}", node.event)
        hits = [n for n in later if any(k in (n.event + n.function) for k in keys[:6])]
        if hits:
            return "supplied", "后续节点消化：" + "、".join(h.node_id for h in hits[:2]), "后续节点"
        return "needs_review", "后果消化未能在骨架层确认（建议编剧复核）", "闭包完整性"
    return "needs_review", "", ""


# ---------------------------------------------------------------- 螺旋校验
def _check_spiral(plan: SpinePlan) -> None:
    issues: list[str] = []
    if not plan.nodes:
        issues.append("骨架为空：无大事件节点")
        return
    seq = [n.type for n in plan.nodes]
    up = seq.count("上升") + seq.count("爆发")
    down = seq.count("挫折")
    if up <= down:
        issues.append(f"螺旋包络异常：上升/爆发节点({up}) 未显著多于挫折节点({down})，整体未见上升趋势")
    # 挫折后必有回升
    for i, n in enumerate(plan.nodes):
        if n.type != "挫折":
            continue
        later = [x for x in plan.nodes[i + 1:] if x.type in ("上升", "蓄力", "爆发")]
        if not later:
            issues.append(f"节点 {n.node_id}（挫折）后无回升节点，螺旋断裂")
    # 收束必须在最后
    endings = [i for i, n in enumerate(plan.nodes) if n.type == "收束"]
    if endings and endings[-1] != len(plan.nodes) - 1:
        issues.append("收束节点不在骨架末尾")
    # ep 覆盖
    eps = sorted(e for n in plan.nodes for e in range(n.ep_start(), n.ep_end() + 1))
    if eps:
        missing = [e for e in range(1, 81) if e not in set(eps)]
        if len(missing) > 10:
            issues.append(f"ep 覆盖缺口过大：{len(missing)} 集未被任何节点覆盖")
    plan.spiral_issues = issues


# ---------------------------------------------------------------- 报告渲染

def render_html(plan: SpinePlan, constraints: list[str] | None = None) -> str:
    """深色中英风格 HTML（与素材卡报告一致），结构化渲染骨架/选角/闭环/螺旋。"""
    css = """
:root{--bg:#0b1020;--panel:#141b30;--panel2:#1a2340;--line:#2a3554;--txt:#e8ecf7;--sub:#9aa7c7;--dim:#6b7799}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 20% -10%, #16224a 0%, var(--bg) 55%);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif;line-height:1.7;padding:28px 20px 80px}
.wrap{max-width:1180px;margin:0 auto}
header.top{text-align:center;margin-bottom:24px}
header.top h1{font-size:30px;background:linear-gradient(90deg,#9cc3ff,#7fe3ff,#c9a7ff);-webkit-background-clip:text;background-clip:text;color:transparent}
header.top .sub{color:var(--sub);margin-top:8px;font-size:14px}
.stats{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-bottom:22px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 14px;font-size:13px;color:var(--sub)}
.stat b{color:var(--txt)}
section{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin:16px 0}
section h2{color:#7fe3ff;font-size:20px;margin-bottom:12px;border-left:4px solid #4f8cff;padding-left:10px}
section h3{color:#9cc3ff;font-size:16px;margin:14px 0 8px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
th{color:#9cc3ff;text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);font-weight:600}
td{padding:7px 8px;border-bottom:1px solid rgba(42,53,84,.5);color:var(--txt);vertical-align:top;word-break:break-word;white-space:normal}
tr:last-child td{border-bottom:none}
.node-card{background:rgba(0,0,0,.18);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.node-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:6px}
.badge{color:#fff;font-size:12px;padding:2px 10px;border-radius:12px;font-weight:600}
.badge-up{background:#27ae60}.badge-down{background:#e74c3c}.badge-charge{background:#f39c12}.badge-burst{background:#d35400}.badge-end{background:#8e44ad}
.node-title{font-size:16px;font-weight:700}
.node-meta{color:var(--sub);font-size:12.5px;margin:2px 0 6px}
.needs{margin-top:6px;font-size:13px}
.needs b{color:#9cc3ff}
.req{padding:2px 0}
.ok{color:#6fcf97}.fatal{color:#ff7675}.warn{color:#f9ca24}.scene{color:#74b9ff}
.footer{text-align:center;color:var(--dim);font-size:12px;margin-top:26px}
@media (max-width:760px){table{font-size:12px}.wrap{max-width:100%}}
""".strip()
    _e = lambda v: str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    TYPE_BADGE = {"上升": ("badge-up", "上升 / Rising"), "挫折": ("badge-down", "挫折 / Setback"),
                  "蓄力": ("badge-charge", "蓄力 / Setup"), "爆发": ("badge-burst", "爆发 / Burst"),
                  "收束": ("badge-end", "收束 / Ending")}
    parts: list[str] = []
    # 0 硬约束
    if constraints:
        items = "".join(f"<li>{_e(c)}</li>" for c in constraints)
        parts.append(f"<section><h2>输入与硬约束 / Input &amp; Constraints</h2><ul style='padding-left:18px'>{items}</ul></section>")
    # 1 骨架总表
    rows = []
    for n in plan.nodes:
        er = f"{n.ep_start()}-{n.ep_end()}" if n.ep_range else "?"
        badge = TYPE_BADGE.get(n.type, ("badge-charge", n.type))
        rows.append(f"<tr><td>{_e(n.node_id)}</td><td><span class='badge {badge[0]}'>{_e(n.type)}</span></td>"
                    f"<td>{_e(n.event)}</td><td>{_e(n.function)}</td><td>{_e(n.spike)}</td>"
                    f"<td>{_e(n.hook)}</td><td>{er}</td><td>{_e(n.source)}</td></tr>")
    parts.append(
        "<section><h2>主线骨架 / Mainline Spine</h2>"
        f"<p style='color:var(--sub);font-size:13px'>{len(plan.nodes)} 个大事件节点 · 螺旋包络结构</p>"
        "<table><tr><th>节点</th><th>类型</th><th>事件</th><th>功能</th><th>爆点</th><th>钩子</th><th>集数</th><th>来源</th></tr>"
        + "".join(rows) + "</table></section>")
    # 节点明细卡片
    cards = []
    for n in plan.nodes:
        badge = TYPE_BADGE.get(n.type, ("badge-charge", n.type))
        er = f"{n.ep_start()}-{n.ep_end()}" if n.ep_range else "?"
        need_rows = []
        for g in NEED_GROUPS:
            if n.needs.get(g):
                need_rows.append(f"<div class='req'><b>{_e(g)}</b>：{_e('；'.join(n.needs[g]))}</div>")
        needs_html = f"<div class='needs'>{''.join(need_rows)}</div>" if need_rows else ""
        cards.append(
            f"<div class='node-card'><div class='node-head'><span class='badge {badge[0]}'>{_e(n.type)}</span>"
            f"<span class='node-title'>{_e(n.node_id)}｜{_e(n.title)}</span></div>"
            f"<div class='node-meta'>集数 {er} · 爆点 {_e(n.spike or '—')} · 来源 {_e(n.source)}"
            f"{' · 参与 ' + _e('、'.join(n.characters)) if n.characters else ''}</div>"
            f"<div>事件：{_e(n.event)}</div>"
            + (f"<div>功能：{_e(n.function)}</div>" if n.function else "")
            + (f"<div>钩子：{_e(n.hook)}</div>" if n.hook else "")
            + needs_html + "</div>")
    parts.append("<section><h2>节点明细与需求声明 / Node Details &amp; Needs</h2>" + "".join(cards) + "</section>")
    # 2 选角
    rows = []
    for c in plan.casting:
        pr = "；".join(f"{k}:{v}" for k, v in list(c.position_reason.items())[:2])
        rows.append(f"<tr><td>{_e(c.name)}</td><td>{_e(c.role)}</td><td>{_e(c.origin)}</td>"
                    f"<td>{_e(c.desire[:32])}</td><td>{_e(c.obstacle[:28])}</td>"
                    f"<td>{_e(c.mini_line[:30])}</td><td>{_e(c.arc[:24])}</td><td>{_e(','.join(c.nodes[:4]))}</td><td>{_e(pr[:44])}</td></tr>")
    parts.append("<section><h2>选角调度 / Casting</h2>"
                 "<table><tr><th>角色</th><th>身份</th><th>来源</th><th>欲望</th><th>阻力</th><th>mini线</th><th>弧光</th><th>交汇节点</th><th>位置理由</th></tr>"
                 + "".join(rows) + "</table></section>")
    # 3 闭环
    fatal = [s for s in plan.supply_checks if s.status == "missing"]
    soft = [s for s in plan.supply_checks if s.status == "needs_review" and s.need_group in ("relation", "emotion", "consumption")]
    scene = [s for s in plan.supply_checks if s.status == "needs_review" and s.need_group in ("environment", "ability")]
    ok = [s for s in plan.supply_checks if s.status == "supplied"]
    closure = [f"<span class='stat'>supplied <b>{len(ok)}</b></span>",
               f"<span class='stat'>fatal <b>{len(fatal)}</b></span>",
               f"<span class='stat'>编剧复核 <b>{len(soft)}</b></span>",
               f"<span class='stat'>大纲层清单 <b>{len(scene)}</b></span>"]
    parts.append("<section><h2>需求-供给闭环 / Supply Closure</h2><div class='stats'>" + "".join(closure) + "</div>")
    if fatal:
        rows = "".join(f"<tr><td>{_e(s.node_id)}</td><td>{_e(s.need_group)}</td><td>{_e(s.need)}</td>"
                       f"<td class='fatal'>{_e(s.supply)}</td></tr>" for s in fatal)
        parts.append("<h3>⛔ 设计期 fatal 缺口</h3><table><tr><th>节点</th><th>组</th><th>需求</th><th>缺口</th></tr>" + rows + "</table>")
    if soft:
        items = "".join(f"<li class='warn'>{_e(s.node_id)} [{_e(s.need_group)}] {_e(s.need)}：{_e(s.supply)}</li>" for s in soft)
        parts.append("<h3>⚠️ 编剧复核项</h3><ul style='padding-left:18px'>" + items + "</ul>")
    if scene:
        items = "".join(f"<li class='scene'>{_e(s.node_id)} [{_e(s.need_group)}] {_e(s.need)}</li>" for s in scene[:12])
        more = f"<li class='scene'>…等 {len(scene)} 项</li>" if len(scene) > 12 else ""
        parts.append(f"<h3>📋 大纲/分镜层供给清单（{len(scene)} 项）</h3><ul style='padding-left:18px'>" + items + more + "</ul>")
    parts.append("</section>")
    # 4 螺旋
    sp = "".join(f"<li style='color:#ff7675'>⚠️ {_e(x)}</li>" for x in plan.spiral_issues) or "<li style='color:#6fcf97'>✅ 螺旋包络、挫折回升、收束位置、ep 覆盖均通过</li>"
    parts.append(f"<section><h2>螺旋结构校验 / Spiral Check</h2><ul style='padding-left:18px'>{sp}</ul></section>")
    # 5 开放问题
    if plan.open_questions:
        q = "".join(f"<li>{_e(x)}</li>" for x in plan.open_questions)
        parts.append("<section><h2>开放问题 / Open Questions</h2><ul style='padding-left:18px'>" + q + "</ul></section>")
    today = __import__("datetime").date.today().isoformat()
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        f"<title>《{_e(plan.spine_title or '未命名')}》主线骨架与倒推闭环 · 设计期报告</title><style>{css}</style></head><body><div class='wrap'>"
        f"<header class='top'><h1>《{_e(plan.spine_title or '未命名')}》主线骨架与倒推闭环</h1>"
        "<div class='sub'>Mainline Spine &amp; Reverse Derivation · 设计期报告 v0.1</div></header>"
        f"<div class='stats'><span class='stat'>大事件节点 <b>{len(plan.nodes)}</b></span>"
        f"<span class='stat'>人物池 <b>{len(plan.casting)}</b></span>"
        f"<span class='stat'>需求 <b>{len(plan.supply_checks)}</b></span>"
        f"<span class='stat'>生成 <b>{today}</b></span></div>"
        + "".join(parts)
        + "<footer>设计基准：docs/主线骨架与倒推闭环.md ｜ 按「交付→反馈→调参→再试」循环迭代</footer>"
        + "</div></body></html>"
    )
    return html

def _esc(v: str) -> str:
    return str(v).replace("|", "｜")


def render_markdown(plan: SpinePlan, brief_file: str = "", constraints: list[str] | None = None) -> str:
    L: list[str] = []
    L.append(f"# 《{plan.spine_title or '未命名'}》主线骨架与倒推闭环（设计期报告 v0.1）")
    L.append("")
    L.append("> 生成依据：`docs/主线骨架与倒推闭环.md`（v0.1）｜数据源：用户梗概｜"
             "本报告为设计期交付物，供编剧抽检反馈迭代。")
    L.append("")
    L.append("## 0. 输入与硬约束")
    if constraints:
        L.append("")
        for c in constraints:
            L.append(f"- {c}")
    L.append("")
    L.append(f"## 1. 主线骨架（{len(plan.nodes)} 个大事件节点）")
    L.append("")
    L.append("| 节点 | 类型 | 事件 | 功能 | 爆点 | 钩子 | 集数 | 来源 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for n in plan.nodes:
        er = f"{n.ep_start()}-{n.ep_end()}" if n.ep_range else "?"
        L.append(f"| {n.node_id} | {n.type} | {_esc(n.event)} | {_esc(n.function)} | {_esc(n.spike)} | {_esc(n.hook)} | {er} | {n.source} |")
    L.append("")
    L.append("### 节点明细与需求声明")
    for n in plan.nodes:
        L.append(f"**{n.node_id}｜{n.type}｜{n.title}**（{n.ep_start()}-{n.ep_end()}，爆点：{n.spike or '—'}）")
        L.append(f"- 事件：{n.event}")
        if n.function:
            L.append(f"- 功能：{n.function}")
        if n.hook:
            L.append(f"- 钩子：{n.hook}")
        if n.characters:
            L.append(f"- 参与：{', '.join(n.characters)}")
        for g in NEED_GROUPS:
            if n.needs.get(g):
                L.append(f"- 需求[{g}]：{'；'.join(n.needs[g])}")
        L.append("")
    L.append("## 2. 选角调度（人物池）")
    L.append("")
    L.append("| 角色 | 身份 | 来源 | 欲望 | 阻力 | mini线 | 弧光 | 交汇节点 | 位置理由 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for c in plan.casting:
        pr = "；".join(f"{k}:{v}" for k, v in list(c.position_reason.items())[:2])
        L.append(f"| {c.name} | {c.role} | {c.origin} | {_esc(c.desire[:30])} | {_esc(c.obstacle[:24])} | "
                 f"{_esc(c.mini_line[:30])} | {_esc(c.arc[:24])} | {','.join(c.nodes[:4])} | {_esc(pr[:40])} |")
    L.append("")
    L.append("## 3. 需求-供给闭环校验")
    L.append("")
    fatal = [s for s in plan.supply_checks if s.status == "missing"]
    soft = [s for s in plan.supply_checks if s.status == "needs_review" and s.need_group in ("relation", "emotion", "consumption")]
    scene = [s for s in plan.supply_checks if s.status == "needs_review" and s.need_group in ("environment", "ability")]
    ok = [s for s in plan.supply_checks if s.status == "supplied"]
    L.append(f"共 {len(plan.supply_checks)} 条需求：**supplied {len(ok)} / fatal {len(fatal)} / 编剧复核 {len(soft)} / 大纲层供给清单 {len(scene)}**")
    if fatal:
        L.append("")
        L.append("### ⛔ 设计期 fatal 缺口（补不齐=后续必出因果断裂，必须先解决）")
        L.append("")
        L.append("| 节点 | 需求组 | 需求 | 缺口 |")
        L.append("|---|---|---|---|")
        for s in fatal:
            L.append(f"| {s.node_id} | {s.need_group} | {_esc(s.need)} | {_esc(s.supply)} |")
    if soft:
        L.append("")
        L.append("### ⚠️ 编剧复核项（关系/情绪/后果消化的软性因果，建议抽检确认）")
        for s in soft:
            L.append(f"- {s.node_id} [{s.need_group}] {_esc(s.need)}：{_esc(s.supply)}")
    if scene:
        L.append("")
        L.append(f"### 📋 大纲/分镜层供给清单（{len(scene)} 项：环境与资源细节，不阻塞骨架，留待大纲与分镜填充）")
        for s in scene[:12]:
            L.append(f"- {s.node_id} [{s.need_group}] {_esc(s.need)}")
        if len(scene) > 12:
            L.append(f"- …等 {len(scene)} 项")
    if not fatal and not soft and not scene:
        L.append("")
        L.append("（无 fatal、无复核项、无供给清单）")
    L.append("")
    L.append("## 4. 螺旋结构校验")
    L.append("")
    if plan.spiral_issues:
        for x in plan.spiral_issues:
            L.append(f"- ⚠️ {x}")
    else:
        L.append("- ✅ 螺旋包络、挫折回升、收束位置、ep 覆盖均通过")
    L.append("")
    if plan.open_questions:
        L.append("## 5. 开放问题（待团队/编剧确认）")
        L.append("")
        for q in plan.open_questions:
            L.append(f"- {q}")
        L.append("")
    L.append("---")
    L.append("*本报告为设计期交付物；按「交付→反馈→调参→再试」循环迭代。*")
    return "\n".join(L)
