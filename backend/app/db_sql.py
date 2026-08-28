"""统一同步 SQLite 访问层（供同步执行上下文复用）。

背景：LangGraph 编排在同步线程运行（to_thread），其进度回写（runner）与
记忆层（memory）都直接写 contentforge.db；此前各自维护一套 sqlite3 连接与
SQL（重复代码）。本模块收敛这些同步操作，消除重复。

repository.py 的异步 CRUD（API 层用）保持不变——它面向事件循环，
改造为同步包装会引入无谓的 to_thread 复杂度，收益不成比例。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .config import get_settings


def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_settings().data_path / "contentforge.db"))
    conn.row_factory = sqlite3.Row
    return conn


def exec(sql: str, params: tuple | list = ()) -> None:
    """执行单条写语句并提交。"""
    c = conn()
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def update_task(task_id: int, **fields: Any) -> None:
    """更新任务字段（自动刷新 updated_at）。"""
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    exec(
        f"UPDATE tasks SET {sets}, updated_at = datetime('now') WHERE id = ?",
        list(fields.values()) + [task_id],
    )


def upsert_stage(task_id: int, stage_key: str, **fields: Any) -> None:
    """插入或更新阶段记录（(task_id, stage_key) 唯一）。"""
    if not fields:
        return
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    set_clause = ", ".join(f"{k} = excluded.{k}" for k in fields)
    exec(
        f"""INSERT INTO stages (task_id, stage_key, {cols}) VALUES (?, ?, {ph})
            ON CONFLICT(task_id, stage_key) DO UPDATE SET {set_clause},
                updated_at = datetime('now')""",
        [task_id, stage_key] + list(fields.values()),
    )


def clear_artifacts(task_id: int) -> None:
    exec("DELETE FROM artifacts WHERE task_id = ?", (task_id,))


def insert_artifact(task_id: int, stage_key: str, idx: int, content: Any) -> None:
    import json

    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    exec(
        "INSERT INTO artifacts (task_id, stage_key, variant_index, content) VALUES (?, ?, ?, ?)",
        (task_id, stage_key, idx, text),
    )


def get_task_metrics(task_id: int) -> tuple[float, float]:
    """读取任务当前累计 (total_cost, total_latency)。"""
    c = conn()
    try:
        row = c.execute(
            "SELECT total_cost, total_latency FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return (row["total_cost"], row["total_latency"]) if row else (0.0, 0.0)
    finally:
        c.close()


def get_stage_metrics(task_id: int, stage_key: str) -> tuple[float, float]:
    """读取阶段已累计 (cost, latency)（打回重跑时阶段可能已有多轮成本）。"""
    c = conn()
    try:
        row = c.execute(
            "SELECT cost, latency FROM stages WHERE task_id = ? AND stage_key = ?",
            (task_id, stage_key),
        ).fetchone()
        return (row["cost"], row["latency"]) if row else (0.0, 0.0)
    finally:
        c.close()
