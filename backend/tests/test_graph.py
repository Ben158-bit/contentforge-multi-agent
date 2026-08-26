"""图编排集成测试。

使用 FakeLLM + 独立 checkpoint 数据库，验证：
- 流水线执行到 human_confirm 中断点
- 审校不通过 → 打回创作的循环
- Command(resume=...) 人工确认后完成
- checkpoint 持久化：重新编译图后状态可恢复
"""
from __future__ import annotations

import json

import pytest

from app.agents.graph import build_graph, get_pipeline_state, resume_pipeline, run_pipeline
from app.llm import LLMResult


class FakeLLM:
    """按序返回预设响应的假 LLM。"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    def chat(self, messages, **kwargs):
        content = self.responses[self.calls]
        self.calls += 1
        return LLMResult(
            content=content, model="fake", prompt_tokens=100,
            completion_tokens=50, latency_seconds=0.1,
        )


RESEARCH = json.dumps(
    {"summary": "s", "trends": ["t"], "audience_insights": ["a"],
     "pain_points": ["p"], "keywords": ["k"]}, ensure_ascii=False)
COMPETITOR = json.dumps(
    {"competitors": [{"name": "C", "positioning": "p", "selling_points": ["s"],
                      "content_strategy": "c", "strengths": [], "weaknesses": []}],
     "gaps": ["g"]}, ensure_ascii=False)
STRATEGY = json.dumps(
    {"target_audience": "白领", "core_value_proposition": "v", "key_messages": ["m"],
     "tone_guidance": "g", "content_angles": ["a"], "channel_strategy": "s"},
    ensure_ascii=False)
COPY = json.dumps(
    {"variants": [{"title": "标题", "body": "正文", "hashtags": [], "notes": "n"}]},
    ensure_ascii=False)
REVIEW_PASS = json.dumps({"passed": True, "feedback": "", "checklist": []},
                         ensure_ascii=False)
REVIEW_FAIL = json.dumps({"passed": False, "feedback": "缺少行动号召", "checklist": []},
                         ensure_ascii=False)


def _initial_state(task_id: int = 1) -> dict:
    return {
        "task_id": task_id,
        "input": {
            "topic": "智能保温杯",
            "brand_name": "暖芯",
            "target_audience": "25-35 都市白领",
            "channel_id": "xiaohongshu",
            "extra_requirements": "",
        },
    }


def _confirmation() -> dict:
    return {"variants": [{"title": "标题-已编辑", "body": "正文-已编辑",
                          "hashtags": [], "notes": "人工修改"}]}


def test_pipeline_interrupts_at_human_confirm(tmp_path):
    """全链路跑到 human_confirm 中断，resume 后完成。"""
    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS])
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")
    result = run_pipeline(graph, _initial_state())

    assert "__interrupt__" in result
    assert result["review"]["passed"] is True
    assert result["stage_status"] == {
        "research": "completed", "competitor": "completed", "strategy": "completed",
        "copywriting": "completed", "review": "completed",
    }
    assert fake.calls == 5

    final = resume_pipeline(graph, 1, _confirmation())
    assert final["confirmed"]["variants"][0]["title"] == "标题-已编辑"


def test_pipeline_review_loop(tmp_path):
    """审校第一次不通过 → 打回创作重写 → 第二次通过。"""
    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_FAIL,
                    COPY, REVIEW_PASS])
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")
    result = run_pipeline(graph, _initial_state(task_id=2))

    assert "__interrupt__" in result
    assert fake.calls == 7  # 5 节点 + 1 次重写 + 1 次复审
    assert result["revision_round"] == 1
    assert result["review"]["passed"] is True
    # 打回后文案被重新生成（fake 返回相同内容，但轮数验证了路径）
    assert result["review_feedback"] == "缺少行动号召"


def test_review_loop_stops_at_max_rounds(tmp_path, monkeypatch):
    """连续不通过时，达到最大轮数后仍进入人工确认（不无限循环）。"""
    from app.agents import graph as graph_module

    monkeypatch.setattr(graph_module.get_settings(), "max_revision_rounds", 2)
    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_FAIL,
                    COPY, REVIEW_FAIL])
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")
    result = run_pipeline(graph, _initial_state(task_id=3))

    assert "__interrupt__" in result
    # 2 次 review 均不通过，第 2 次达到上限后强制进入人工确认
    assert result["revision_round"] == 2
    assert fake.calls == 7  # 5 节点 + 1 次重写 + 1 次复审


def test_checkpoint_persists_across_rebuild(tmp_path):
    """checkpoint 持久化：重新编译图后，中断状态与最终状态均可恢复。"""
    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS])
    graph1 = build_graph(fake, db_path=tmp_path / "ckpt.db")
    result = run_pipeline(graph1, _initial_state(task_id=4))
    assert "__interrupt__" in result

    # 重新编译（模拟服务重启），状态仍可从 checkpoint 读取
    graph2 = build_graph(fake, db_path=tmp_path / "ckpt.db")
    snap = get_pipeline_state(graph2, 4)
    assert snap is not None
    assert snap["next"] == ["human_confirm"]
    assert snap["review"]["passed"] is True

    final = resume_pipeline(graph2, 4, _confirmation())
    assert final["confirmed"]["variants"][0]["title"] == "标题-已编辑"

    # 完成后再次查询：next 应为空
    snap2 = get_pipeline_state(graph2, 4)
    assert snap2["next"] == []
