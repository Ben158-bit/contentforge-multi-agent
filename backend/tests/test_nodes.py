"""Agent 节点单元测试。

使用 FakeLLM 注入，验证：
- 各节点 JSON 解析与状态写入
- stage_status reducer 合并
- 成本/耗时累加
- 审校不通过 → feedback + revision_round 递增
"""
from __future__ import annotations

import json

import pytest

from app.agents.nodes import (
    make_competitor_node,
    make_copywriting_node,
    make_research_node,
    make_review_node,
    make_strategy_node,
)
from app.agents.state import AgentState, _merge_dict
from app.llm import LLMResult


class FakeLLM:
    """按序返回预设响应的假 LLM（同步接口）。"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.json_mode_used: list[bool] = []

    def chat(self, messages, **kwargs):
        content = self.responses[self.calls]
        self.calls += 1
        self.json_mode_used.append(kwargs.get("json_mode", False))
        return LLMResult(
            content=content, model="fake", prompt_tokens=100,
            completion_tokens=50, latency_seconds=0.1,
        )


def _base_state() -> AgentState:
    return {
        "task_id": 1,
        "input": {
            "topic": "智能保温杯",
            "brand_name": "暖芯",
            "target_audience": "25-35 都市白领",
            "channel_id": "xiaohongshu",
            "extra_requirements": "突出保温 24h",
        },
    }


def _apply(state: AgentState, update: dict) -> None:
    """模拟 LangGraph reducer 语义合并状态。"""
    if "stage_status" in update:
        update["stage_status"] = _merge_dict(
            state.get("stage_status"), update["stage_status"]
        )
    state.update(update)


def test_research_node():
    fake = FakeLLM([json.dumps(
        {"summary": "s", "trends": ["t1"], "audience_insights": ["a1"],
         "pain_points": ["p1"], "keywords": ["k1"]}, ensure_ascii=False)])
    state = _base_state()
    _apply(state, make_research_node(fake)(state))
    assert state["research"]["trends"] == ["t1"]
    assert state["stage_status"]["research"] == "completed"
    assert state["total_cost"] > 0 and state["total_latency"] == 0.1
    assert fake.json_mode_used == [True]


def test_full_chain_passes_review():
    """全链路：调研→竞品→策略→创作→审校通过，stage_status 全部 completed。"""
    fake = FakeLLM([
        json.dumps({"summary": "s", "trends": [], "audience_insights": [],
                    "pain_points": [], "keywords": []}, ensure_ascii=False),
        json.dumps({"competitors": [], "gaps": []}, ensure_ascii=False),
        json.dumps({"target_audience": "白领", "core_value_proposition": "v",
                    "key_messages": [], "tone_guidance": "g", "content_angles": [],
                    "channel_strategy": "s"}, ensure_ascii=False),
        json.dumps({"variants": [{"title": "标题", "body": "正文",
                                  "hashtags": [], "notes": "n"}]}, ensure_ascii=False),
        json.dumps({"passed": True, "feedback": "", "checklist": []}, ensure_ascii=False),
    ])
    state = _base_state()
    for fn in [make_research_node(fake), make_competitor_node(fake),
               make_strategy_node(fake), make_copywriting_node(fake),
               make_review_node(fake)]:
        _apply(state, fn(state))
    assert state["stage_status"] == {
        "research": "completed", "competitor": "completed", "strategy": "completed",
        "copywriting": "completed", "review": "completed",
    }
    assert state["review"]["passed"] is True
    assert state["copywriting"][0]["title"] == "标题"
    assert fake.calls == 5


def test_review_rejects_and_increments_round():
    """审校不通过：返回 feedback 且 revision_round 递增。"""
    fake = FakeLLM([
        json.dumps({"passed": False, "feedback": "缺少行动号召，字数超限",
                    "checklist": [{"item": "结构", "passed": False, "note": "x"}]},
                   ensure_ascii=False)])
    state = _base_state()
    state["copywriting"] = [{"title": "t", "body": "b", "hashtags": [], "notes": "n"}]
    _apply(state, make_review_node(fake)(state))
    assert state["review"]["passed"] is False
    assert "行动号召" in state["review_feedback"]
    assert state["revision_round"] == 1


def test_copywriting_requires_variants():
    """文案创作必须返回至少 1 个变体，否则报错。"""
    fake = FakeLLM([json.dumps({"variants": []}, ensure_ascii=False)])
    state = _base_state()
    with pytest.raises(ValueError, match="未返回任何变体"):
        make_copywriting_node(fake)(state)


def test_json_parse_fallback():
    """LLM 输出带多余文字时仍能提取 JSON。"""
    fake = FakeLLM(['以下是结果：\n```json\n{"summary": "s"}\n```'])
    state = _base_state()
    _apply(state, make_research_node(fake)(state))
    assert state["research"]["summary"] == "s"


def test_copywriting_injects_review_feedback():
    """创作节点在打回重写时应收到上一轮审校反馈。"""
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    state = _base_state()
    state["strategy"] = {"core_value_proposition": "v"}
    state["review_feedback"] = "缺少行动号召"
    messages = build_copywriting_messages(state, get_channel("xiaohongshu"))
    user_content = messages[-1]["content"]
    assert "缺少行动号召" in user_content
