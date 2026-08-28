"""ContentForge 后端入口。

启动流程（lifespan）：
1. 初始化 SQLite（业务库 + checkpoint 库）
2. 构建 LangGraph 流水线（LLM 客户端注入）
3. 注册 CORS 与路由

运行：
    cd backend && uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.graph import build_graph
from .agents.runner import fail_stale_running_tasks, recover_running_tasks
from .api.routes import router
from .config import get_settings, get_llm_client, get_web_search_client
from .db import init_db
from .logging_setup import setup_logging
from .security import (
    AuthMiddleware,
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
)

setup_logging()
logger = logging.getLogger(__name__)


async def _watchdog_loop(deadline_minutes: int, interval_seconds: int) -> None:
    """任务级超时看门狗：定期把超过 deadline 的 running 任务置 failed。"""
    while True:
        await asyncio.sleep(max(int(interval_seconds), 1))
        try:
            count = fail_stale_running_tasks(deadline_minutes)
            if count:
                logger.info("看门狗：%d 个超时 running 任务已置为 failed", count)
        except Exception:
            logger.exception("看门狗检查失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    s = get_settings()
    # 运行时校验：缺失关键配置时记录明确错误（fail-fast 语义体现在日志与图不可用）
    try:
        s.validate_runtime()
    except RuntimeError as exc:
        logger.error("运行时配置校验失败: %s", exc)
    try:
        graph = build_graph(get_llm_client(), web_search=get_web_search_client())
        app.state.graph = graph
        logger.info("流水线图构建成功 (model=%s, web_search=%s)",
                    s.deepseek_model, s.web_search_provider or "disabled")
    except RuntimeError as exc:
        # 缺少 API Key 等：服务可启动但无法创建任务
        logger.warning("流水线图构建失败，任务创建将不可用: %s", exc)
        app.state.graph = None

    # 启动恢复：处理进程重启遗留的 running 任务
    recovered = recover_running_tasks()
    if recovered:
        logger.info("启动恢复：处理了 %d 个遗留 running 任务", recovered)

    watchdog = asyncio.create_task(
        _watchdog_loop(s.task_deadline_minutes, s.watchdog_interval_seconds)
    )
    yield
    watchdog.cancel()
    with suppress(asyncio.CancelledError):
        await watchdog
    # 关闭 SqliteSaver 连接（避免 _saver_cm 连接泄漏）
    graph = getattr(app.state, "graph", None)
    saver_cm = getattr(graph, "_saver_cm", None)
    if saver_cm is not None:
        try:
            saver_cm.__exit__(None, None, None)
        except Exception:
            logger.exception("关闭 checkpoint 连接失败")


app = FastAPI(
    title="ContentForge API",
    description="多 Agent 营销内容生产工作台",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    # 收紧：显式方法集与允许头白名单（不再全放开）
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)
# 注意 add_middleware 顺序：后添加者在外层、先执行。
# 目标执行顺序：RequestID -> BodySize -> RateLimit -> Auth -> CORS -> 路由。
app.add_middleware(AuthMiddleware, token=settings.api_token)
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

app.include_router(router)
