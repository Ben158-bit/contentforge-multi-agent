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


def test_copywriting_injects_channel_guide():
    """创作节点注入渠道文案方法论（提炼自 agency-agents-zh）。"""
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    state = _base_state()
    state["strategy"] = {"core_value_proposition": "v"}
    messages = build_copywriting_messages(state, get_channel("xiaohongshu"))
    user_content = messages[-1]["content"]
    assert "渠道文案方法论" in user_content
    # 小红书标题公式应注入
    assert "18-20 字" in user_content
    assert "痛点共鸣" in user_content
    assert "热门大标签" in user_content

    # 产品页渠道注入标题公式
    messages_p = build_copywriting_messages(state, get_channel("product_page"))
    assert "[品牌] + [核心关键词]" in messages_p[-1]["content"]


def test_weibo_copywriting_injects_reasoning_skill_rules():
    """微博渠道应注入电商推理 skill 规则，且规则不泄漏到其他渠道。"""
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    state = _base_state()
    state["input"]["channel_id"] = "weibo"
    state["strategy"] = {"core_value_proposition": "v"}

    weibo_content = build_copywriting_messages(
        state, get_channel("weibo")
    )[-1]["content"]
    assert "现象 → 机制 → 动作 → 指标/结果" in weibo_content
    assert "多样本归纳优先" in weibo_content
    assert "不编造销量、价格、效果和微博来源" in weibo_content
    assert "280 字内" in weibo_content

    xhs_content = build_copywriting_messages(
        {**state, "input": {**state["input"], "channel_id": "xiaohongshu"}},
        get_channel("xiaohongshu"),
    )[-1]["content"]
    assert "现象 → 机制 → 动作 → 指标/结果" not in xhs_content


def test_copywriting_adds_job_and_fact_controls():
    """文案 Prompt 应明确任务类型和产品事实边界。"""
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    state = _base_state()
    state["input"]["channel_id"] = "weibo"
    state["input"]["extra_requirements"] = "新品官宣"
    content = build_copywriting_messages(state, get_channel("weibo"))[-1]["content"]

    assert "任务类型" in content
    assert "事实账本" in content
    assert "已提供事实" in content
    assert "不得把单个案例写成普遍结论" in content
    assert '"hook_type"' in content
    assert '"proof_used"' in content


def test_product_page_prompt_requires_fab_evidence():
    """商品详情页应要求 FAB 结构，并将缺失证据标为待补充。"""
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    state = _base_state()
    state["input"]["channel_id"] = "product_page"
    content = build_copywriting_messages(state, get_channel("product_page"))[-1]["content"]

    assert "事实账本" in content
    assert "特性→优势→用户利益→证据" in content
    assert "待补充" in content


class FakeWebSearch:
    """假搜索客户端。"""

    available = True

    def __init__(self, results: list):
        self.results = results
        self.calls = 0

    def search(self, query, max_results=None):
        self.calls += 1
        return self.results


def test_research_node_with_web_search():
    """调研节点联网：检索结果注入 prompt，输出附带 sources 来源。"""
    from app.tools.web_search import SearchResult

    fake_search = FakeWebSearch([
        SearchResult(title="趋势报告", url="https://x.com/trend",
                     snippet="行业增长 20%", site_name="报告网"),
        SearchResult(title="用户调研", url="https://x.com/user",
                     snippet="用户偏好健康材质", site_name="调研网"),
    ])
    fake_llm = FakeLLM([json.dumps(
        {"summary": "s", "trends": ["t"], "audience_insights": ["a"],
         "pain_points": ["p"], "keywords": ["k"],
         "sources": [{"title": "趋势报告", "url": "https://x.com/trend"}]},
        ensure_ascii=False)])
    state = _base_state()
    _apply(state, make_research_node(fake_llm, web_search=fake_search)(state))

    assert fake_search.calls >= 2  # 至少检索 topic + 行业趋势
    assert state["research"]["sources"][0]["url"] == "https://x.com/trend"
    assert state["stage_status"]["research"] == "completed"


def test_research_node_fallback_sources():
    """LLM 未输出 sources 时，节点兜底附上检索来源。"""
    from app.tools.web_search import SearchResult

    fake_search = FakeWebSearch([
        SearchResult(title="来源A", url="https://x.com/a", snippet="内容"),
    ])
    fake_llm = FakeLLM([json.dumps(
        {"summary": "s", "trends": [], "audience_insights": [],
         "pain_points": [], "keywords": []}, ensure_ascii=False)])
    state = _base_state()
    _apply(state, make_research_node(fake_llm, web_search=fake_search)(state))

    assert state["research"]["sources"][0]["url"] == "https://x.com/a"


def test_research_node_without_web_search():
    """未配置搜索时自动降级：不注入检索，正常完成调研。"""
    fake_llm = FakeLLM([json.dumps(
        {"summary": "s", "trends": [], "audience_insights": [],
         "pain_points": [], "keywords": []}, ensure_ascii=False)])
    state = _base_state()
    _apply(state, make_research_node(fake_llm, web_search=None)(state))

    assert state["research"]["summary"] == "s"
    assert state["stage_status"]["research"] == "completed"


def test_research_node_search_failure_degrades():
    """搜索抛异常时降级为纯 LLM，不阻塞流水线。"""

    class BrokenSearch:
        available = True

        def search(self, query, max_results=None):
            raise RuntimeError("搜索服务挂了")

    fake_llm = FakeLLM([json.dumps(
        {"summary": "s", "trends": [], "audience_insights": [],
         "pain_points": [], "keywords": []}, ensure_ascii=False)])
    state = _base_state()
    _apply(state, make_research_node(fake_llm, web_search=BrokenSearch())(state))

    assert state["research"]["summary"] == "s"
    assert state["stage_status"]["research"] == "completed"
