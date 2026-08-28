"""流水线执行器：同步运行 LangGraph 图，逐步回写任务/阶段/工件到 SQLite。

设计说明：
- LangGraph 0.6.x 在 Python 3.10 下 interrupt 仅支持同步执行路径，
  因此 runner 用同步 graph.stream 逐步推进，并同步回写数据库。
- 同步回写使用标准库 sqlite3 直连（数据库为 WAL 模式），与事件循环中
  aiosqlite 连接并发安全。
- 执行入口在 FastAPI 中通过 asyncio.to_thread 隔离，避免阻塞事件循环。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from langgraph.types import Command

from ..config import get_settings
from ..db_sql import (
    clear_artifacts as _clear_artifacts,
    conn as _sync_conn,
    get_stage_metrics as _get_stage_metrics,
    get_task_metrics as _get_task_metrics,
    insert_artifact as _insert_artifact,
    update_task as _update_task,
    upsert_stage as _upsert_stage,
)
from ..memory import learn_from_confirmation
from .graph import thread_id_for, update_pipeline_state
from .nodes import STAGE_KEYS

logger = logging.getLogger(__name__)

# 上游阶段：编辑后会影响下游文案生成，confirm 时需回填并重跑下游
UPSTREAM_STAGE_KEYS = ["research", "competitor", "strategy"]


def _write_chunk(task_id: int, chunk: dict) -> None:
    """把 graph.stream 产出的节点更新写入 DB（每阶段实时进度）。"""
    for node_name, updates in chunk.items():
        if node_name == "__interrupt__":
            _update_task(task_id, status="waiting_human")
            continue
        if node_name in STAGE_KEYS:
            status = updates.get("stage_status", {}).get(node_name, "completed")
            output = json.dumps(updates.get(node_name, {}), ensure_ascii=False)
            new_total = updates.get("total_cost")
            if new_total is not None:
                # 阶段成本 = 任务累计值的增量 + 该阶段已有累计（打回重跑时累加）
                prev_cost, prev_latency = _get_task_metrics(task_id)
                old_stage_cost, old_stage_latency = _get_stage_metrics(task_id, node_name)
                new_latency = float(updates.get("total_latency", prev_latency))
                stage_cost = round(old_stage_cost + (float(new_total) - prev_cost), 6)
                stage_latency = round(old_stage_latency + (new_latency - prev_latency), 2)
            else:
                stage_cost = 0.0
                stage_latency = 0.0
            _upsert_stage(
                task_id, node_name,
                status=status, output=output,
                cost=stage_cost,
                latency=stage_latency,
            )
            # 任务级累计统计实时回写
            if new_total is not None:
                _update_task(
                    task_id,
                    total_cost=round(float(new_total), 6),
                    total_latency=round(float(updates.get("total_latency", 0)), 2),
                )
            if status == "completed":
                logger.info("任务 %s 阶段[%s]完成", task_id, node_name)


def _load_edited_upstream_values(task_id: int) -> tuple[dict | None, str | None]:
    """读取被人工编辑的上游阶段（research/competitor/strategy）产出。

    Returns:
        (values, earliest)：values 为可写回 checkpoint 的编辑值（stage_key -> dict），
        earliest 为最早被编辑的阶段 key（按流水线顺序），无编辑时为 (None, None)。
    """
    conn = _sync_conn()
    try:
        rows = conn.execute(
            "SELECT stage_key, status, output FROM stages "
            "WHERE task_id = ? AND stage_key IN ('research', 'competitor', 'strategy')",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()

    by_key = {r["stage_key"]: r for r in rows}
    values: dict = {}
    earliest: str | None = None
    for key in UPSTREAM_STAGE_KEYS:
        row = by_key.get(key)
        if row is None or row["status"] != "edited":
            continue
        if earliest is None:
            earliest = key
        raw = row["output"] or ""
        try:
            values[key] = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            logger.warning("任务 %s 阶段[%s]编辑内容解析失败，跳过回填", task_id, key)
            continue
    return (values or None), earliest


def _mark_upstream_applied(task_id: int, keys: list[str]) -> None:
    """把已回填进流水线的编辑阶段标记为 completed（编辑已被下游采纳）。"""
    for key in keys:
        _upsert_stage(task_id, key, status="completed")


def run_task_pipeline(graph, task_id: int, initial_state: dict) -> str:
    """执行流水线直到人工确认点（waiting_human），逐步回写进度。

    Returns:
        任务最终状态（'waiting_human' 或 'failed'）。
    """
    _update_task(task_id, status="running")
    cfg = {"configurable": {"thread_id": thread_id_for(task_id)}}
    try:
        for chunk in graph.stream(initial_state, cfg):
            _write_chunk(task_id, chunk)
    except Exception:
        logger.exception("任务 %s 流水线执行失败", task_id)
        _update_task(task_id, status="failed")
        return "failed"
    _update_task(task_id, status="waiting_human")
    return "waiting_human"


def learn_and_confirm(graph, task_id: int, confirmation: dict) -> str:
    """纠错学习 + 确认恢复：用户勾选学习时，先对比 AI 原版与定稿提取偏好规则
    入库，再执行确认流程（无差异时 learn_from_confirmation 自动跳过，不耗 LLM）。"""
    brand_id = confirmation.get("brand_id")
    if brand_id:
        cfg = {"configurable": {"thread_id": thread_id_for(task_id)}}
        snap = graph.get_state(cfg)
        ai_variants = (snap.values or {}).get("copywriting", [])
        learn_from_confirmation(
            brand_id, ai_variants, confirmation.get("variants", []),
            source_task=task_id,
        )
    return confirm_pipeline(graph, task_id, confirmation)


def confirm_pipeline(graph, task_id: int, confirmation: dict) -> str:
    """提交人工确认：写最终工件 + 恢复执行到完成。

    编辑回填：若用户编辑过上游阶段（research/competitor/strategy），
    先把编辑值写回 checkpoint，并把执行位置重定位到最早编辑阶段之后，
    重新执行下游（copywriting→review→human_confirm），使编辑真正影响文案；
    重跑后任务回到 waiting_human，等待用户对新文案再次确认。

    Args:
        confirmation: 前端提交的确认内容（含编辑后的 variants）

    Returns:
        任务最终状态（'completed' / 'waiting_human' / 'failed'）。
    """
    variants = confirmation.get("variants", [])
    _clear_artifacts(task_id)
    for i, v in enumerate(variants):
        _insert_artifact(task_id, "copywriting", i, v)

    cfg = {"configurable": {"thread_id": thread_id_for(task_id)}}

    # 1. 上游编辑回填：编辑真正生效（从最早编辑阶段之后重跑下游）
    values, earliest = _load_edited_upstream_values(task_id)
    if earliest and values:
        update_pipeline_state(graph, task_id, values, as_node=earliest)
        _mark_upstream_applied(task_id, list(values.keys()))
        _update_task(task_id, status="running")
        try:
            for chunk in graph.stream(None, cfg):
                _write_chunk(task_id, chunk)
        except Exception:
            logger.exception("任务 %s 编辑回填后重跑下游失败", task_id)
            _update_task(task_id, status="failed")
            return "failed"
        # 重跑下游后再次到达人工确认点，等待用户对新文案确认
        _update_task(task_id, status="waiting_human")
        return "waiting_human"

    # 2. 无上游编辑：正常恢复执行到完成
    _update_task(task_id, status="running")
    try:
        for chunk in graph.stream(Command(resume=confirmation), cfg):
            _write_chunk(task_id, chunk)
    except Exception:
        logger.exception("任务 %s 确认恢复失败", task_id)
        _update_task(task_id, status="failed")
        return "failed"
    # 汇总任务级统计
    snap = graph.get_state(cfg)
    values = snap.values or {}
    _update_task(
        task_id,
        status="completed",
        total_cost=round(float(values.get("total_cost", 0)), 6),
        total_latency=round(float(values.get("total_latency", 0)), 2),
    )
    return "completed"


def rerun_pipeline(graph, task_id: int, initial_state: dict) -> str:
    """重跑整个流水线：重置 checkpoint、清空旧产出后从头执行。"""
    try:
        graph.checkpointer.delete_thread(thread_id_for(task_id))
    except Exception:
        logger.warning("任务 %s 清理 checkpoint 失败，继续重跑", task_id)
    _clear_artifacts(task_id)
    for key in STAGE_KEYS:
        _upsert_stage(task_id, key, status="pending", output="", feedback="",
                      revision_round=0, cost=0, latency=0)
    return run_task_pipeline(graph, task_id, initial_state)


# ===================== 可靠性：看门狗与启动恢复 =====================

def fail_stale_running_tasks(deadline_minutes: int) -> int:
    """把超过 deadline 仍未推进的 running 任务置 failed，返回处理数量。"""
    conn = _sync_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status = 'running' "
            "AND updated_at < datetime('now', ?)",
            (f"-{max(int(deadline_minutes), 1)} minutes",),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        logger.warning("任务 %s 超过任务级 deadline，置为 failed", row["id"])
        _update_task(row["id"], status="failed")
    return len(rows)


def recover_running_tasks() -> int:
    """启动恢复：处理进程重启遗留的 running 任务。

    规则（近似 checkpoint 语义）：
    - review 阶段已完成 → 流水线已到人工确认点（human_confirm 前后崩溃）
      → 恢复为 waiting_human（用户可再次确认）
    - 否则 → 执行中崩溃，置 failed（用户可 rerun 重试）
    """
    conn = _sync_conn()
    try:
        rows = conn.execute("SELECT id FROM tasks WHERE status = 'running'").fetchall()
        result = []
        for row in rows:
            task_id = row["id"]
            review = conn.execute(
                "SELECT status FROM stages WHERE task_id = ? AND stage_key = 'review'",
                (task_id,),
            ).fetchone()
            if review is not None and review["status"] == "completed":
                result.append((task_id, "waiting_human"))
            else:
                result.append((task_id, "failed"))
    finally:
        conn.close()
    for task_id, status in result:
        logger.info("启动恢复：任务 %s 遗留 running → %s", task_id, status)
        _update_task(task_id, status=status)
    return len(result)
