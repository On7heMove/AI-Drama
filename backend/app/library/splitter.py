# -*- coding: utf-8 -*-
"""app.library.splitter：章回节卷部文本切分（backend 合并补齐）。"""
from __future__ import annotations
import re

_CHAPTER_RE = re.compile(r"^\s*#{0,4}\s*第\s*([0-9零一二三四五六七八九十百千]+)\s*(章|回|节|卷|部)\s*[：:、\s]*")

def split_chapters(text: str) -> list[tuple[str, str]]:
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    hits = [(i, ln.strip()) for i, ln in enumerate(lines) if _CHAPTER_RE.match(ln)]
    if not hits:
        return [("", text.strip())]
    out = []
    for idx, (start, title) in enumerate(hits):
        end = hits[idx + 1][0] if idx + 1 < len(hits) else len(lines)
        body = "\n".join(l for l in lines[start + 1:end] if l.strip()).strip()
        out.append((title[:60], body))
    return out
