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


def test_fake_llm_routes_strategy_before_competitor_context():
    """策略提示词包含竞品分析上下文时，仍必须路由到策略响应。"""
    fake = FakeLLMClient()
    r = fake.chat([{"role": "system", "content": "你是营销内容生产流水线中的【营销策略总监】。"},
                   {"role": "user", "content": "竞品分析：已有竞品数据"}])
    data = json.loads(r.content)
    assert "core_value_proposition" in data
    assert "competitors" not in data


def test_fake_llm_demo_output_follows_headset_topic():
    """电竞耳机任务的演示输出不能继续使用保温杯内容。"""
    fake = FakeLLMClient()
    prompts = [
        {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
        {"role": "user", "content": "<user_input>\n- topic: 电竞耳机\n- brand_name: kk耳机\n</user_input>"},
    ]
    data = json.loads(fake.chat(prompts).content)
    variant = data["variants"][0]
    assert "耳机" in variant["title"] or "耳机" in variant["body"]
    assert "保温杯" not in json.dumps(data, ensure_ascii=False)


def test_fake_llm_demo_output_follows_arbitrary_topics():
    """演示模式应根据每次任务输入生成主题一致、阶段不同的结果。"""
    fake = FakeLLMClient()
    for topic in ("机械键盘", "家用咖啡机"):
        def call(role: str, context: str = ""):
            return json.loads(fake.chat([
                {"role": "system", "content": f"你是营销内容生产流水线中的【{role}】。"},
                {"role": "user", "content": f"<user_input>\n- topic: {topic}\n- brand_name: 示例品牌\n- target_audience: 年轻用户\n</user_input>\n{context}"},
            ]).content)

        competitor = call("竞品分析专家", "市场调研摘要：围绕该主题的需求")
        strategy = call("营销策略总监", "竞品分析：已有竞品数据")
        copy = call("文案创作专家")
        assert topic in json.dumps(competitor, ensure_ascii=False)
        assert topic in json.dumps(strategy, ensure_ascii=False)
        assert topic in json.dumps(copy, ensure_ascii=False)
        assert competitor != strategy


def test_fake_llm_product_page_is_a_product_introduction():
    fake = FakeLLMClient()
    data = json.loads(fake.chat([
        {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
        {"role": "user", "content": (
            "<user_input>\n- topic: 机械键盘\n- brand_name: VGN\n"
            "- target_audience: 爱打游戏的电脑群体\n- channel_id: product_page\n"
            "- extra_requirements: 突出静音和手感\n</user_input>"
        )},
    ]).content)
    variant = data["variants"][0]
    assert "产品介绍" in variant["body"]
    assert "核心卖点" in variant["body"]
    assert "机械键盘" in variant["body"]
    assert variant["hashtags"] == []
    assert len(variant["selling_points"]) >= 3
    assert "静音" in json.dumps(variant["selling_points"], ensure_ascii=False)
    assert {"feature", "advantage", "benefit", "proof"} <= set(variant["selling_points"][0])
    assert "specifications" in variant and variant["specifications"]
    assert variant["cta"]


def test_product_page_prompt_declares_channel_specific_schema():
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    messages = build_copywriting_messages({"input": {
        "topic": "机械键盘", "brand_name": "VGN", "target_audience": "游戏玩家",
        "channel_id": "product_page", "extra_requirements": "全键热插拔、三模连接",
    }, "strategy": {}}, get_channel("product_page"))
    prompt = messages[-1]["content"]
    assert "channel_id: product_page" in prompt
    assert "selling_points" in prompt
    assert "specifications" in prompt
    assert "cta" in prompt


def test_fake_llm_weibo_is_short_and_hashtags():
    fake = FakeLLMClient()
    data = json.loads(fake.chat([
        {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
        {"role": "user", "content": (
            "<user_input>\n- topic: 家用咖啡机\n- brand_name: 示例品牌\n"
            "- target_audience: 咖啡爱好者\n- channel_id: weibo\n</user_input>"
        )},
    ]).content)
    variant = data["variants"][0]
    assert len(variant["body"]) <= 280
    assert len(variant["hashtags"]) >= 2
    assert "家用咖啡机" in variant["body"]


def test_fake_llm_ignores_empty_requirement_sentinel_from_real_prompt():
    from app.agents.prompts import build_copywriting_messages
    from app.templates import get_channel

    messages = build_copywriting_messages({"input": {
        "topic": "机械键盘", "brand_name": "VGN", "target_audience": "游戏玩家",
        "channel_id": "product_page", "extra_requirements": "",
    }, "strategy": {}}, get_channel("product_page"))
    data = json.loads(FakeLLMClient().chat(messages).content)
    variant = data["variants"][0]
    assert all(item.get("feature") != "无" for item in variant["selling_points"])
    assert all(item.get("value") != "无" for item in variant["specifications"])


def test_fake_llm_weibo_enforces_limit_for_max_length_inputs():
    fake = FakeLLMClient()
    data = json.loads(fake.chat([
        {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
        {"role": "user", "content": (
            f"<user_input>\n- topic: {'机' * 200}\n- brand_name: {'牌' * 100}\n"
            f"- target_audience: {'玩' * 200}\n- channel_id: weibo\n</user_input>"
        )},
    ]).content)
    assert len(data["variants"][0]["body"]) <= 280


def test_fake_llm_weibo_uses_topic_specific_reasoning():
    """微博演示文案应给出主题相关的判断维度，而非固定泛化句式。"""
    fake = FakeLLMClient()
    data = json.loads(fake.chat([
        {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
        {"role": "user", "content": (
            "<user_input>\n- topic: 静音机械键盘\n- brand_name: 键屿\n"
            "- target_audience: 办公室久坐的程序员\n- channel_id: weibo\n</user_input>"
        )},
    ]).content)
    variant = data["variants"][0]
    assert "轴体" in variant["body"] or "噪音" in variant["body"]
    assert "hook_type" in variant
    assert "reasoning_chain" in variant
    assert "proof_used" in variant


def test_fake_llm_keeps_xiaohongshu_and_wechat_distinct():
    fake = FakeLLMClient()

    def render(channel: str) -> dict:
        return json.loads(fake.chat([
            {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
            {"role": "user", "content": (
                "<user_input>\n- topic: 机械键盘\n- brand_name: VGN\n"
                f"- target_audience: 游戏玩家\n- channel_id: {channel}\n</user_input>"
            )},
        ]).content)["variants"][0]

    xhs = render("xiaohongshu")
    wechat = render("wechat")
    assert xhs != wechat
    assert len(xhs["hashtags"]) >= 5
    assert "✨" in xhs["body"]
    assert wechat["hashtags"] == []
    assert "一、" in wechat["body"]


def test_fake_llm_product_page_revision_does_not_invent_service_support():
    fake = FakeLLMClient()
    data = json.loads(fake.chat([
        {"role": "system", "content": "你是营销内容生产流水线中的【文案创作专家】。"},
        {"role": "user", "content": (
            "<user_input>\n- topic: 机械键盘\n- brand_name: VGN\n"
            "- target_audience: 游戏玩家\n- channel_id: product_page\n"
            "- extra_requirements: 无\n</user_input>\n上一轮审校未通过"
        )},
    ]).content)
    variant = data["variants"][0]
    assert "服务支持" not in variant["cta"]
    assert "服务支持" not in variant["body"]


def test_fake_llm_review_is_topic_and_channel_aware():
    fake = FakeLLMClient()
    data = json.loads(fake.chat([
        {"role": "system", "content": "你是营销内容生产流水线中的【内容审校专家】。"},
        {"role": "user", "content": (
            "<user_input>\n- topic: 机械键盘\n- channel_id: product_page\n</user_input>\n"
            "待审文案变体：机械键盘产品介绍，核心卖点，查看详情。"
        )},
    ]).content)
    rendered = json.dumps(data, ensure_ascii=False)
    assert "保温" not in rendered
    assert "小红书" not in rendered
    assert "机械键盘" in rendered


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
