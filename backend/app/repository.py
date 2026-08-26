"""仓储层：项目/任务/阶段/工件的异步 CRUD 与查询。

所有函数返回 dict（由 aiosqlite.Row 转换），便于 API 层直接序列化。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from .db import get_conn


@asynccontextmanager
async def _conn_ctx(db_path: Optional[Path] = None):
    """连接上下文管理器：自动关闭连接。"""
    conn = await get_conn(db_path)
    try:
        yield conn
    finally:
        await conn.close()


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


# ===================== 项目 Projects =====================

async def create_project(name: str, description: str = "") -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "INSERT INTO projects (name, description) VALUES (?, ?)", (name, description)
        )
        await conn.commit()
        return cur.lastrowid


async def get_project(project_id: int) -> dict:
    async with _conn_ctx() as conn:
        cur = await conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        return _row_to_dict(await cur.fetchone())


async def list_projects() -> list[dict]:
    async with _conn_ctx() as conn:
        cur = await conn.execute("SELECT * FROM projects ORDER BY id DESC")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ===================== 任务 Tasks =====================

async def create_task(
    topic: str,
    channel_id: str,
    *,
    project_id: Optional[int] = None,
    brand_name: str = "",
    target_audience: str = "",
    extra_requirements: str = "",
) -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            """INSERT INTO tasks
               (project_id, topic, brand_name, target_audience, channel_id, extra_requirements)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, topic, brand_name, target_audience, channel_id, extra_requirements),
        )
        await conn.commit()
        return cur.lastrowid


async def get_task(task_id: int) -> dict:
    async with _conn_ctx() as conn:
        cur = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_dict(await cur.fetchone())


async def list_tasks(project_id: Optional[int] = None) -> list[dict]:
    async with _conn_ctx() as conn:
        if project_id is None:
            cur = await conn.execute("SELECT * FROM tasks ORDER BY id DESC")
        else:
            cur = await conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY id DESC", (project_id,)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_task(task_id: int, **fields: Any) -> None:
    """按字段名更新任务（自动刷新 updated_at）。"""
    if not fields:
        return
    allowed = {"project_id", "topic", "brand_name", "target_audience",
               "channel_id", "extra_requirements", "status", "total_cost", "total_latency"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    async with _conn_ctx() as conn:
        await conn.execute(
            f"UPDATE tasks SET {cols}, updated_at = datetime('now', 'localtime') WHERE id = ?",
            values,
        )
        await conn.commit()


# ===================== 阶段 Stages =====================

async def upsert_stage(task_id: int, stage_key: str, **fields: Any) -> int:
    """插入或更新阶段记录（(task_id, stage_key) 唯一）。"""
    allowed = {"status", "output", "feedback", "revision_round", "cost", "latency"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return 0
    cols = ", ".join(updates.keys())
    placeholders = ", ".join("?" for _ in updates)
    set_clause = ", ".join(f"{k} = excluded.{k}" for k in updates)
    values = [task_id, stage_key] + list(updates.values())
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            f"""INSERT INTO stages (task_id, stage_key, {cols})
                VALUES (?, ?, {placeholders})
                ON CONFLICT(task_id, stage_key) DO UPDATE SET {set_clause},
                    updated_at = datetime('now', 'localtime')""",
            values,
        )
        await conn.commit()
        return cur.lastrowid


async def get_stage(task_id: int, stage_key: str) -> dict:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "SELECT * FROM stages WHERE task_id = ? AND stage_key = ?", (task_id, stage_key)
        )
        return _row_to_dict(await cur.fetchone())


async def list_stages(task_id: int) -> list[dict]:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "SELECT * FROM stages WHERE task_id = ? ORDER BY id", (task_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ===================== 工件 Artifacts =====================

async def create_artifact(
    task_id: int, stage_key: str, variant_index: int, content: str
) -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            """INSERT INTO artifacts (task_id, stage_key, variant_index, content)
               VALUES (?, ?, ?, ?)""",
            (task_id, stage_key, variant_index, content),
        )
        await conn.commit()
        return cur.lastrowid


async def list_artifacts(task_id: int, stage_key: Optional[str] = None) -> list[dict]:
    async with _conn_ctx() as conn:
        if stage_key is None:
            cur = await conn.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY id", (task_id,)
            )
        else:
            cur = await conn.execute(
                """SELECT * FROM artifacts WHERE task_id = ? AND stage_key = ?
                   ORDER BY id""",
                (task_id, stage_key),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_artifacts(task_id: int, stage_key: Optional[str] = None) -> None:
    """删除任务的工件（可选按阶段过滤）。"""
    async with _conn_ctx() as conn:
        if stage_key is None:
            await conn.execute("DELETE FROM artifacts WHERE task_id = ?", (task_id,))
        else:
            await conn.execute(
                "DELETE FROM artifacts WHERE task_id = ? AND stage_key = ?",
                (task_id, stage_key),
            )
        await conn.commit()


async def update_artifact(artifact_id: int, *, content: Optional[str] = None,
                          status: Optional[str] = None) -> None:
    updates: dict[str, Any] = {}
    if content is not None:
        updates["content"] = content
    if status is not None:
        updates["status"] = status
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [artifact_id]
    async with _conn_ctx() as conn:
        await conn.execute(
            f"UPDATE artifacts SET {cols}, updated_at = datetime('now', 'localtime') WHERE id = ?",
            values,
        )
        await conn.commit()
