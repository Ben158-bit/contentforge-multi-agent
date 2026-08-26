"""联网搜索客户端抽象：Bocha（博查）+ Tavily 双 provider。

调研 Agent 通过 WebSearchClient 检索真实数据，把结果（含来源 URL）
注入 prompt，使调研报告有真实依据、可溯源。

- provider=bocha：博查 AI 搜索（国内直连，有免费额度，专为 LLM 设计）
- provider=tavily：Tavily 搜索（国际主流 AI 搜索 API）

未配置 API Key 时 available=False，调研节点自动降级为纯 LLM 生成。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"


@dataclass
class SearchResult:
    """一条搜索结果（含来源信息，用于溯源）。"""

    title: str
    url: str
    snippet: str
    site_name: str = ""


class WebSearchClient:
    """线程安全的同步搜索客户端（与编排执行路径一致）。"""

    def __init__(
        self,
        provider: str = "bocha",
        api_key: str = "",
        timeout: float = 20.0,
        max_results: int = 5,
    ) -> None:
        self.provider = provider.lower()
        self.api_key = api_key
        self.timeout = timeout
        self.max_results = max_results

    @property
    def available(self) -> bool:
        """未配置 Key 时不可用（调用方据此降级）。"""
        return bool(self.api_key)

    def search(self, query: str, max_results: Optional[int] = None) -> list[SearchResult]:
        """执行搜索，返回按相关度排序的结果列表。"""
        if not self.available:
            raise RuntimeError("WebSearch 未配置 API Key")
        n = max_results or self.max_results
        if self.provider == "bocha":
            return self._search_bocha(query, n)
        if self.provider == "tavily":
            return self._search_tavily(query, n)
        raise ValueError(f"未知搜索 provider: {self.provider}")

    def _search_bocha(self, query: str, n: int) -> list[SearchResult]:
        resp = httpx.post(
            BOCHA_ENDPOINT,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "freshness": "noLimit", "summary": True, "count": n},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        pages = resp.json().get("data", {}).get("webPages", {}).get("value", [])
        results = []
        for p in pages[:n]:
            results.append(
                SearchResult(
                    title=p.get("name", ""),
                    url=p.get("url", ""),
                    snippet=p.get("summary") or p.get("snippet", ""),
                    site_name=p.get("siteName", ""),
                )
            )
        logger.debug("Bocha 搜索完成: %r -> %d 条", query, len(results))
        return results

    def _search_tavily(self, query: str, n: int) -> list[SearchResult]:
        resp = httpx.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": n,
                "search_depth": "basic",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("results", [])
        results = []
        for r in raw[:n]:
            results.append(
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    site_name="",
                )
            )
        logger.debug("Tavily 搜索完成: %r -> %d 条", query, len(results))
        return results
