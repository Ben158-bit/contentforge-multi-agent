"""API 安全中间件：Bearer Token 鉴权 + 内存滑动窗口限流。

两者均为纯 ASGI 中间件（不使用 BaseHTTPMiddleware），
避免对 SSE 等流式响应的缓冲问题。

鉴权规则：
- /api/health、/docs 等公开路径与 OPTIONS 预检请求放行；
- 未配置 API_TOKEN 时放行（本地开发模式）；
- 其余请求需携带 `Authorization: Bearer <token>` 头，
  或（仅限 SSE 这类无法自定义头的场景）`?token=<token>` 查询参数。

限流规则：
- 仅对写方法（POST/PUT/DELETE/PATCH）按客户端 IP 做滑动窗口计数；
- 单实例内存实现，多实例部署需替换为 Redis 等共享存储。
"""
from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from .logging_setup import request_id_var

# 无需鉴权的公开路径
PUBLIC_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}
# 参与限流的写方法
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


async def _send_json(send: Send, status_code: int, detail: str) -> None:
    """向客户端发送一个 JSON 错误响应。"""
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    """Bearer Token 鉴权中间件。"""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        # CORS 预检与公开路径直接放行
        if method == "OPTIONS" or path in PUBLIC_PATHS or not self.token:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        query_token = query.get("token", [""])[0]

        if auth == f"Bearer {self.token}" or query_token == self.token:
            await self.app(scope, receive, send)
            return

        await _send_json(send, 401, "无效或缺失的 API Token")


class RequestIDMiddleware:
    """为每个请求生成/透传 request_id，注入日志上下文与响应头。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode("latin-1") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", rid.encode("ascii")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """按 Content-Length 拒绝超限请求体（默认 1MB），返回 413。"""

    def __init__(self, app: ASGIApp, max_bytes: int = 1_000_000) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length:
            try:
                if int(length) > self.max_bytes:
                    await _send_json(send, 413, "请求体过大（上限 1MB）")
                    return
            except ValueError:
                pass
        await self.app(scope, receive, send)


class RateLimitMiddleware:
    """按客户端 IP 的写请求滑动窗口限流（内存实现）。"""

    def __init__(self, app: ASGIApp, requests_per_minute: int = 60) -> None:
        self.app = app
        self.limit = max(int(requests_per_minute), 1)
        self.window = 60.0
        self._hits: dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if method not in WRITE_METHODS or path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        key = client[0] if client else "unknown"
        now = time.monotonic()
        hits = self._hits[key]
        # 滑动窗口：丢弃窗口外的历史请求
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            await _send_json(send, 429, "请求过于频繁，请稍后再试")
            return
        hits.append(now)
        await self.app(scope, receive, send)
