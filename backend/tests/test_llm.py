"""LLM 客户端单元测试。

验证：
- 调用参数正确传递（json_mode → response_format、temperature 覆盖）
- 结果解析（content/token 用量/耗时）
- 超时与重试配置
- 异常传播
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm import LLMClient


class FakeResponse:
    def __init__(self, content: str, model: str = "deepseek-chat",
                 prompt_tokens: int = 10, completion_tokens: int = 5):
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        self.model = model

    def model_dump(self) -> dict:
        return {"model": self.model}


class FakeCompletions:
    def __init__(self, responses: list):
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        resp = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeChat:
    def __init__(self, responses: list):
        self.completions = FakeCompletions(responses)


def _client_with(monkeypatch, responses: list, **kwargs) -> tuple[LLMClient, FakeCompletions]:
    fake = FakeChat(responses)
    monkeypatch.setattr("app.llm.OpenAI", lambda **kw: SimpleNamespace(chat=fake))
    client = LLMClient(api_key="sk-test", **kwargs)
    return client, fake.completions


def test_chat_basic_and_token_stats(monkeypatch):
    client, completions = _client_with(monkeypatch, [FakeResponse("你好，世界")])
    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.content == "你好，世界"
    assert result.model == "deepseek-chat"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.total_tokens == 15
    assert result.latency_seconds >= 0
    # 默认温度
    assert completions.calls[0]["temperature"] == 0.7


def test_chat_json_mode_and_temperature_override(monkeypatch):
    client, completions = _client_with(monkeypatch, [FakeResponse("{}")])
    client.chat(
        [{"role": "user", "content": "x"}],
        json_mode=True,
        temperature=0.2,
        max_tokens=500,
    )
    call = completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 500


def test_timeout_and_retry_config(monkeypatch):
    captured: dict = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(chat=FakeChat([FakeResponse("ok")]))

    monkeypatch.setattr("app.llm.OpenAI", fake_openai)
    LLMClient(api_key="sk-test", timeout_seconds=30.0, max_retries=3)
    assert captured["timeout"] == 30.0
    assert captured["max_retries"] == 3


def test_chat_error_propagates(monkeypatch):
    client, _ = _client_with(monkeypatch, [RuntimeError("API 挂了")])
    with pytest.raises(RuntimeError, match="API 挂了"):
        client.chat([{"role": "user", "content": "x"}])
