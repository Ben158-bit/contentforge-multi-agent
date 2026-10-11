"""可靠性测试：任务级超时看门狗 + 启动恢复遗留任务 + 失败状态落库。

涵盖两次真实故障的回归：
1. WAL 旁路文件竞态让「置 running / 置 failed」这次写入也失败，
   任务于是永久停在 pending（看门狗只看 running）→ 见 test_mark_failed_*。
2. 任务失败只有日志、没有原因，前端只能显示一个「失败」→ 见 test_*_records_*。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-rel-test-")

import pytest  # noqa: E402

import app.agents.runner as runner  # noqa: E402
from app.agents.runner import (  # noqa: E402
    fail_stale_running_tasks,
    recover_running_tasks,
)
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _db():
    """本模块自带建库，不依赖别的测试模块先跑过（否则单跑必挂）。"""
    asyncio.run(init_db())


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


def _task_row(task_id: int) -> sqlite3.Row | None:
    conn = _conn()
    row = conn.execute(
        "SELECT status, error FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    conn.close()
    return row


def _task_status(task_id: int) -> str | None:
    row = _task_row(task_id)
    return row["status"] if row else None


def _task_error(task_id: int) -> str:
    row = _task_row(task_id)
    return row["error"] if row else ""


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


def test_fail_stale_running_tasks_records_the_reason():
    """看门狗置 failed 时必须把原因写下来，否则前端只能显示一个「失败」。"""
    stale = _insert_task(status="running", updated_ago_minutes=30)
    fail_stale_running_tasks(deadline_minutes=10)
    assert _task_status(stale) == "failed"
    assert "deadline" in _task_error(stale)


def test_fail_stale_ignores_pending_tasks():
    """pending 是进程内**合法**的排队状态（等 _task_slots），看门狗不得误杀。

    并发上限为 3，一批任务排队十几分钟并非异常。
    """
    queued = _insert_task(status="pending", updated_ago_minutes=30)
    fail_stale_running_tasks(deadline_minutes=10)
    assert _task_status(queued) == "pending"


def test_recover_marks_orphan_pending_as_failed():
    """启动恢复要处理 pending 孤儿：任务已建但流水线从未启动。

    这类任务 checkpoint 里没有状态，恢复成 waiting_human 是错的；
    置 failed 才能让用户看见原因并重跑。
    """
    orphan = _insert_task(status="pending")
    recover_running_tasks()
    assert _task_status(orphan) == "failed"
    assert "尚未开始执行" in _task_error(orphan)


# ---------------------------------------------------------------------------
# 失败状态本身也要写得进去
# ---------------------------------------------------------------------------
def test_mark_failed_never_raises_even_when_the_write_fails(monkeypatch):
    """真实故障：异常处理里那次「置 failed」同样写不进去，任务就永久停在 pending。

    所以置失败必须走 `_mark_failed`，且它自身绝不能再抛异常。
    """
    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(runner, "_update_task", boom)
    runner._mark_failed(999, sqlite3.OperationalError("attempt to write a readonly database"))


def test_run_task_pipeline_records_the_reason_and_never_leaves_pending():
    """流水线在首个写入就失败时：任务必须落到 failed 且带原因，而不是停在 pending。"""
    class BoomGraph:
        def stream(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("attempt to write a readonly database")

    task_id = _insert_task(status="pending")
    status = runner.run_task_pipeline(BoomGraph(), task_id, {"input": {}})

    assert status == "failed"
    row = _task_row(task_id)
    assert row["status"] == "failed"
    assert "readonly database" in row["error"]


def test_mark_failed_records_the_exception_type_and_message():
    task_id = _insert_task(status="running")
    runner._mark_failed(task_id, ValueError("模型返回的不是 JSON"))
    assert _task_status(task_id) == "failed"
    assert _task_error(task_id) == "ValueError: 模型返回的不是 JSON"


def test_running_write_clears_a_previous_error():
    """重跑时要把上一次的失败原因清掉，否则会显示过期信息。"""
    task_id = _insert_task(status="failed")
    runner._update_task(task_id, status="running", error="")
    assert _task_error(task_id) == ""
