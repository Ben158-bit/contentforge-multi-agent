"""DeepSeek LLM 客户端封装。

基于 OpenAI 官方 SDK 的 OpenAI 兼容接口，统一封装：
- 同步调用（langgraph 0.6.x + Python 3.10 下 interrupt 仅支持同步执行路径，
  因此编排核心采用同步执行，由 API 层用线程池隔离阻塞）
- token 用量统计（多 Agent 场景成本可观测性的基础）
- 调用耗时统计
- 超时与重试
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    """一次 LLM 调用的结构化结果。"""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """线程安全的 DeepSeek 同步客户端（OpenAI 兼容接口）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        temperature: float = 0.7,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def chat(
        self,
        messages: Iterable[dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict[str, str]] = None,
        json_mode: bool = False,
    ) -> LLMResult:
        """调用对话补全接口（同步）。

        Args:
            messages: OpenAI 消息列表 [{"role": ..., "content": ...}, ...]
            temperature: 覆盖默认温度
            max_tokens: 最大生成长度
            response_format: OpenAI 兼容的 response_format（如 {"type": "json_object"}）
            json_mode: 快捷开关，等价于 response_format={"type": "json_object"}

        Returns:
            LLMResult：包含生成内容、token 用量与耗时。
        """
        if json_mode:
            response_format = {"type": "json_object"}

        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception:
            logger.exception("LLM 调用失败 (model=%s)", self.model)
            raise

        latency = time.monotonic() - started
        choice = resp.choices[0]
        usage = resp.usage
        result = LLMResult(
            content=choice.message.content or "",
            model=resp.model or self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
            latency_seconds=round(latency, 3),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )
        logger.debug(
            "LLM 调用完成 (model=%s, tokens=%s, latency=%.2fs)",
            result.model,
            result.total_tokens,
            result.latency_seconds,
        )
        return result

    @property
    def display_name(self) -> str:
        """用于界面展示的模型名。"""
        return self.model
