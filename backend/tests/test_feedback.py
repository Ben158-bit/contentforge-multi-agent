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


# ===================== 特征提取 =====================

from app.feedback import extract_features  # noqa: E402


def test_extract_features():
    content = {
        "title": "3 个保温杯选购技巧，为什么买贵不如买对？",
        "body": "第一点：看材质。\n第二点：看保温时长。\n第三点：看密封性。",
        "hashtags": ["#保温杯", "#好物"],
        "notes": "",
    }
    f = extract_features(content)
    assert f["has_num"] == 1          # 标题含数字
    assert f["has_question"] == 1     # 标题含问句
    assert f["title_len"] == len("3 个保温杯选购技巧，为什么买贵不如买对？")
    assert f["bullet_count"] == 3     # 正文要点数（按行/编号）
    assert f["has_emoji"] == 0
