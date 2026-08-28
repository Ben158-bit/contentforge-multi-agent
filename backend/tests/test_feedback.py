"""效果闭环测试：特征提取、规律提炼、回填幂等、模拟相关性、注入排序、API。"""
from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-fb-test-")

import pytest  # noqa: E402

from app import repository as repo  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
async def _init_db():
    await init_db()
    yield


async def test_feedback_upsert_idempotent():
    tid = await repo.create_task("保温杯", "xiaohongshu", brand_name="暖芯")
    await repo.upsert_content_feedback(tid, views=1000, conversions=30, score=3.5)
    await repo.upsert_content_feedback(tid, views=2000, conversions=60, score=4.0)
    row = await repo.get_content_feedback(tid)
    assert row["views"] == 2000 and row["conversions"] == 60 and row["score"] == 4.0
    rows = await repo.list_content_feedback()
    assert len(rows) == 1  # 幂等：同 content_id 不新增
