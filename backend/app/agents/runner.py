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
from .graph import thread_id_for
from .nodes import STAGE_KEYS

logger = logging.getLogger(__name__)


def _sync_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_settings().data_path / "contentforge.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _exec(sql: str, params: tuple | list = ()) -> None:
    conn = _sync_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _update_task(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    _exec(
        f"UPDATE tasks SET {sets}, updated_at = datetime('now', 'localtime') WHERE id = ?",
        list(fields.values()) + [task_id],
    )


def _upsert_stage(task_id: int, stage_key: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    set_clause = ", ".join(f"{k} = excluded.{k}" for k in fields)
    _exec(
        f"""INSERT INTO stages (task_id, stage_key, {cols}) VALUES (?, ?, {ph})
            ON CONFLICT(task_id, stage_key) DO UPDATE SET {set_clause},
                updated_at = datetime('now', 'localtime')""",
        [task_id, stage_key] + list(fields.values()),
    )


def _clear_artifacts(task_id: int) -> None:
    _exec("DELETE FROM artifacts WHERE task_id = ?", (task_id,))


def _insert_artifact(task_id: int, stage_key: str, idx: int, content: Any) -> None:
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False)
    _exec(
        "INSERT INTO artifacts (task_id, stage_key, variant_index, content) VALUES (?, ?, ?, ?)",
        (task_id, stage_key, idx, text),
    )


def _write_chunk(task_id: int, chunk: dict) -> None:
    """把 graph.stream 产出的节点更新写入 DB（每阶段实时进度）。"""
    for node_name, updates in chunk.items():
        if node_name == "__interrupt__":
            _update_task(task_id, status="waiting_human")
            continue
        if node_name in STAGE_KEYS:
            status = updates.get("stage_status", {}).get(node_name, "completed")
            output = json.dumps(updates.get(node_name, {}), ensure_ascii=False)
            cost = updates.get("total_cost")
            latency = updates.get("total_latency")
            _upsert_stage(
                task_id, node_name,
                status=status, output=output,
                cost=cost if cost is not None else 0,
                latency=latency if latency is not None else 0,
            )
            # 任务级累计统计实时回写
            if cost is not None:
                _update_task(
                    task_id,
                    total_cost=round(float(cost), 6),
                    total_latency=round(float(latency or 0), 2),
                )
            if status == "completed":
                logger.info("任务 %s 阶段[%s]完成", task_id, node_name)


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


def confirm_pipeline(graph, task_id: int, confirmation: dict) -> str:
    """提交人工确认：写最终工件 + 恢复执行到完成。

    Args:
        confirmation: 前端提交的确认内容（含编辑后的 variants）

    Returns:
        任务最终状态（'completed' 或 'failed'）。
    """
    variants = confirmation.get("variants", [])
    _clear_artifacts(task_id)
    for i, v in enumerate(variants):
        _insert_artifact(task_id, "copywriting", i, v)

    _update_task(task_id, status="running")
    cfg = {"configurable": {"thread_id": thread_id_for(task_id)}}
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
