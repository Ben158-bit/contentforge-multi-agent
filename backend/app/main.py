"""ContentForge 后端入口。

启动流程（lifespan）：
1. 初始化 SQLite（业务库 + checkpoint 库）
2. 构建 LangGraph 流水线（LLM 客户端注入）
3. 注册 CORS 与路由

运行：
    cd backend && uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.graph import build_graph
from .api.routes import router
from .config import get_settings, get_llm_client
from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        graph = build_graph(get_llm_client())
        app.state.graph = graph
        logger.info("流水线图构建成功 (model=%s)", get_settings().deepseek_model)
    except RuntimeError as exc:
        # 缺少 API Key 等：服务可启动但无法创建任务
        logger.warning("流水线图构建失败，任务创建将不可用: %s", exc)
        app.state.graph = None
    yield


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
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
