"""伏笔台账（§12 L3）：LLM 依 skill prompt 埋伏笔（state_update.open_threads），本地台账跟踪 埋点→回收。

- add(ep, texts)：登记本集新埋伏笔（open_threads）
- payoff(ep, texts)：回收（resolved）——按文本匹配（先精确，再包含）
- open()：未回收清单（生成后续集时注入 prompt）
- to_prompt()：'未回收伏笔（必须推进/兑现）' 段

复用声明：复用 app.production.schemas（无强依赖，独立可测）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Foreshadow:
    id: str
    text: str
    planted_ep: int
    status: str = "open"            # open / payoff
    payoff_ep: int | None = None


class ForeshadowLedger:
    """跨集伏笔台账：登记/回收/查询。"""

    def __init__(self) -> None:
        self.items: list[Foreshadow] = []
        self._seq = 0

    def add(self, ep: int, texts: list[str]) -> None:
        """登记本集新埋伏笔（open_threads）；已存在同文的不重复记。"""
        for t in texts:
            t = (t or "").strip()
            if not t or self._find_open(t):
                continue
            self._seq += 1
            self.items.append(Foreshadow(id=f"fs{self._seq:03d}", text=t, planted_ep=ep))

    def payoff(self, ep: int, texts: list[str]) -> list[str]:
        """回收（resolved）：精确或包含匹配，返回回收的伏笔 id。"""
        paid: list[str] = []
        for t in texts:
            t = (t or "").strip()
            if not t:
                continue
            for it in self.items:
                if it.status == "open" and (t == it.text or t in it.text or it.text in t):
                    it.status = "payoff"
                    it.payoff_ep = ep
                    paid.append(it.id)
        return paid

    def open(self) -> list[Foreshadow]:
        return [it for it in self.items if it.status == "open"]

    def open_texts(self) -> list[str]:
        return [it.text for it in self.open()]

    def _find_open(self, text: str) -> Foreshadow | None:
        for it in self.items:
            if it.status == "open" and (text == it.text or text in it.text or it.text in text):
                return it
        return None

    def to_prompt(self, max_items: int = 12) -> str:
        opens = self.open()[:max_items]
        if not opens:
            return ""
        lines = ["## 未回收伏笔（必须推进或兑现，不得遗忘）"]
        for it in opens:
            lines.append(f"- [{it.id}]（埋于 ep{it.planted_ep}）：{it.text}")
        return "\n".join(lines)

    def summary(self) -> list[dict]:
        return [{"id": it.id, "text": it.text, "planted_ep": it.planted_ep,
                 "status": it.status, "payoff_ep": it.payoff_ep} for it in self.items]
