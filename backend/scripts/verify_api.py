"""验证 DeepSeek API 连通性（使用 backend/.env 配置的真实 Key）。

用法（backend 目录下）:
    python -m scripts.verify_api

不会打印 API Key。
"""
from __future__ import annotations

from app.config import get_llm_client


def main() -> None:
    client = get_llm_client()
    print(f"模型: {client.display_name}（读取自 backend/.env）")
    result = client.chat(
        [{"role": "user", "content": "只回复两个字：正常"}],
        max_tokens=16,
        temperature=0.1,
    )
    print(f"响应: {result.content!r}")
    print(f"tokens: {result.total_tokens} | 耗时: {result.latency_seconds:.2f}s")
    print("API 连通性验证通过 [PASS]")


if __name__ == "__main__":
    main()
