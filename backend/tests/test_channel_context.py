"""渠道上下文前移（切入点 B）测试。

改造前：``research`` / ``competitor`` / ``strategy`` 只拿到 ``channel_id`` 这个**字符串**，
没有渠道的任何规范 —— 于是「做小红书笔记」和「做电商详情页」的调研与策略是
**同一套逻辑**，渠道差异全部压在 ``copywriting`` 最后一棒。

改造后：渠道上下文（desc / tone / structure / max_chars / seo_keywords）
注入 ``research`` 与 ``strategy``；``strategy`` 产出该渠道原生的
``content_structure``（内容骨架），``copywriting`` 必须按它展开正文，
``review`` 要核对它有没有被遵守。

本文件守四件事：

1. **渠道上下文真的到达上游**（不是只改了 docstring）；
2. **多重真源不漂移**：``_channel_brief`` 与演示骨架都从 ``channels.yaml`` 派生，
   改配置就跟着变，不存在第二份渠道描述；
3. **骨架真被消费**：``copywriting`` 提示词里出现策略给的骨架；策略没给时
   退回渠道标准结构（同样来自 ``channels.yaml``），不是静默忽略；
4. **不推断未知渠道**：不认识的渠道 id 不编造渠道上下文。
"""
from __future__ import annotations

import pytest

from app import templates
from app.agents.prompts import (
    _channel_brief,
    _ordered_lines,
    build_copywriting_messages,
    build_research_messages,
    build_strategy_messages,
)
from app.templates import get_channel


def _state(channel_id: str, **extra) -> dict:
    base = {
        "input": {
            "topic": "智能保温杯新品上市",
            "brand_name": "屿暖",
            "target_audience": "25-35岁通勤上班族",
            "channel_id": channel_id,
            "extra_requirements": "",
        }
    }
    base.update(extra)
    return base


def _user(messages: list[dict[str, str]]) -> str:
    return next(m["content"] for m in messages if m["role"] == "user")


# ==========================================================================
# 1. _channel_brief 从配置派生
# ==========================================================================
def test_channel_brief_renders_the_configured_fields():
    """渠道块里的每一项都必须能在 ``channels.yaml`` 里找到原文。"""
    channel = get_channel("xiaohongshu")
    brief = _channel_brief("xiaohongshu")

    assert channel["name"] in brief
    assert channel["structure"] in brief
    assert channel["tone"] in brief
    assert channel["seo_keywords"] in brief
    assert str(channel["max_chars"]) in brief


def test_channel_brief_follows_the_config(monkeypatch):
    """改配置 → 渠道块跟着变（证明是派生，不是另写一份）。"""
    original = templates.load_channel_templates()
    patched = {**original,
               "xiaohongshu": {**original["xiaohongshu"],
                               "structure": "自定义结构A → 自定义结构B"}}
    monkeypatch.setattr(templates, "load_channel_templates", lambda: patched)

    brief = _channel_brief("xiaohongshu")
    assert "自定义结构A" in brief
    assert original["xiaohongshu"]["structure"] not in brief


def test_unknown_channel_gets_no_brief():
    """未知渠道**不推断**渠道上下文 —— 编一个出来比空着更危险。"""
    assert _channel_brief("") == ""
    assert _channel_brief("douyin") == ""


# ==========================================================================
# 2. 渠道上下文真的到达 research / strategy
# ==========================================================================
@pytest.mark.parametrize("channel_id", ["xiaohongshu", "wechat", "weibo", "product_page"])
def test_research_prompt_carries_channel_context(channel_id):
    channel = get_channel(channel_id)
    user = _user(build_research_messages(_state(channel_id)))

    assert "目标渠道" in user
    assert channel["name"] in user
    assert channel["structure"] in user
    # 关键词口径必须绑定渠道，而不是「5-8 个搜索关键词」这种通用说法
    assert "该渠道用户真实会搜索" in user


@pytest.mark.parametrize("channel_id", ["xiaohongshu", "wechat", "weibo", "product_page"])
def test_strategy_prompt_carries_channel_context(channel_id):
    channel = get_channel(channel_id)
    user = _user(build_strategy_messages(_state(channel_id)))

    assert "目标渠道" in user
    assert channel["name"] in user
    assert channel["structure"] in user
    # 必须明确要求产出内容骨架，否则下游无从消费
    assert "content_structure" in user


def test_upstream_prompts_differ_between_channels():
    """同一任务、不同渠道 → 上游提示词必须不同。

    这条是「渠道差异不再只体现在最后一棒」的最小可验证断言。
    """
    xhs = _user(build_strategy_messages(_state("xiaohongshu")))
    wechat = _user(build_strategy_messages(_state("wechat")))
    assert xhs != wechat

    xhs_r = _user(build_research_messages(_state("xiaohongshu")))
    wechat_r = _user(build_research_messages(_state("wechat")))
    assert xhs_r != wechat_r


def test_competitor_stage_is_deliberately_channel_agnostic():
    """竞品分析**刻意**不发渠道上下文：竞品是跨渠道竞争的。

    把它写下来是为了防止后人「顺手统一」——那会让竞品分析被单一渠道视角带偏。
    """
    from app.agents.prompts import build_competitor_messages
    user = _user(build_competitor_messages(_state("xiaohongshu")))
    assert "目标渠道" not in user


# ==========================================================================
# 3. 骨架真被 copywriting 消费（含兜底）
# ==========================================================================
def test_copywriting_consumes_the_strategy_structure():
    channel = get_channel("xiaohongshu")
    strategy = {"content_angles": ["a"], "content_structure": ["钩子：通勤水凉", "体验细节", "CTA"]}
    user = _user(build_copywriting_messages(_state("xiaohongshu", strategy=strategy), channel))

    assert "正文结构" in user
    assert "策略阶段为本任务给出的内容骨架" in user
    for step in strategy["content_structure"]:
        assert step in user


def test_copywriting_falls_back_to_the_channel_structure():
    """策略没给骨架（历史数据/桩数据）时退回渠道标准结构，而不是静默忽略。"""
    channel = get_channel("xiaohongshu")
    user = _user(build_copywriting_messages(_state("xiaohongshu", strategy={}), channel))

    assert "正文结构" in user
    assert "该渠道的标准内容结构" in user
    assert "爆款开头抓眼球" in user  # 来自 channels.yaml 的 structure


def test_copywriting_structure_section_differs_by_channel():
    """兜底骨架也必须随渠道变 —— 否则又是「一套通用结构」。"""
    xhs = _user(build_copywriting_messages(
        _state("xiaohongshu", strategy={}), get_channel("xiaohongshu")))
    wechat = _user(build_copywriting_messages(
        _state("wechat", strategy={}), get_channel("wechat")))
    assert xhs != wechat


def test_review_asks_to_check_the_structure():
    from app.agents.prompts import build_review_messages
    channel = get_channel("xiaohongshu")
    user = _user(build_review_messages(_state("xiaohongshu"), channel, [{"title": "t", "body": "b"}]))
    assert "content_structure" in user
    assert "checklist" in user


# ==========================================================================
# 4. _ordered_lines 的形态容忍
# ==========================================================================
def test_ordered_lines_accepts_lists_and_arrow_strings():
    assert _ordered_lines(["a", "b"]) == ["a", "b"]
    assert _ordered_lines("A → B → C") == ["A", "B", "C"]
    assert _ordered_lines("A；B") == ["A", "B"]
    assert _ordered_lines(["  ", ""]) == []
    assert _ordered_lines(None) == []
    assert _ordered_lines("") == []


def test_ordered_lines_does_not_split_list_items():
    """数组里的一条本身可能含箭头（「痛点共鸣 → 引出产品」是**一步**，不是两步）。"""
    steps = ["痛点共鸣 → 引出产品", "体验细节"]
    assert _ordered_lines(steps) == steps


# ==========================================================================
# 5. 演示模式：渠道差异 + 真源不漂移
# ==========================================================================
def test_demo_strategy_structure_differs_by_channel():
    from app.fake_llm import _dynamic_strategy

    xhs = _dynamic_strategy("保温杯", "屿暖", "上班族", "xiaohongshu", "")
    wechat = _dynamic_strategy("保温杯", "屿暖", "上班族", "wechat", "")

    assert xhs["content_structure"] and wechat["content_structure"]
    assert xhs["content_structure"] != wechat["content_structure"]


def test_demo_structure_tracks_the_config(monkeypatch):
    """演示骨架从 ``channels.yaml`` 现取 —— 改配置就跟着变，不会停在旧结构。"""
    from app.fake_llm import _demo_structure

    original = templates.load_channel_templates()
    patched = {**original,
               "xiaohongshu": {**original["xiaohongshu"],
                               "structure": "步骤一 → 步骤二"}}
    monkeypatch.setattr(templates, "load_channel_templates", lambda: patched)

    assert _demo_structure("保温杯", "xiaohongshu") == [
        "步骤一（围绕「保温杯」）", "步骤二（围绕「保温杯」）",
    ]


def test_demo_research_keywords_differ_by_channel():
    from app.fake_llm import _dynamic_research

    xhs = _dynamic_research("保温杯", "上班族", "xiaohongshu")["keywords"]
    wechat = _dynamic_research("保温杯", "上班族", "wechat")["keywords"]

    assert xhs != wechat
    assert all("保温杯" in k for k in xhs + wechat)


def test_demo_research_keywords_default_when_channel_missing():
    """向后兼容：不带渠道调用时仍返回一组可用关键词，不报错。"""
    from app.fake_llm import _dynamic_research

    kwargs = _dynamic_research("保温杯", "上班族")["keywords"]
    assert kwargs and all("保温杯" in k for k in kwargs)


def test_demo_fake_llm_strategy_role_still_routed():
    """FakeLLM 按 system 角色路由 —— 改了 strategy 分支别把路由关键字弄丢。"""
    from app.fake_llm import FakeLLMClient
    from app.agents.prompts import _sys

    raw = FakeLLMClient().chat([
        {"role": "system", "content": _sys("营销策略总监", "")},
        {"role": "user", "content": "<user_input>\n- topic: 保温杯\n- channel_id: xiaohongshu\n</user_input>"},
    ]).content
    payload = __import__("json").loads(raw)
    assert "error" not in payload
    assert payload.get("content_structure")
