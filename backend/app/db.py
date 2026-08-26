"""SQLite 数据层：连接管理、表结构（DDL）与数据库初始化。

数据模型（四张业务表 + 迁移版本表）：
- projects     项目（营销任务的分组）
- tasks        营销任务（主题、渠道、状态、成本统计）
- stages       流水线阶段（5 个 agent 阶段的执行状态与产出）
- artifacts    最终工件（各渠道文案变体，支持人工编辑）
- schema_migrations 迁移版本记录
"""
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from .config import get_settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id         INTEGER,
        topic              TEXT NOT NULL,
        brand_name         TEXT NOT NULL DEFAULT '',
        target_audience    TEXT NOT NULL DEFAULT '',
        channel_id         TEXT NOT NULL,
        extra_requirements TEXT NOT NULL DEFAULT '',
        status             TEXT NOT NULL DEFAULT 'pending',
        total_cost         REAL NOT NULL DEFAULT 0,
        total_latency      REAL NOT NULL DEFAULT 0,
        created_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stages (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id       INTEGER NOT NULL,
        stage_key     TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'pending',
        output        TEXT NOT NULL DEFAULT '',
        feedback      TEXT NOT NULL DEFAULT '',
        revision_round INTEGER NOT NULL DEFAULT 0,
        cost          REAL NOT NULL DEFAULT 0,
        latency       REAL NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        UNIQUE (task_id, stage_key),
        FOREIGN KEY (task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id       INTEGER NOT NULL,
        stage_key     TEXT NOT NULL,
        variant_index INTEGER NOT NULL DEFAULT 0,
        content       TEXT NOT NULL DEFAULT '',
        status        TEXT NOT NULL DEFAULT 'draft',
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """,
]


def default_db_path() -> Path:
    """默认数据库路径：<data_dir>/contentforge.db。"""
    return get_settings().data_path / "contentforge.db"


async def get_conn(db_path: Path | None = None) -> aiosqlite.Connection:
    """打开数据库连接（Row 工厂），调用方负责关闭。"""
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db(db_path: Path | None = None) -> None:
    """初始化数据库：建表 + 记录迁移版本（幂等）。"""
    conn = await get_conn(db_path)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(stmt)
        await conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        await conn.commit()
        logger.info("数据库初始化完成 (version=%s)", SCHEMA_VERSION)
    finally:
        await conn.close()
