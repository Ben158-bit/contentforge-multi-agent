"""可靠性测试：任务级超时看门狗 + 启动恢复遗留 running 任务。"""
from __future__ import annotations

import os
import sqlite3
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-rel-test-")

from app.agents.runner import (  # noqa: E402
    fail_stale_running_tasks,
    recover_running_tasks,
)
from app.config import get_settings  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_settings().data_path / "contentforge.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _insert_task(status: str = "running", updated_ago_minutes: int = 0) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO tasks (topic, channel_id, status, updated_at) "
        "VALUES (?, ?, ?, datetime('now', ?))",
        ("保温杯", "xiaohongshu", status, f"-{updated_ago_minutes} minutes"),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def _task_status(task_id: int) -> str | None:
    conn = _conn()
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row["status"] if row else None


def _set_stage(task_id: int, stage_key: str, status: str) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO stages (task_id, stage_key, status) VALUES (?, ?, ?) "
        "ON CONFLICT(task_id, stage_key) DO UPDATE SET status = excluded.status",
        (task_id, stage_key, status),
    )
    conn.commit()
    conn.close()


def test_fail_stale_running_tasks():
    fresh = _insert_task(status="running", updated_ago_minutes=0)
    stale = _insert_task(status="running", updated_ago_minutes=30)
    count = fail_stale_running_tasks(deadline_minutes=10)
    assert count == 1
    assert _task_status(fresh) == "running"
    assert _task_status(stale) == "failed"


def test_recover_running_tasks_review_done():
    """review 已完成 → 流水线已到人工确认点 → 恢复为 waiting_human。"""
    task_id = _insert_task(status="running")
    for key in ["research", "competitor", "strategy", "copywriting", "review"]:
        _set_stage(task_id, key, "completed")
    count = recover_running_tasks()
    assert count >= 1
    assert _task_status(task_id) == "waiting_human"


def test_recover_running_tasks_mid_pipeline():
    """review 未完成 → 执行中崩溃 → 置 failed（可 rerun）。"""
    task_id = _insert_task(status="running")
    _set_stage(task_id, "research", "completed")
    count = recover_running_tasks()
    assert count >= 1
    assert _task_status(task_id) == "failed"
