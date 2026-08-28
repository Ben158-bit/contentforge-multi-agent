"""数据库迁移兼容性测试：旧版（版本化迁移机制引入前）数据库升级不崩溃。

旧库的 schema_migrations 表为 INTEGER version 结构（记录 version=1），
新代码以字符串版本号写入会触发 datatype mismatch。本测试构造旧库并验证：
- init_db 成功（不抛 datatype mismatch）
- 旧记录保留（1 → 'v1'），v2 迁移被应用
- 旧业务数据保留，新表/新列就绪
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app import db

pytestmark = pytest.mark.asyncio

# 旧版 schema_migrations 表（INTEGER version + localtime 默认值）
SCHEMA_OLD_MIGRATIONS = """
CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

# 旧版 tasks 表（无 brand_id 列、localtime 默认值）
SCHEMA_OLD_TASKS = """
CREATE TABLE tasks (
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
"""


def _build_old_db() -> Path:
    path = Path(tempfile.mkdtemp(prefix="contentforge-old-")) / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA_OLD_MIGRATIONS)
    conn.execute(SCHEMA_OLD_TASKS)
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.execute(
        "INSERT INTO tasks (topic, channel_id, status) VALUES ('旧库任务', 'wechat', 'pending')"
    )
    conn.commit()
    conn.close()
    return path


async def test_upgrade_from_legacy_schema():
    """旧库升级：不崩溃、版本记录迁移、业务数据保留、新结构就绪。"""
    path = _build_old_db()

    await db.init_db(path)  # 不应抛 datatype mismatch
    await db.init_db(path)  # 幂等：再次初始化也不报错

    conn = sqlite3.connect(path)
    try:
        versions = [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )]
        brands = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='brands'"
        ).fetchone()
        prefs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='brand_preferences'"
        ).fetchone()
        task_cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        mig_ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert versions == ["v1", "v2"], f"旧记录应迁移为 v1 且 v2 已应用，实际 {versions}"
    assert brands is not None and prefs is not None  # v2 新表已创建
    assert "brand_id" in task_cols  # v2 新列已添加
    assert task_count == 1  # 旧业务数据保留
    assert "version text primary key" in " ".join(mig_ddl.split()).lower()  # 表结构已重建为 TEXT


async def test_legacy_repair_keeps_applied_at():
    """重建时保留旧记录的 applied_at 原值。"""
    path = _build_old_db()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_migrations SET applied_at = '2020-01-01 00:00:00'")
    conn.commit()
    conn.close()

    await db.init_db(path)

    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT version, applied_at FROM schema_migrations WHERE version='v1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[1] == "2020-01-01 00:00:00"
