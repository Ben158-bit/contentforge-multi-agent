"""仓储层集成测试。

使用临时数据目录运行，验证：
- init_db 幂等性
- 项目/任务/阶段/工件四实体 CRUD 与 upsert
"""
from __future__ import annotations

import os
import tempfile

# 必须在导入 app 之前设置数据目录（临时目录，避免污染开发库）
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-test-")

import pytest

from app import db, repository as repo

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _init_db():
    await db.init_db()
    yield


async def test_init_db_idempotent():
    """重复初始化不报错，且表结构存在、迁移版本正确。"""
    await db.init_db()  # 第二次调用
    conn = await db.get_conn()
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = {r[0] for r in await cur.fetchall()}
        cur = await conn.execute("SELECT version FROM schema_migrations")
        versions = {r["version"] for r in await cur.fetchall()}
        cur = await conn.execute("PRAGMA table_info(tasks)")
        task_cols = {r["name"] for r in await cur.fetchall()}
    finally:
        await conn.close()
    assert {"projects", "tasks", "stages", "artifacts", "schema_migrations"} <= tables
    assert versions == {"v1", "v2"}
    # v2 迁移应新增品牌表与 tasks.brand_id 列
    assert "brands" in tables and "brand_preferences" in tables
    assert "brand_id" in task_cols


async def test_project_and_task_crud():
    pid = await repo.create_project("测试项目", "描述")
    assert pid > 0
    proj = await repo.get_project(pid)
    assert proj["name"] == "测试项目"

    tid = await repo.create_task(
        "智能保温杯",
        "xiaohongshu",
        project_id=pid,
        brand_name="暖芯",
        target_audience="25-35 都市白领",
        extra_requirements="突出保温 24h",
    )
    task = await repo.get_task(tid)
    assert task["topic"] == "智能保温杯"
    assert task["status"] == "pending"

    await repo.update_task(tid, status="running", total_cost=0.05)
    updated = await repo.get_task(tid)
    assert updated["status"] == "running"
    assert updated["total_cost"] == 0.05

    tasks = await repo.list_tasks(project_id=pid)
    assert len(tasks) == 1


async def test_stage_upsert_and_list():
    tid = await repo.create_task("主题A", "wechat")
    await repo.upsert_stage(tid, "research", status="completed", output="{v1}", cost=0.01)
    # 覆盖更新
    await repo.upsert_stage(tid, "research", status="completed", output="{v2}", cost=0.02)
    stage = await repo.get_stage(tid, "research")
    assert stage["output"] == "{v2}"
    assert stage["cost"] == 0.02

    await repo.upsert_stage(tid, "strategy", status="running")
    stages = await repo.list_stages(tid)
    assert len(stages) == 2
    keys = {s["stage_key"] for s in stages}
    assert keys == {"research", "strategy"}


async def test_artifact_crud():
    tid = await repo.create_task("主题B", "weibo")
    a1 = await repo.create_artifact(tid, "copywriting", 0, "变体一")
    a2 = await repo.create_artifact(tid, "copywriting", 1, "变体二")
    await repo.update_artifact(a1, status="approved")

    arts = await repo.list_artifacts(tid, "copywriting")
    assert len(arts) == 2
    by_index = {a["variant_index"]: a for a in arts}
    assert by_index[0]["status"] == "approved"
    assert by_index[1]["content"] == "变体二"

    await repo.update_artifact(a2, content="变体二改")
    arts = await repo.list_artifacts(tid, "copywriting")
    assert {a["content"] for a in arts} == {"变体一", "变体二改"}
