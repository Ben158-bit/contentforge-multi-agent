"""SQLite 数据层：连接管理、版本化迁移与数据库初始化。

数据模型（业务表 + 迁移版本表）：
- projects     项目（营销任务的分组）
- tasks        营销任务（主题、渠道、状态、成本统计、可选关联品牌）
- stages       流水线阶段（5 个 agent 阶段的执行状态与产出）
- artifacts    最终工件（各渠道文案变体，支持人工编辑）
- brands       品牌档案（记忆层·显式记忆）
- brand_preferences 品牌偏好规则（记忆层·纠错学习）
- schema_migrations 迁移版本记录

迁移机制：MIGRATIONS 为有序版本列表，init_db 按版本顺序执行
尚未应用的迁移并记录版本（修复旧版"只有建表、无升级路径"的问题）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from .config import get_settings

logger = logging.getLogger(__name__)

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# 有序迁移列表：(version, [DDL 语句])，按序执行、按版本号记录
MIGRATIONS: list[tuple[str, list[str]]] = [
    (
        "v1",
        [
            """
            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
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
                created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
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
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
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
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
            """,
        ],
    ),
    (
        # 记忆层：品牌档案 + 偏好规则 + tasks 关联品牌
        "v2",
        [
            """
            CREATE TABLE IF NOT EXISTS brands (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                tone        TEXT NOT NULL DEFAULT '',
                core_claims TEXT NOT NULL DEFAULT '',
                audience    TEXT NOT NULL DEFAULT '',
                taboos      TEXT NOT NULL DEFAULT '',
                notes       TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS brand_preferences (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_id    INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
                rule_text   TEXT NOT NULL,
                source_task INTEGER,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_pref_brand ON brand_preferences(brand_id)
            """,
            # 已存在的 tasks 表补充 brand_id 列（新库同样走此路径，保持新老库结构一致）
            "ALTER TABLE tasks ADD COLUMN brand_id INTEGER",
        ],
    ),
    (
        # 效果闭环：效果回填表 + 偏好规则增加来源/强度
        "v3",
        [
            """
            CREATE TABLE IF NOT EXISTS content_feedback (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id   INTEGER NOT NULL,
                channel      TEXT NOT NULL DEFAULT 'general',
                views        INTEGER NOT NULL DEFAULT 0,
                conversions  INTEGER NOT NULL DEFAULT 0,
                score        REAL NOT NULL DEFAULT 0,
                is_simulated INTEGER NOT NULL DEFAULT 0,
                note         TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (content_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_feedback_content ON content_feedback(content_id)",
            "ALTER TABLE brand_preferences ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
            "ALTER TABLE brand_preferences ADD COLUMN strength REAL NOT NULL DEFAULT 1.0",
        ],
    ),
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


async def _repair_legacy_schema_migrations(conn: aiosqlite.Connection) -> None:
    """兼容旧版 schema_migrations 表（version INTEGER）：重建为 TEXT 结构并迁移旧记录。

    版本化迁移机制引入前的旧库，schema_migrations 表为 INTEGER version 结构
    （记录 version=1）。新版以字符串版本号（'v1'/'v2'）写入，若直接 INSERT 会触发
    ``datatype mismatch``，导致旧库升级后应用启动即崩。此函数检测旧结构并原地重建，
    旧记录 version 1 → 'v1' 保留（applied_at 原值不变）。
    """
    cur = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    row = await cur.fetchone()
    if row is None or not row["sql"]:
        return
    normalized = " ".join(str(row["sql"]).split())
    if "version text primary key" in normalized.lower():
        return  # 已是新结构

    cur = await conn.execute("SELECT version, applied_at FROM schema_migrations")
    old_rows = await cur.fetchall()
    await conn.execute("DROP TABLE schema_migrations")
    await conn.execute(SCHEMA_MIGRATIONS_DDL)
    for r in old_rows:
        v = r["version"]
        new_version = f"v{v}" if isinstance(v, int) else str(v)
        await conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (new_version, r["applied_at"]),
        )
    logger.warning("检测到旧版 schema_migrations 表（INTEGER version），已重建为 TEXT 结构并迁移记录")


async def init_db(db_path: Path | None = None) -> None:
    """初始化数据库：按版本顺序执行未应用的迁移（幂等）。"""
    conn = await get_conn(db_path)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(SCHEMA_MIGRATIONS_DDL)
        await _repair_legacy_schema_migrations(conn)
        cur = await conn.execute("SELECT version FROM schema_migrations")
        applied = {r["version"] for r in await cur.fetchall()}

        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            for stmt in statements:
                try:
                    await conn.execute(stmt)
                except Exception as exc:  # 兼容性：重复加列等已就绪场景
                    lowered = str(exc).lower()
                    if "duplicate column" not in lowered and "already exists" not in lowered:
                        raise
            await conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (version,)
            )
            logger.info("数据库迁移 %s 已应用", version)
        await conn.commit()
        logger.info("数据库初始化完成 (versions=%s)", ",".join(applied | {v for v, _ in MIGRATIONS}))
    finally:
        await conn.close()
