"""联网搜索客户端单元测试（Bocha + Tavily，mock HTTP）。"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.tools.web_search import SearchResult, WebSearchClient


def _bocha_response() -> dict:
    return {
        "data": {
            "webPages": {
                "value": [
                    {"name": "标题1", "url": "https://a.com/1", "summary": "摘要1", "siteName": "站点A"},
                    {"name": "标题2", "url": "https://b.com/2", "snippet": "片段2"},
                ]
            }
        }
    }


def test_bocha_search(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        resp = Mock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: _bocha_response()
        return resp

    monkeypatch.setattr("app.tools.web_search.httpx.post", fake_post)
    client = WebSearchClient(provider="bocha", api_key="sk-bocha", timeout=15.0)
    assert client.available

    results = client.search("保温杯推荐")
    assert captured["url"] == "https://api.bochaai.com/v1/web-search"
    assert captured["headers"]["Authorization"] == "Bearer sk-bocha"
    assert captured["json"]["query"] == "保温杯推荐"
    assert captured["json"]["summary"] is True
    assert captured["timeout"] == 15.0

    assert len(results) == 2
    assert results[0].title == "标题1"
    assert results[0].url == "https://a.com/1"
    assert results[0].site_name == "站点A"
    assert results[1].snippet == "片段2"


def test_tavily_search(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, json=json, timeout=timeout)
        resp = Mock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {
            "results": [{"title": "T1", "url": "https://t.com/1", "content": "内容"}]
        }
        return resp

    monkeypatch.setattr("app.tools.web_search.httpx.post", fake_post)
    client = WebSearchClient(provider="tavily", api_key="tvly-x")
    results = client.search("query")

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["json"]["api_key"] == "tvly-x"
    assert len(results) == 1
    assert results[0].title == "T1"
    assert results[0].url == "https://t.com/1"


def test_unavailable_without_key():
    client = WebSearchClient(provider="bocha", api_key="")
    assert client.available is False
    with pytest.raises(RuntimeError, match="未配置 API Key"):
        client.search("q")


def test_unknown_provider():
    client = WebSearchClient(provider="unknown", api_key="k")
    with pytest.raises(ValueError, match="未知搜索 provider"):
        client.search("q")


def test_search_error_propagates(monkeypatch):
    def fake_post(*args, **kwargs):
        resp = Mock()
        resp.raise_for_status = Mock(side_effect=RuntimeError("搜索服务不可用"))
        return resp

    monkeypatch.setattr("app.tools.web_search.httpx.post", fake_post)
    client = WebSearchClient(provider="bocha", api_key="sk-x")
    with pytest.raises(RuntimeError, match="搜索服务不可用"):
        client.search("q")
