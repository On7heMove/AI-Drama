# -*- coding: utf-8 -*-
"""P1 修复回归测试（2026-08-31）：P1-7 E2E 路径、P1-8 剧本字数、P1-9 重试统一、P1-10 翻译契约。
全部离线，不调用真实模型/网络/密钥。
"""
from __future__ import annotations
import asyncio
from pathlib import Path

import pytest

from app.production.schemas import TranslatedFields
from app.production.screenplay_render import render_screenplay, screenplay_chars


# ---------------------------------------------------------------- P1-8 剧本字数
def test_render_screenplay_matches_delivery_text():
    eps = [{"ep": 1, "title": "破晓",
            "scenes": [{"location": "避难所", "time": "夜",
                        "action_blocks": ["陈思扬按住抽搐的男人"],
                        "dialogues": [{"speaker": "陈思扬", "line": "按住他！"}]}]}]
    text = render_screenplay(eps)
    assert "## 第1集 破晓" in text
    assert "### 避难所 夜" in text
    assert "（陈思扬按住抽搐的男人）" in text
    assert "陈思扬：按住他！" in text
    # 字数 = 实际剧本文本，而非 JSON 序列化长度
    assert screenplay_chars(eps) == len(text)
    import json
    assert screenplay_chars(eps) < len(json.dumps(eps, ensure_ascii=False))


def test_screenplay_chars_empty():
    assert screenplay_chars([]) == 0
    assert render_screenplay(None) == ""


# ---------------------------------------------------------------- P1-10 翻译契约
def test_translated_fields_defaults_fill_missing():
    tf = TranslatedFields.model_validate({"scene": "A man looks up"})
    assert tf.scene == "A man looks up"
    assert tf.camera == "" and tf.lighting == "" and tf.blocking == ""  # 缺省补默认值
    assert tf.model_dump()["dialogue"] == ""


def test_translated_fields_rejects_wrong_type():
    with pytest.raises(Exception):
        TranslatedFields.model_validate({"scene": 123})  # 类型非法 → 契约拒绝（调用方回退本地）


def test_translate_fields_falls_back_on_bad_contract():
    from app.production import storyboard_export as se

    class _BadClient:
        async def chat(self, system, user, **kw):
            return '{"scene": 123, "staging": "x"}'  # scene 类型非法

    out = asyncio.run(se._translate_fields(
        _BadClient(), "男人抬头", "", "", "", "", camera="", lighting="", blocking="", dialogue=""))
    assert out == {}  # 契约校验失败 → 本地兜底空字典，不崩溃


# ---------------------------------------------------------------- P1-9 重试统一
def test_retryable_error_classification():
    from app.production import llm_client as lc

    class _E(Exception):
        def __init__(self, code=None):
            super().__init__()
            self.status_code = code

    assert lc._is_retryable_error(_E(401)) is False      # 鉴权不重试
    assert lc._is_retryable_error(_E(403)) is False      # 权限不重试
    assert lc._is_retryable_error(_E(400)) is False      # 参数不重试
    assert lc._is_retryable_error(_E(429)) is True       # 限流可重试
    assert lc._is_retryable_error(_E(500)) is True       # 5xx 可重试
    assert lc._is_retryable_error(_E(503)) is True
    assert lc._is_retryable_error(RuntimeError("模型返回空内容（可能 max_tokens 全被推理占用）")) is True
    assert lc._is_retryable_error(TimeoutError()) is True


def test_chat_json_bounded_budget_no_amplification():
    """chat_json 总调用数 = MAX_JSON_ATTEMPTS（不再 chat×chat_json 放大）。"""
    from app.production import llm_client as lc

    calls = {"n": 0}

    class _Msg:
        content = "not a json {"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _Completions:
        async def create(self, **kw):
            calls["n"] += 1
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        chat = _Chat()

    c = lc.DeepSeekClient.__new__(lc.DeepSeekClient)
    c.model = "stub"; c.max_tokens = 100; c.temperature = 0.7; c.timeout = 1.0
    c.usage = lc.LLMUsage()
    c.api_key = "x"; c.base_url = "http://x"; c._client = _FakeClient()

    with pytest.raises(RuntimeError):
        asyncio.run(c.chat_json("s", "u"))
    assert calls["n"] == lc.MAX_JSON_ATTEMPTS  # 恰好总预算，无放大


def test_chat_fails_fast_on_auth_error():
    """非瞬时错误（401）不重试，只调用 1 次。"""
    from app.production import llm_client as lc

    calls = {"n": 0}

    class _AuthErr(Exception):
        status_code = 401

    class _Completions:
        async def create(self, **kw):
            calls["n"] += 1
            raise _AuthErr()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        chat = _Chat()

    c = lc.DeepSeekClient.__new__(lc.DeepSeekClient)
    c.model = "stub"; c.max_tokens = 100; c.temperature = 0.7; c.timeout = 1.0
    c.usage = lc.LLMUsage()
    c.api_key = "x"; c.base_url = "http://x"; c._client = _FakeClient()

    with pytest.raises(_AuthErr):
        asyncio.run(c.chat("s", "u", retries=3))
    assert calls["n"] == 1  # 非瞬时错误快速失败


# ---------------------------------------------------------------- P1-7 E2E 路径
def test_run_real_e2e_paths_portable():
    src = Path(__file__).resolve().parents[2] / "backend" / "run_real_e2e.py"
    code = src.read_text(encoding="utf-8")
    assert "BE_DIR = Path(__file__).resolve().parent" in code      # 不再依赖 cwd/旧目录名
    assert "os.path.abspath(r\"backend\")" not in code
    assert "if __name__ == \"__main__\":" in code                   # 可导入、可测试
    # 旧目录名仅存在于兼容回退分支
    assert "_瀑布流_提示词模块" in code
