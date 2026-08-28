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
    brand_id: Optional[int] = None,
) -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            """INSERT INTO tasks
               (project_id, topic, brand_name, target_audience, channel_id,
                extra_requirements, brand_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, topic, brand_name, target_audience, channel_id,
             extra_requirements, brand_id),
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


async def list_tasks_with_stages(project_id: Optional[int] = None) -> list[dict]:
    """任务列表 + 每任务的阶段状态（单次 LEFT JOIN 查询，避免 N+1）。"""
    async with _conn_ctx() as conn:
        sql = (
            "SELECT t.*, s.stage_key, s.status AS stage_status "
            "FROM tasks t LEFT JOIN stages s ON s.task_id = t.id "
        )
        params: tuple = ()
        if project_id is not None:
            sql += "WHERE t.project_id = ? "
            params = (project_id,)
        sql += "ORDER BY t.id DESC"
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()

    tasks: dict[int, dict] = {}
    order: list[int] = []
    for r in rows:
        tid = r["id"]
        if tid not in tasks:
            task = dict(r)
            task.pop("stage_key", None)
            task.pop("stage_status", None)
            task["stages"] = []
            tasks[tid] = task
            order.append(tid)
        if r["stage_key"] is not None:
            tasks[tid]["stages"].append(
                {"stage_key": r["stage_key"], "status": r["stage_status"]}
            )
    return [tasks[tid] for tid in order]


async def update_task(task_id: int, **fields: Any) -> None:
    """按字段名更新任务（自动刷新 updated_at）。"""
    if not fields:
        return
    allowed = {"project_id", "topic", "brand_name", "target_audience",
               "channel_id", "extra_requirements", "status", "total_cost",
               "total_latency", "brand_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    async with _conn_ctx() as conn:
        await conn.execute(
            f"UPDATE tasks SET {cols}, updated_at = datetime('now') WHERE id = ?",
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
                    updated_at = datetime('now')""",
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
            f"UPDATE artifacts SET {cols}, updated_at = datetime('now') WHERE id = ?",
            values,
        )
        await conn.commit()


# ===================== 品牌 Brands（记忆层）=====================

async def create_brand(
    name: str,
    tone: str = "",
    core_claims: str = "",
    audience: str = "",
    taboos: str = "",
    notes: str = "",
) -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            """INSERT INTO brands (name, tone, core_claims, audience, taboos, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, tone, core_claims, audience, taboos, notes),
        )
        await conn.commit()
        return cur.lastrowid


async def get_brand(brand_id: int) -> dict:
    async with _conn_ctx() as conn:
        cur = await conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,))
        return _row_to_dict(await cur.fetchone())


async def get_brand_by_name(name: str) -> dict:
    async with _conn_ctx() as conn:
        cur = await conn.execute("SELECT * FROM brands WHERE name = ?", (name,))
        return _row_to_dict(await cur.fetchone())


async def list_brands() -> list[dict]:
    """品牌列表（含偏好规则条数）。"""
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            """SELECT b.*, (SELECT COUNT(*) FROM brand_preferences p
                           WHERE p.brand_id = b.id) AS pref_count
               FROM brands b ORDER BY b.id"""
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_brand(brand_id: int, **fields: Any) -> None:
    allowed = {"name", "tone", "core_claims", "audience", "taboos", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [brand_id]
    async with _conn_ctx() as conn:
        await conn.execute(
            f"UPDATE brands SET {cols}, updated_at = datetime('now') WHERE id = ?",
            values,
        )
        await conn.commit()


async def delete_brand(brand_id: int) -> None:
    """删除品牌及其偏好规则（SQLite 外键默认关闭，需显式级联）。"""
    async with _conn_ctx() as conn:
        await conn.execute("DELETE FROM brand_preferences WHERE brand_id = ?", (brand_id,))
        await conn.execute("DELETE FROM brands WHERE id = ?", (brand_id,))
        await conn.commit()


# ===================== 品牌偏好 BrandPreferences（记忆层）=====================

async def add_brand_preference(
    brand_id: int, rule_text: str,
    source_task: Optional[int] = None,
    source: str = "manual", strength: float = 1.0,
) -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "INSERT INTO brand_preferences (brand_id, rule_text, source_task, source, strength) "
            "VALUES (?, ?, ?, ?, ?)",
            (brand_id, rule_text, source_task, source, strength),
        )
        await conn.commit()
        return cur.lastrowid


async def delete_feedback_rule(brand_id: int, rule_id: int) -> None:
    """删除效果学习规律（仅 source='feedback' 的规则可被删除）。"""
    async with _conn_ctx() as conn:
        await conn.execute(
            "DELETE FROM brand_preferences WHERE id = ? AND brand_id = ? AND source = 'feedback'",
            (rule_id, brand_id),
        )
        await conn.commit()


async def merge_feedback_rule(brand_id: int, text: str, strength: float) -> int:
    """同 brand + source='feedback' + 同 rule_text → 衰减加权累计 strength；否则新增。

    返回 1=新增，0=合并（防规则爆炸：相同规律不重复入库）。
    """
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "SELECT id, strength FROM brand_preferences "
            "WHERE brand_id = ? AND source = 'feedback' AND rule_text = ?",
            (brand_id, text),
        )
        row = await cur.fetchone()
        if row:
            new_strength = round(row["strength"] + strength * 0.5, 2)
            await conn.execute(
                "UPDATE brand_preferences SET strength = ? WHERE id = ?",
                (new_strength, row["id"]),
            )
            await conn.commit()
            return 0
        cur = await conn.execute(
            "INSERT INTO brand_preferences (brand_id, rule_text, source, strength) "
            "VALUES (?, ?, 'feedback', ?)",
            (brand_id, text, strength),
        )
        await conn.commit()
        return 1


async def list_brand_preferences(brand_id: int, limit: Optional[int] = None) -> list[dict]:
    async with _conn_ctx() as conn:
        if limit is None:
            cur = await conn.execute(
                "SELECT * FROM brand_preferences WHERE brand_id = ? ORDER BY id DESC",
                (brand_id,),
            )
        else:
            cur = await conn.execute(
                "SELECT * FROM brand_preferences WHERE brand_id = ? ORDER BY id DESC LIMIT ?",
                (brand_id, int(limit)),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ===================== 效果闭环 Feedback =====================

async def upsert_content_feedback(
    content_id: int, *, views: int = 0, conversions: int = 0,
    score: float = 0.0, is_simulated: int = 0, note: str = "",
    channel: str = "general",
) -> None:
    """回填/更新效果数据（content_id 唯一，幂等 upsert）。"""
    async with _conn_ctx() as conn:
        await conn.execute(
            """
            INSERT INTO content_feedback
                (content_id, channel, views, conversions, score, is_simulated, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_id) DO UPDATE SET
                views=excluded.views, conversions=excluded.conversions,
                score=excluded.score, is_simulated=excluded.is_simulated,
                note=excluded.note, updated_at=datetime('now')
            """,
            (content_id, channel, views, conversions, score, is_simulated, note),
        )
        await conn.commit()


async def get_content_feedback(content_id: int) -> dict:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "SELECT * FROM content_feedback WHERE content_id = ?", (content_id,))
        row = await cur.fetchone()
        return _row_to_dict(row)


async def list_content_feedback() -> list[dict]:
    """真实效果数据（排除模拟），供规律提炼基线。"""
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "SELECT * FROM content_feedback WHERE is_simulated = 0 ORDER BY id DESC")
        rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]
