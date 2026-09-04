"""REST API 路由：任务 CRUD、阶段编辑、确认推进、重跑、SSE 进度推送。

流水线执行在后台线程（asyncio.to_thread）进行，执行进度实时回写
SQLite（见 agents.runner），前端通过轮询/SSE 获取状态。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import repository as repo
from ..agents.nodes import STAGE_KEYS, STAGE_LABELS
from ..agents.runner import confirm_pipeline, learn_and_confirm, rerun_pipeline, run_task_pipeline
from ..agents.graph import thread_id_for
from ..config import get_settings
from ..feedback import run_feedback_learning, simulate_feedback_for_task
from ..schemas import (
    BrandCreate,
    BrandUpdate,
    ChannelOut,
    ConfirmRequest,
    FeedbackIn,
    FeedbackRuleOut,
    ProjectCreate,
    StageEdit,
    TaskCreate,
)
from ..templates import list_channels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# SSE 轮询间隔（秒）
SSE_POLL_INTERVAL = 1.5

# 允许的编辑阶段（strategy/copywriting 是人工干预价值最高的两个）
EDITABLE_STAGES = {"research", "competitor", "strategy", "copywriting", "review"}

# 后台流水线并发上限（信号量，超出排队）
_task_slots = asyncio.Semaphore(get_settings().max_concurrent_tasks)


def _get_graph(request: Request):
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=400, detail="流水线未初始化：请配置 DEEPSEEK_API_KEY 后重启服务")
    return graph


def _decode_stage_output(stage: dict) -> dict:
    """把 DB 中的 JSON 字符串阶段产出解析为对象。"""
    raw = stage.get("output") or ""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"text": raw}


def _decode_artifact_content(artifact: dict) -> Any:
    raw = artifact.get("content") or ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# ===================== 健康与渠道 =====================

@router.get("/health")
async def health(request: Request) -> dict:
    # DB 连通性探活（进程活着 ≠ 数据库可用）
    from ..db import get_conn

    db_ok = False
    try:
        conn = await get_conn()
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        db_ok = True
    except Exception:
        logger.warning("health 检查：数据库不可达")
    return {
        "status": "ok" if db_ok else "degraded",
        "graph_ready": getattr(request.app.state, "graph", None) is not None,
        "db_ok": db_ok,
    }


@router.get("/channels", response_model=list[ChannelOut])
async def channels() -> list[dict]:
    return [
        {"id": cid, "name": ch.get("name", cid), "desc": ch.get("desc", ""),
         "tone": ch.get("tone", ""), "max_chars": int(ch.get("max_chars", 0))}
        for cid, ch in list_channels().items()
    ]


# ===================== 项目 =====================

@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate) -> dict:
    pid = await repo.create_project(body.name, body.description)
    return await repo.get_project(pid)


@router.get("/projects")
async def list_projects() -> list[dict]:
    return await repo.list_projects()


# ===================== 品牌 Brands（记忆层）=====================

@router.post("/brands", status_code=201)
async def create_brand(body: BrandCreate) -> dict:
    existing = await repo.get_brand_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"品牌已存在: {body.name}")
    bid = await repo.create_brand(
        body.name, body.tone, body.core_claims, body.audience, body.taboos, body.notes
    )
    return await repo.get_brand(bid)


@router.get("/brands")
async def list_brands() -> list[dict]:
    return await repo.list_brands()


@router.get("/brands/{brand_id}")
async def get_brand(brand_id: int) -> dict:
    brand = await repo.get_brand(brand_id)
    if not brand:
        raise HTTPException(status_code=404, detail="品牌不存在")
    brand["preferences"] = await repo.list_brand_preferences(brand_id)
    return brand


@router.put("/brands/{brand_id}")
async def update_brand(brand_id: int, body: BrandUpdate) -> dict:
    if not await repo.get_brand(brand_id):
        raise HTTPException(status_code=404, detail="品牌不存在")
    await repo.update_brand(brand_id, **body.model_dump(exclude_none=True))
    return await repo.get_brand(brand_id)


@router.delete("/brands/{brand_id}")
async def delete_brand(brand_id: int) -> dict:
    if not await repo.get_brand(brand_id):
        raise HTTPException(status_code=404, detail="品牌不存在")
    await repo.delete_brand(brand_id)
    return {"ok": True}


# ===================== 任务 =====================

async def _task_detail(task_id: int) -> Optional[dict]:
    task = await repo.get_task(task_id)
    if task is None:
        return None
    stages = await repo.list_stages(task_id)
    for s in stages:
        s["output"] = _decode_stage_output(s)
    artifacts = await repo.list_artifacts(task_id)
    for a in artifacts:
        a["content"] = _decode_artifact_content(a)
    task["stages"] = stages
    task["artifacts"] = artifacts
    return task


async def _run_background(app, task_id: int, initial_state: dict) -> None:
    """后台执行流水线（不阻塞请求）。"""
    async with _task_slots:
        try:
            await asyncio.to_thread(run_task_pipeline, app.state.graph, task_id, initial_state)
        except Exception:
            logger.exception("任务 %s 后台执行异常", task_id)


async def _run_confirm_background(app, graph, task_id: int, confirmation: dict) -> None:
    """后台执行人工确认恢复（不阻塞请求）；勾选学习时先执行纠错学习。"""
    async with _task_slots:
        try:
            if confirmation.get("learn") and confirmation.get("brand_id"):
                await asyncio.to_thread(learn_and_confirm, graph, task_id, confirmation)
            else:
                await asyncio.to_thread(confirm_pipeline, graph, task_id, confirmation)
        except Exception:
            logger.exception("任务 %s 后台确认异常", task_id)


async def _run_rerun_background(app, graph, task_id: int, initial_state: dict) -> None:
    """后台执行任务重跑（不阻塞请求）。"""
    async with _task_slots:
        try:
            await asyncio.to_thread(rerun_pipeline, graph, task_id, initial_state)
        except Exception:
            logger.exception("任务 %s 后台重跑异常", task_id)


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, request: Request) -> dict:
    graph = _get_graph(request)
    if body.channel_id not in list_channels():
        raise HTTPException(status_code=400, detail=f"未知渠道: {body.channel_id}")

    task_id = await repo.create_task(
        body.topic, body.channel_id, project_id=body.project_id,
        brand_name=body.brand_name, target_audience=body.target_audience,
        extra_requirements=body.extra_requirements,
        brand_id=body.brand_id,
    )
    for key in STAGE_KEYS:
        await repo.upsert_stage(task_id, key, status="pending")

    initial_state = {
        "task_id": task_id,
        "brand_id": body.brand_id,
        "input": {
            "topic": body.topic,
            "brand_name": body.brand_name,
            "target_audience": body.target_audience,
            "channel_id": body.channel_id,
            "extra_requirements": body.extra_requirements,
        },
    }
    # 后台执行，立即返回任务
    asyncio.create_task(_run_background(request.app, task_id, initial_state))
    return await _task_detail(task_id)


@router.get("/tasks")
async def list_tasks() -> list[dict]:
    # 单次 JOIN 批量取阶段状态，避免逐任务查询（N+1）
    return await repo.list_tasks_with_stages()


@router.get("/tasks/{task_id}")
async def get_task(task_id: int) -> dict:
    detail = await _task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return detail


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, request: Request) -> dict:
    """删除历史任务及其本地流水线状态。"""
    deleted = await repo.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    graph = getattr(request.app.state, "graph", None)
    if graph is not None:
        try:
            graph.checkpointer.delete_thread(thread_id_for(task_id))
        except Exception:
            logger.warning("任务 %s 清理 checkpoint 失败", task_id)
    return {"ok": True}


@router.put("/tasks/{task_id}/stages/{stage_key}")
async def edit_stage(task_id: int, stage_key: str, body: StageEdit, request: Request) -> dict:
    if stage_key not in EDITABLE_STAGES:
        raise HTTPException(status_code=400, detail=f"不可编辑的阶段: {stage_key}")
    detail = await _task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if body.output is not None:
        await repo.upsert_stage(
            task_id, stage_key,
            output=json.dumps(body.output, ensure_ascii=False),
            status="edited",
        )
    if body.variants is not None:
        if stage_key != "copywriting":
            raise HTTPException(status_code=400, detail="variants 仅适用于 copywriting 阶段")
        await repo.delete_artifacts(task_id, "copywriting")
        for i, v in enumerate(body.variants):
            await repo.create_artifact(
                task_id, "copywriting", i, json.dumps(v, ensure_ascii=False)
            )
        await repo.upsert_stage(
            task_id, stage_key,
            output=json.dumps(body.variants, ensure_ascii=False),
            status="edited",
        )
    return await _task_detail(task_id)


@router.post("/tasks/{task_id}/confirm")
async def confirm_task(task_id: int, body: ConfirmRequest, request: Request) -> dict:
    graph = _get_graph(request)
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "waiting_human":
        raise HTTPException(status_code=400, detail=f"任务当前状态为 {task['status']}，无法确认")

    # 确认/编辑后的变体（未编辑则保留原样）
    if not body.variants:
        stage = await repo.get_stage(task_id, "copywriting")
        raw = stage.get("output") or ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            body.variants = parsed
        else:
            body.variants = parsed.get("variants", [])

    confirmation = {"variants": body.variants, "learn": body.learn, "brand_id": body.brand_id}
    # 后台执行（可能触发纠错学习 / 编辑回填重跑下游），立即返回当前状态
    asyncio.create_task(_run_confirm_background(request.app, graph, task_id, confirmation))
    return await _task_detail(task_id)


@router.post("/tasks/{task_id}/rerun")
async def rerun_task(task_id: int, request: Request) -> dict:
    graph = _get_graph(request)
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    initial_state = {
        "task_id": task_id,
        "brand_id": task["brand_id"],
        "input": {
            "topic": task["topic"],
            "brand_name": task["brand_name"],
            "target_audience": task["target_audience"],
            "channel_id": task["channel_id"],
            "extra_requirements": task["extra_requirements"],
        },
    }
    # 后台执行（整条流水线重跑，耗时较长），立即返回当前状态
    asyncio.create_task(_run_rerun_background(request.app, graph, task_id, initial_state))
    return await _task_detail(task_id)


# ===================== SSE 进度推送 =====================

def _snapshot(detail: dict) -> dict:
    """生成可比较的任务状态快照（SSE 变更检测用）。"""
    return {
        "task_id": detail["id"],
        "status": detail["status"],
        "stages": {s["stage_key"]: s["status"] for s in detail["stages"]},
        "total_cost": detail["total_cost"],
        "total_latency": detail["total_latency"],
        "updated_at": detail["updated_at"],
    }


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: int, request: Request) -> StreamingResponse:
    """SSE 事件流：任务状态变化实时推送（轮询 DB 快照做变更检测）。

    事件格式：data: <任务状态快照 JSON>，终态后发送 event: done 并关闭。
    """
    detail = await _task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_gen():
        last_snapshot: Optional[dict] = None
        while True:
            if await request.is_disconnected():
                break
            d = await _task_detail(task_id)
            snapshot = _snapshot(d)
            if snapshot != last_snapshot:
                last_snapshot = snapshot
                yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            if d["status"] in ("completed", "failed"):
                # 终态：再推一帧后关闭连接
                yield "event: done\n"
                yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(SSE_POLL_INTERVAL)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===================== 统计 =====================

@router.get("/stats")
async def stats() -> dict:
    """任务级成本/耗时聚合统计（可观测性）。"""
    tasks = await repo.list_tasks()
    completed = [t for t in tasks if t["status"] == "completed"]
    total_cost = round(sum(t["total_cost"] for t in tasks), 6)
    total_latency = round(sum(t["total_latency"] for t in tasks), 2)
    return {
        "task_count": len(tasks),
        "completed_count": len(completed),
        "total_cost": total_cost,
        "total_latency": total_latency,
        "avg_cost": round(total_cost / len(completed), 6) if completed else 0.0,
        "avg_latency": round(total_latency / len(completed), 2) if completed else 0.0,
    }


# ===================== 效果闭环 =====================

_FEEDBACK_FIELDS = ("content_id", "views", "conversions", "score",
                    "is_simulated", "note", "created_at")


def _feedback_payload(row: dict) -> dict:
    return {k: row[k] for k in _FEEDBACK_FIELDS}


@router.post("/tasks/{task_id}/feedback", response_model=dict)
async def submit_feedback(task_id: int, body: FeedbackIn, request: Request) -> dict:
    """手动回填效果数据（幂等 upsert）。"""
    task = await repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    await repo.upsert_content_feedback(
        task_id, views=body.views, conversions=body.conversions,
        score=body.score, note=body.note)
    # 闭环触发：品牌关联时异步提炼效果规律（失败静默降级）
    if task.get("brand_id"):
        await run_feedback_learning(task["brand_id"])
    row = await repo.get_content_feedback(task_id)
    return _feedback_payload(row)


@router.post("/tasks/{task_id}/feedback/simulate", response_model=dict)
async def simulate_feedback(task_id: int, request: Request) -> dict:
    """一键生成模拟效果数据（特征相关，标记 is_simulated=1 不进入规律基线）。"""
    task = await repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    stages = await repo.list_stages(task_id)
    task["stages"] = stages
    sim = await asyncio.to_thread(simulate_feedback_for_task, task)
    await repo.upsert_content_feedback(
        task_id, views=sim["views"], conversions=sim["conversions"],
        score=sim["score"], is_simulated=1,
        note="模拟效果（演示用，不进入规律基线）")
    row = await repo.get_content_feedback(task_id)
    return _feedback_payload(row)


@router.get("/tasks/{task_id}/feedback", response_model=dict)
async def get_feedback(task_id: int) -> dict:
    row = await repo.get_content_feedback(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="尚无效果数据")
    return _feedback_payload(row)


@router.get("/brands/{brand_id}/feedback-rules", response_model=list[FeedbackRuleOut])
async def list_feedback_rules(brand_id: int) -> list[dict]:
    prefs = await repo.list_brand_preferences(brand_id)
    return [
        {"id": p["id"], "rule_text": p["rule_text"], "strength": p["strength"],
         "created_at": p["created_at"]}
        for p in prefs if p.get("source") == "feedback"
    ]


@router.delete("/brands/{brand_id}/feedback-rules/{rule_id}")
async def delete_feedback_rule(brand_id: int, rule_id: int) -> dict:
    await repo.delete_feedback_rule(brand_id, rule_id)
    return {"ok": True}
