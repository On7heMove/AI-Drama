"""DeepSeek 生成客户端：OpenAI 兼容接口，统一 重试/超时/token 统计/JSON 解析。

A1 决策：模型=deepseek-v4-flash（推理模型），使用现有 DEEPSEEK_API_KEY。
推理模型会在 completion_tokens 中单列 reasoning_tokens，成本统计单独记录。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import settings
from app.paths import runtime_dir


@dataclass
class LLMUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    input_chars: int = 0
    output_chars: int = 0
    # DeepSeek 计价（元/百万 token）——估算用，试点后按真实账单校准
    cost_input_per_m: float = 2.0
    cost_output_per_m: float = 8.0

    def add(self, prompt_chars: int, output_chars: int, usage: object | None) -> None:
        self.calls += 1
        self.input_chars += prompt_chars
        self.output_chars += output_chars
        if usage is not None:
            self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            details = getattr(usage, "completion_tokens_details", None)
            if details is not None:
                self.reasoning_tokens += int(getattr(details, "reasoning_tokens", 0) or 0)
        else:  # 无 usage 时的字符估算兜底（中文约 0.7 token/字）
            self.input_tokens += int(prompt_chars * 0.7)
            self.output_tokens += int(output_chars * 0.7)

    @property
    def est_cost_yuan(self) -> float:
        return (
            self.input_tokens / 1_000_000 * self.cost_input_per_m
            + self.output_tokens / 1_000_000 * self.cost_output_per_m
        )

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "est_cost_yuan": round(self.est_cost_yuan, 4),
        }


def _load_env_file() -> dict[str, str]:
    """兜底：cwd 不在 backend 时手动读取 backend/.env（不打印密钥）。"""
    env_path = runtime_dir() / ".env"
    out: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


class DeepSeekClient:
    def __init__(
        self,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float = 180.0,
    ) -> None:
        env = _load_env_file()
        self.model = model or settings.llm_model
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.temperature = settings.llm_temperature if temperature is None else temperature
        self.timeout = timeout
        self.usage = LLMUsage()
        # Key 来源统一：.env > 设置页保存的 llm_api_key（settings_store 加密存储）
        # 客户在交付 exe 里通过设置页填 Key，生成链路必须能读到，否则报"未配置"
        api_key = settings.deepseek_api_key or env.get("DEEPSEEK_API_KEY", "")
        base_url = settings.openai_base_url or env.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        if not api_key:
            try:
                from app.services import settings_store
                saved = (settings_store.get_settings().get("llm_api_key") or "").strip()
                if saved:
                    api_key = saved
            except Exception:  # noqa: BLE001  设置存储不可用时忽略
                pass
        self.api_key = api_key
        self.base_url = base_url
        if not api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY（backend/.env 或 设置页）")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens or self.max_tokens,
                    temperature=self.temperature if temperature is None else temperature,
                    timeout=self.timeout,
                )
                content = (resp.choices[0].message.content or "").strip()
                self.usage.add(len(system) + len(user), len(content), resp.usage)
                if not content:
                    raise RuntimeError("模型返回空内容（可能 max_tokens 全被推理占用）")
                return parse_json(content) if json_mode else content
            except Exception as e:  # noqa: BLE001
                last_err = e
                await asyncio.sleep(2**attempt)
        raise RuntimeError(f"LLM 调用失败（3 次重试后）: {last_err}")

    async def chat_json(self, system: str, user: str, **kw: object) -> dict:
        last_exc: Exception | None = None
        for _ in range(3):
            text = await self.chat(system, user, json_mode=True, **kw)
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                last_exc = exc
                await asyncio.sleep(1.0)
        raise RuntimeError(f"LLM 返回非 JSON（3 次重试后）：{last_exc}") from last_exc


def parse_json(text: str) -> str:
    """从模型输出中提取 JSON 文本（容错代码块/前后缀/散文包裹）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    # 优先按花括号对象截取；无对象再按数组截取；去掉尾部散文
    lb = text.find("{")
    rb = text.rfind("}")
    lbr = text.find("[")
    rbr = text.rfind("]")
    if lb >= 0 and rb > lb and (lbr < 0 or lb <= lbr):
        return text[lb : rb + 1].strip()
    if lbr >= 0 and rbr > lbr:
        return text[lbr : rbr + 1].strip()
    return text.strip()

async def ensure_llm_ready(transport=None, api_key: str | None = None) -> dict:
    """LLM 链路前置校验（生成提示词前强制调用，未配置/不可用即报错阻断）。

    校验两件事：
    1. API Key 是否配置（默认复用 DeepSeekClient 构造的 .env 解析，与生成实际用同一套配置；
       也可显式传入 api_key 覆盖——供设置页"Key 校验测试"注入 正确/错误 key）；
    2. Key 是否真实可用：GET {base_url}/models 探测（OpenAI 兼容），401/403/网络/超时均视为不可用。

    transport：测试注入用（httpx.AsyncClient(transport=...)）；生产传 None 走真实网络。

    返回 {"ok": True, "base_url": ..., "model": ...}；失败抛 RuntimeError（带明确原因）。
    """
    import httpx

    if api_key is not None:
        # 显式注入 key：校验「给定 key 是否可用」（设置页测试用），不读 .env
        if not api_key.strip():
            raise RuntimeError("LLM 链路校验失败：未配置 DEEPSEEK_API_KEY（backend/.env）（请在 backend/.env 配置，或在设置页填写）")
        client = DeepSeekClient.__new__(DeepSeekClient)
        client.api_key = api_key.strip()
        client.base_url = (settings.openai_base_url or "https://api.deepseek.com/v1").rstrip("/")
        client.model = settings.llm_model
    else:
        try:
            client = DeepSeekClient()
        except RuntimeError as exc:
            raise RuntimeError(f"LLM 链路校验失败：{exc}（请在 backend/.env 配置 DEEPSEEK_API_KEY，或在设置页填写）") from exc

    base_url = client.base_url.rstrip("/")
    url = f"{base_url}/models"
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as hc:
            r = await hc.get(url, headers={"Authorization": f"Bearer {client.api_key}"})
        if r.status_code < 400:
            return {"ok": True, "base_url": base_url, "model": client.model}
        if r.status_code in (401, 403):
            raise RuntimeError(
                f"LLM 链路校验失败：API Key 无效或无权限（HTTP {r.status_code}）。请检查 DEEPSEEK_API_KEY 是否正确"
            )
        raise RuntimeError(f"LLM 链路校验失败：API 返回 HTTP {r.status_code}：{r.text[:200]}")
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001  网络/超时/解析错误
        raise RuntimeError(f"LLM 链路校验失败：无法连接 {base_url}（{type(exc).__name__}: {exc}）。请检查网络与 OPENAI_BASE_URL") from exc
