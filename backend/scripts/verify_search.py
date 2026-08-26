"""验证联网搜索 API 连通性（读取 backend/.env 配置的搜索 Key）。

用法（backend 目录下）:
    python -m scripts.verify_search

不会打印 API Key。账号无额度时给出明确提示（不影响退出码）。
"""
from __future__ import annotations

import httpx

from app.config import get_web_search_client


def main() -> None:
    client = get_web_search_client()
    if client is None:
        print("未配置搜索（WEB_SEARCH_PROVIDER / WEB_SEARCH_API_KEY 为空）")
        return
    print(f"搜索 provider: {client.provider}")
    try:
        results = client.search("智能保温杯 行业趋势 2025", max_results=3)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            body = exc.response.text
            print("搜索 API 返回 403：账号可能没有余额或免费额度。")
            print(f"服务端信息: {body[:120]}")
            print("请到 open.bochaai.com 领取免费额度或充值后重试。")
        else:
            print(f"搜索 API 错误: HTTP {exc.response.status_code} {exc.response.text[:120]}")
        return
    print(f"检索到 {len(results)} 条结果：")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r.title[:40]}")
        print(f"     URL: {r.url}")
        print(f"     摘要: {(r.snippet or '')[:80]}")
    print("搜索连通性验证通过 [PASS]")


if __name__ == "__main__":
    main()

