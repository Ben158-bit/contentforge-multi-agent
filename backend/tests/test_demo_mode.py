"""演示模式（FakeLLM）集成测试：无 API Key 跑通完整流水线。

覆盖：
- FakeLLMClient 关键字路由与审校轮次（第一次不通过触发打回）
- 用演示模式构建的图：全流程执行 → 审校打回一次 → 通过 → 到达人工确认
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-demo-test-")

from app.agents.graph import build_graph, run_pipeline  # noqa: E402
from app.config import get_llm_client, get_settings  # noqa: E402
from app.fake_llm import FakeLLMClient  # noqa: E402


def test_get_llm_client_fake_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "fake_llm", True)
    client = get_llm_client()
    assert isinstance(client, FakeLLMClient)
    assert client.display_name == "fake-demo"


def test_fake_llm_keyword_routing():
    fake = FakeLLMClient()
    r = fake.chat([{"role": "user", "content": "你是营销内容生产流水线中的【市场调研分析师】"}])
    data = json.loads(r.content)
    assert "trends" in data and "keywords" in data

    r = fake.chat([{"role": "user", "content": "你是营销内容生产流水线中的【文案创作专家】"}])
    data = json.loads(r.content)
    assert "variants" in data and len(data["variants"]) >= 1


def test_fake_llm_review_depends_on_copy():
    """演示审校（无状态）：待审文案含 CTA 才通过，否则打回。"""
    fake = FakeLLMClient()
    r1 = fake.chat([{"role": "user", "content": (
        "你是营销内容生产流水线中的【内容审校专家】\n"
        "待审文案变体：\n[{\"title\": \"t\", \"body\": \"无行动号召\"}]"
    )}])
    assert json.loads(r1.content)["passed"] is False
    r2 = fake.chat([{"role": "user", "content": (
        "你是营销内容生产流水线中的【内容审校专家】\n"
        "待审文案变体：\n[{\"title\": \"t\", \"body\": \"限时立减，立即下单\"}]"
    )}])
    assert json.loads(r2.content)["passed"] is True


def test_fake_llm_stateless_concurrent():
    """无状态：交错调用审校时结果只取决于各自 prompt，互不污染。"""
    fake = FakeLLMClient()
    fail_prompt = "【内容审校专家】\n待审文案变体：\n[{\"body\": \"无CTA\"}]"
    pass_prompt = "【内容审校专家】\n待审文案变体：\n[{\"body\": \"立即下单\"}]"
    assert json.loads(fake.chat([{"role": "user", "content": fail_prompt}]).content)["passed"] is False
    assert json.loads(fake.chat([{"role": "user", "content": pass_prompt}]).content)["passed"] is True
    assert json.loads(fake.chat([{"role": "user", "content": fail_prompt}]).content)["passed"] is False
    assert json.loads(fake.chat([{"role": "user", "content": fail_prompt}]).content)["passed"] is False


def test_full_pipeline_with_demo_mode(tmp_path, monkeypatch):
    """演示模式全流程：触发一次审校打回 → 通过 → 到达人工确认。"""
    monkeypatch.setattr(get_settings(), "fake_llm", True)
    fake = get_llm_client()
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")

    result = run_pipeline(graph, {
        "task_id": 1,
        "input": {
            "topic": "智能保温杯", "brand_name": "暖芯",
            "target_audience": "25-35 都市白领", "channel_id": "xiaohongshu",
            "extra_requirements": "",
        },
    })

    assert "__interrupt__" in result
    assert result["revision_round"] == 1  # 审校打回一次
    assert result["review"]["passed"] is True
    assert len(result["copywriting"]) >= 1
    assert result["total_cost"] > 0
    assert result["stage_status"] == {
        "research": "completed", "competitor": "completed", "strategy": "completed",
        "copywriting": "completed", "review": "completed",
    }
