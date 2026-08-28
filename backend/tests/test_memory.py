"""记忆层测试：品牌仓储 CRUD、偏好级联删除、任务关联品牌。

学习逻辑与注入断言见 test_memory.py 后续部分（随实现补充）。
"""
from __future__ import annotations

import os
import tempfile

# 必须在导入 app 之前设置（临时数据目录）
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-mem-test-")

import pytest  # noqa: E402

from app import repository as repo  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
async def _init_db():
    await init_db()
    yield


async def test_brand_crud():
    bid = await repo.create_brand(
        "暖芯", tone="温暖亲切", core_claims="24 小时保温", audience="都市白领",
        taboos="不夸大宣传", notes="测试品牌",
    )
    brand = await repo.get_brand(bid)
    assert brand["name"] == "暖芯"
    assert brand["tone"] == "温暖亲切"

    await repo.update_brand(bid, tone="专业可信", notes="")
    brand = await repo.get_brand(bid)
    assert brand["tone"] == "专业可信"

    await repo.delete_brand(bid)
    assert await repo.get_brand(bid) == {}


async def test_brand_name_unique():
    await repo.create_brand("同名品牌")
    # 唯一约束：第二次插入同名应抛错
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        await repo.create_brand("同名品牌")


async def test_brand_preferences_and_cascade():
    bid = await repo.create_brand("暖芯")
    await repo.add_brand_preference(bid, "避免限时促销话术", source_task=1)
    await repo.add_brand_preference(bid, "标题用口语化表达")

    prefs = await repo.list_brand_preferences(bid)
    assert len(prefs) == 2

    limited = await repo.list_brand_preferences(bid, limit=1)
    assert len(limited) == 1

    # 级联删除：删品牌后偏好一并清除
    await repo.delete_brand(bid)
    assert await repo.list_brand_preferences(bid) == []


async def test_task_linked_to_brand():
    bid = await repo.create_brand("暖芯")
    tid = await repo.create_task("保温杯", "xiaohongshu", brand_name="暖芯", brand_id=bid)
    task = await repo.get_task(tid)
    assert task["brand_id"] == bid

    await repo.update_task(tid, brand_id=None)
    assert (await repo.get_task(tid))["brand_id"] is None


# ===================== 学习逻辑与注入 =====================

class FakeLLM:
    """返回预设 JSON 的假 LLM。"""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        from app.llm import LLMResult

        return LLMResult(content=self.content, model="fake", prompt_tokens=10,
                         completion_tokens=5, latency_seconds=0.01)


AI_VARIANTS = [{"title": "限时立减 30 元", "body": "促销", "hashtags": [], "notes": "x"}]
USER_VARIANTS = [{"title": "老用户专享价", "body": "会员", "hashtags": [], "notes": "y"}]


def test_variants_differ_ignores_notes():
    from app.memory import variants_differ

    # 仅 notes 不同 → 不算差异
    same = [dict(v, notes="改") for v in AI_VARIANTS]
    assert variants_differ(AI_VARIANTS, same) is False
    # 实质不同 → 算差异
    assert variants_differ(AI_VARIANTS, USER_VARIANTS) is True


def test_extract_rules_and_failure_degrades(monkeypatch):
    import json as jsonlib

    from app.memory import extract_rules

    fake = FakeLLM(jsonlib.dumps({"rules": ["避免限时促销话术"], }))
    rules = extract_rules(fake, AI_VARIANTS, USER_VARIANTS)
    assert rules == ["避免限时促销话术"]
    assert fake.calls == 1

    # LLM 输出坏 JSON → 降级为空列表
    bad = FakeLLM("not json")
    assert extract_rules(bad, AI_VARIANTS, USER_VARIANTS) == []

    # 空输入 → 不调用 LLM
    empty = FakeLLM("{}")
    assert extract_rules(empty, [], USER_VARIANTS) == []


async def test_learn_from_confirmation(monkeypatch):
    import json as jsonlib

    import app.memory as memory
    from app.fake_llm import FakeLLMClient  # noqa: F401

    class RuleLLM:
        def chat(self, messages, **kwargs):
            return __import__("app.llm", fromlist=["LLMResult"]).LLMResult(
                content=jsonlib.dumps({"rules": ["避免限时促销话术"]}),
                model="fake", prompt_tokens=10, completion_tokens=5, latency_seconds=0.01)

    monkeypatch.setattr(memory, "get_llm_client", lambda: RuleLLM())

    # 有差异 → 学习并入库
    bid = await repo.create_brand("暖芯-学习")
    rules = memory.learn_from_confirmation(bid, AI_VARIANTS, USER_VARIANTS, source_task=9)
    assert rules == ["避免限时促销话术"]
    prefs = await repo.list_brand_preferences(bid)
    assert len(prefs) == 1
    assert prefs[0]["source_task"] == 9

    # 无差异 → 不调用 LLM、不新增
    monkeypatch.setattr(memory, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError("不应调用")))
    same = [dict(v, notes="改") for v in AI_VARIANTS]
    rules = memory.learn_from_confirmation(bid, AI_VARIANTS, same)
    assert rules == []
    assert len(await repo.list_brand_preferences(bid)) == 1


async def test_brand_context_and_prompt_injection():
    from app.agents.prompts import build_copywriting_messages
    from app.agents.state import AgentState
    from app.memory import get_brand_context
    from app.templates import get_channel

    bid = await repo.create_brand(
        "暖芯-注入", tone="温暖亲切", core_claims="24 小时保温", audience="白领",
        taboos="不夸大",
    )
    await repo.add_brand_preference(bid, "避免限时促销话术")
    ctx = get_brand_context(bid)
    assert ctx["name"] == "暖芯-注入"
    assert ctx["preferences"] == ["避免限时促销话术"]

    state: AgentState = {
        "task_id": 1,
        "brand_id": bid,
        "input": {"topic": "保温杯", "brand_name": "暖芯", "target_audience": "白领",
                  "channel_id": "xiaohongshu", "extra_requirements": ""},
        "strategy": {"core_value_proposition": "v"},
    }
    messages = build_copywriting_messages(state, get_channel("xiaohongshu"),
                                          brand_context=ctx)
    content = messages[-1]["content"]
    assert "品牌档案（必须遵守）" in content
    assert "避免限时促销话术" in content
    # system 侧同步注入（权重更高）
    sys_content = messages[0]["content"]
    assert "品牌档案与已沉淀偏好" in sys_content
    assert "避免限时促销话术" in sys_content

    # 无品牌上下文 → 不注入品牌块（向后兼容）
    messages2 = build_copywriting_messages(state, get_channel("xiaohongshu"),
                                           brand_context=None)
    assert "品牌档案" not in messages2[-1]["content"]


def test_diff_variants_field_level():
    """diff_variants 按变体/字段定位修改点，忽略 notes，无差异返回空。"""
    from app.memory import diff_variants

    ai = [
        {"title": "限时立减 30 元", "body": "保温 12 小时", "hashtags": ["#冬"], "notes": "a"},
        {"title": "第二版", "body": "原文", "notes": "a"},
    ]
    user = [
        {"title": "限时立减 30 元", "body": "保温 16 小时", "hashtags": ["#冬"], "notes": "改"},
        {"title": "第二版", "body": "原文", "notes": "改"},
    ]
    diffs = diff_variants(ai, user)
    assert len(diffs) == 1, diffs
    assert "变体1.body" in diffs[0]
    assert "12" in diffs[0] and "16" in diffs[0]
    # notes 差异不计入
    assert "notes" not in diffs[0]
    # 未修改的 title 不计入（子串级 diff 只保留变更片段）
    assert "限时立减" not in diffs[0]

    # 无实质差异
    assert diff_variants(ai, [dict(v, notes="x") for v in ai]) == []

    # 变体数量不一致（新增/删除变体）
    missing = diff_variants(ai, ai[:1])
    assert len(missing) == 1 and "变体2" in missing[0]
    assert len(diff_variants(ai, ai + [{"title": "新增", "body": "新增"}])) == 1


def test_extract_rules_sends_only_diffs():
    """extract_rules 只把修改点喂给 LLM，不发送全量文案（防止臆造规则）。"""
    import json as jsonlib

    from app.memory import extract_rules

    captured = {}

    class CaptureLLM:
        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            from app.llm import LLMResult

            return LLMResult(content=jsonlib.dumps({"rules": ["保温时长统一为 16 小时"]}),
                             model="fake", prompt_tokens=10, completion_tokens=5,
                             latency_seconds=0.01)

    ai = [{"title": "冬日温暖", "body": "保温 12 小时，限时优惠", "notes": "x"}]
    user = [{"title": "冬日温暖", "body": "保温 16 小时，限时优惠", "notes": "y"}]
    rules = extract_rules(CaptureLLM(), ai, user)
    assert rules == ["保温时长统一为 16 小时"]

    user_content = captured["messages"][-1]["content"]
    assert "16" in user_content and "12" in user_content
    # 未修改的字段/文字（title、'限时优惠'）不应作为对比材料出现，避免 LLM 编造无关规则
    assert "冬日温暖" not in user_content
    assert "限时优惠" not in user_content
    # system 提示聚焦修改点、禁止推测
    assert "仅基于这些修改点" in captured["messages"][0]["content"]

    # 无差异 → 不调用 LLM
    assert extract_rules(CaptureLLM(), ai, [dict(v, notes="z") for v in ai]) == []
