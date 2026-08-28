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


# ===================== 规律提炼 =====================

from app.feedback import _strength_from_stats, derive_feedback_rules  # noqa: E402
from app.llm import LLMResult  # noqa: E402


class _FakeLLM:
    """模拟 LLM：固定返回合法 JSON（对齐 test_api.py 的 chat 接口）。"""

    def chat(self, messages, **kwargs):
        return LLMResult(
            content='{"rules": ['
                    '{"feature": "has_num", "text": "标题含数字点击率高"}, '
                    '{"feature": "has_emoji", "text": "标题含 emoji 效果差"}]}',
            model="fake", prompt_tokens=1, completion_tokens=1, latency_seconds=0.0,
        )


def test_strength_from_stats():
    assert _strength_from_stats(0.80, 0.30) > 0   # 高分组更多 → 正向规则
    assert _strength_from_stats(0.20, 0.70) < 0   # 低分组更多 → 负向（避坑）规则
    assert _strength_from_stats(0.5, 0.5) == 0    # 无差异 → 0（不产生规则）


def test_derive_feedback_rules_uses_stats():
    stats = {"has_num": (0.8, 0.3), "has_emoji": (0.2, 0.6)}
    rules = derive_feedback_rules(stats, llm=_FakeLLM())
    assert len(rules) >= 1
    assert all(r["source"] == "feedback" for r in rules)
    assert all(isinstance(r["strength"], float) for r in rules)
    # has_num 高分占比高 → 正向 strength；has_emoji 低分占比高 → 负向 strength
    by_feature = {r["feature"]: r["strength"] for r in rules}
    assert by_feature["has_num"] > 0
    assert by_feature["has_emoji"] < 0
