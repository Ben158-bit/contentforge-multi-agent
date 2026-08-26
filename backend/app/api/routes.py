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
from ..agents.runner import confirm_pipeline, rerun_pipeline, run_task_pipeline
from ..schemas import ChannelOut, ConfirmRequest, ProjectCreate, StageEdit, TaskCreate
from ..templates import list_channels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# SSE 轮询间隔（秒）
SSE_POLL_INTERVAL = 1.5

# 允许的编辑阶段（strategy/copywriting 是人工干预价值最高的两个）
EDITABLE_STAGES = {"research", "competitor", "strategy", "copywriting", "review"}


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
    return {"status": "ok", "graph_ready": getattr(request.app.state, "graph", None) is not None}


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
    try:
        await asyncio.to_thread(run_task_pipeline, app.state.graph, task_id, initial_state)
    except Exception:
        logger.exception("任务 %s 后台执行异常", task_id)


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, request: Request) -> dict:
    graph = _get_graph(request)
    if body.channel_id not in list_channels():
        raise HTTPException(status_code=400, detail=f"未知渠道: {body.channel_id}")

    task_id = await repo.create_task(
        body.topic, body.channel_id, project_id=body.project_id,
        brand_name=body.brand_name, target_audience=body.target_audience,
        extra_requirements=body.extra_requirements,
    )
    for key in STAGE_KEYS:
        await repo.upsert_stage(task_id, key, status="pending")

    initial_state = {
        "task_id": task_id,
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
    tasks = await repo.list_tasks()
    for t in tasks:
        stages = await repo.list_stages(t["id"])
        t["stages"] = [
            {"stage_key": s["stage_key"], "status": s["status"]} for s in stages
        ]
    return tasks


@router.get("/tasks/{task_id}")
async def get_task(task_id: int) -> dict:
    detail = await _task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return detail


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
            await repo.create_artifact(task_id, "copywriting", i, v)
        await repo.upsert_stage(
            task_id, stage_key,
            output=json.dumps({"variants": body.variants}, ensure_ascii=False),
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
            body.variants = parsed.get("variants", [])
        except json.JSONDecodeError:
            body.variants = []

    confirmation = {"variants": body.variants}
    await asyncio.to_thread(confirm_pipeline, graph, task_id, confirmation)
    return await _task_detail(task_id)


@router.post("/tasks/{task_id}/rerun")
async def rerun_task(task_id: int, request: Request) -> dict:
    graph = _get_graph(request)
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    initial_state = {
        "task_id": task_id,
        "input": {
            "topic": task["topic"],
            "brand_name": task["brand_name"],
            "target_audience": task["target_audience"],
            "channel_id": task["channel_id"],
            "extra_requirements": task["extra_requirements"],
        },
    }
    await asyncio.to_thread(rerun_pipeline, graph, task_id, initial_state)
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
