"""应用配置：读取 backend/.env（pydantic-settings）。"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "AI短剧逻辑校验"
    app_env: str = "dev"
    log_level: str = "INFO"

    # LLM（OpenAI 兼容接口，DeepSeek；A1 决策：模型=deepseek-v4-flash，使用现有 API-Key）
    llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-v4-flash"
    # 推理模型会消耗 reasoning tokens；max_tokens 按任务大小分级（避免一刀切导致过度推理/截断）
    llm_max_tokens: int = 20000            # 兜底默认
    llm_max_tokens_chapter: int = 6000     # 章节摘要（小 JSON）
    llm_max_tokens_storyline: int = 16000  # 故事线（中）
    llm_max_tokens_outline: int = 24000    # 分集大纲（20集节拍，大）
    llm_max_tokens_episode: int = 20000    # 分集剧本（剧本+事件+状态，中大）
    llm_max_tokens_cards: int = 24000      # 素材卡（10类，大）
    llm_max_tokens_patterns: int = 24000   # 库级模式（大）
    llm_max_tokens_bible: int = 32000      # 世界圣经（大且关键，宁大勿小）
    llm_max_tokens_spine: int = 64000     # 主线骨架+选角（梗概+规则+JSON，推理模型，宁大勿小）
    llm_max_tokens_quality: int = 6000     # 语义质检（小）
    llm_temperature: float = 0.7

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./dev.db"


settings = Settings()