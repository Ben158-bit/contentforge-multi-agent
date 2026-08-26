"""应用配置管理。

使用 pydantic-settings 从 .env / 环境变量加载配置，
并提供 LLM 客户端与数据目录的统一访问入口。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .llm import LLMClient

logger = logging.getLogger(__name__)

# backend/ 目录（.env 与 data/ 的相对基准）
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用全局配置。字段名与 .env 中的大写变量一一对应。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- DeepSeek ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_temperature: float = 0.7
    # 演示模式：无需 API Key，用内置 FakeLLM 跑通完整流水线
    fake_llm: bool = False

    # ---- 服务 ----
    data_dir: str = "./data"
    cors_origins: str = "http://localhost:5173"

    # ---- 编排 ----
    max_revision_rounds: int = 3
    llm_timeout_seconds: float = 120.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        """数据目录（SQLite 等），自动创建。"""
        p = (
            Path(self.data_dir)
            if Path(self.data_dir).is_absolute()
            else BACKEND_DIR / self.data_dir
        )
        p.mkdir(parents=True, exist_ok=True)
        return p

    def validate_runtime(self) -> None:
        """启动时校验关键配置，缺失直接报错。"""
        if not self.deepseek_api_key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY：请复制 backend/.env.example 为 backend/.env 并填入 API Key"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_llm_client() -> LLMClient:
    """构建 LLM 客户端（演示模式优先，其次校验 API Key）。"""
    s = get_settings()
    if s.fake_llm:
        from .fake_llm import FakeLLMClient

        return FakeLLMClient()
    if not s.deepseek_api_key:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY：请复制 backend/.env.example 为 backend/.env 并填入 API Key"
        )
    return LLMClient(
        api_key=s.deepseek_api_key,
        base_url=s.deepseek_base_url,
        model=s.deepseek_model,
        temperature=s.deepseek_temperature,
        timeout_seconds=s.llm_timeout_seconds,
    )
