"""守门节点测试：把确定性守门层接进流水线后的行为。

分两层：
1. **节点级**（直接调工厂，不起图）——判定口径、报告结构、绝不静默改写；
2. **图级**（真跑 marketing 流水线）——守门层接在创作与审校之间、
   不消耗 token、报告进阶段产出、`needs_human` 送到人工确认点。

核心约定（全文件反复验证）：
- 守门层**只自动修**「删信息以外、不改语义」的事（emoji / 标签 / 字数降档）；
- **违禁词与 AI 味一律只标记**，绝不静默改写文案。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents import nodes as nodes_pkg
from app.agents.graph import build_graph
from app.agents.nodes.guardrail import make_guardrail_node
from app.agents.pipeline_definition import load_default_pipeline
from app.llm import LLMResult

GUARDRAIL_MODULE = "app.agents.nodes.guardrail"

# 小红书（require_emoji true → emoji 最多留 3 个；require_hashtags true → 标签成对）
REAL_CHANNEL_ID = "xiaohongshu"


def _channel(**overrides) -> dict:
    """一个可控的渠道配置；默认关掉 emoji/标签规则，让断言只看被测维度。"""
    channel = {
        "name": "测试渠道",
        "max_chars": 1000,
        "require_hashtags": False,
        "require_emoji": False,
    }
    channel.update(overrides)
    return channel


def _patch_channel(monkeypatch, channel: dict) -> None:
    monkeypatch.setattr(f"{GUARDRAIL_MODULE}.get_channel", lambda _cid: channel)


def _patch_brand(monkeypatch, taboos: str) -> None:
    monkeypatch.setattr(
        f"{GUARDRAIL_MODULE}.get_brand_context", lambda _bid: {"taboos": taboos}
    )


def _state(*variants: dict, **overrides) -> dict:
    state: dict = {
        "task_id": 1,
        "input": {
            "topic": "智能保温杯",
            "brand_name": "暖芯",
            "target_audience": "白领",
            "channel_id": REAL_CHANNEL_ID,
            "extra_requirements": "",
        },
        "copywriting": list(variants),
    }
    state.update(overrides)
    return state


def _variant(body: str, *, title: str = "标题", **extra) -> dict:
    variant = {"title": title, "body": body, "hashtags": [], "notes": "n"}
    variant.update(extra)
    return variant


# ==========================================================================
# 一、节点级：判定口径
# ==========================================================================
def test_clean_copy_passes_and_changes_nothing(monkeypatch):
    _patch_channel(monkeypatch, _channel())
    node = make_guardrail_node()
    out = node(_state(_variant("这是一段干净的正文。")))

    report = out["guardrail"]
    assert report["passed"] is True
    assert report["needs_human"] == []
    assert report["auto_fixed"] == []
    assert report["stats"]["variants"] == 1
    # 干净文案不该被改动一个字符
    assert out["copywriting"][0]["body"] == "这是一段干净的正文。"
    assert out["stage_status"] == {"guardrail": "completed"}


def test_report_records_the_checked_copy_not_a_copy_of_it(monkeypatch):
    """回写的变体必须是守门层**实际检查过**的那一份，否则报告与交付物对不上。"""
    _patch_channel(monkeypatch, _channel(max_chars=20))
    body = "钩子句。" + "要点一：这里是相当长的细节说明文字。" * 3
    out = node_out = make_guardrail_node()(_state(_variant(body)))

    assert node_out["copywriting"][0]["body"] != body, "超限正文应被降档"
    assert len(node_out["copywriting"][0]["body"]) <= 20
    assert out["guardrail"]["variant_reports"][0]["downgraded"] is True


def test_auto_downgrade_is_reported_as_a_fix_not_as_a_problem(monkeypatch):
    _patch_channel(monkeypatch, _channel(max_chars=20))
    out = make_guardrail_node()(
        _state(_variant("钩子句。" + "要点一：这里是相当长的细节说明文字。" * 3))
    )
    report = out["guardrail"]
    assert report["stats"]["downgraded"] == 1
    assert any("降档" in note for note in report["auto_fixed"])
    # 降档成功且别无问题 → 不该升级成「需人工」
    assert report["needs_human"] == []
    assert report["passed"] is True


def test_forbidden_word_is_flagged_but_never_rewritten(monkeypatch):
    """分界线：违禁词只标记。静默替换会把「作者原意」换成工具的字。"""
    _patch_channel(monkeypatch, _channel())
    body = "全网最低价，错过后悔。"
    out = make_guardrail_node()(_state(_variant(body)))

    report = out["guardrail"]
    assert report["passed"] is False
    assert {hit["word"] for hit in report["draft_hits"]} >= {"全网最低"}
    assert any("全网最低" in note for note in report["needs_human"])
    # 原文一个字都不能被工具改掉
    assert out["copywriting"][0]["body"] == body


def test_source_scope_separates_brief_terms_from_the_draft(monkeypatch):
    """素材自带违禁词单独归档；成稿没抄它就不算成稿违规。"""
    _patch_channel(monkeypatch, _channel())
    state = _state(_variant("这是一段干净的正文。"))
    state["input"]["extra_requirements"] = "写的时候可以提「全网最低」这个说法"
    out = make_guardrail_node()(state)

    report = out["guardrail"]
    assert {hit["word"] for hit in report["source_hits"]} >= {"全网最低"}
    assert report["draft_hits"] == []
    assert report["passed"] is True


def test_brand_taboos_are_used_as_user_banned_words(monkeypatch):
    _patch_channel(monkeypatch, _channel())
    _patch_brand(monkeypatch, "最,第一,根治")
    out = make_guardrail_node()(_state(_variant("这个方案根治所有问题。")))

    hits = {hit["word"] for hit in out["guardrail"]["draft_hits"]}
    assert "根治" in hits
    assert out["guardrail"]["passed"] is False


def test_empty_or_sentencelike_taboos_do_not_create_fake_hits(monkeypatch):
    """品牌禁忌是自由文本，长句不能当词用 —— 否则误报会淹没真命中。"""
    _patch_channel(monkeypatch, _channel())
    _patch_brand(monkeypatch, "不夸大宣传、不承诺具体疗效")
    out = make_guardrail_node()(_state(_variant("一篇很普通的正文。")))

    assert out["guardrail"]["draft_hits"] == []
    assert out["guardrail"]["passed"] is True


def test_research_keywords_feed_the_title_check(monkeypatch):
    _patch_channel(monkeypatch, _channel())
    state = _state(_variant("正文", title="智能保温杯怎么选"))
    state["research"] = {"keywords": ["保温杯", "长效保温"]}
    out = make_guardrail_node()(state)

    dimensions = out["guardrail"]["variant_reports"][0]["title"]["dimensions"]
    assert dimensions.get("keyword_match") is not None
    # 标题含关键词 → 该维度应拿到正分
    assert dimensions["keyword_match"] > 0


def test_research_without_keywords_or_broken_shape_does_not_crash(monkeypatch):
    _patch_channel(monkeypatch, _channel())
    for research in (None, {}, {"keywords": "保温杯"}):
        state = _state(_variant("正文"))
        state["research"] = research
        out = make_guardrail_node()(state)
        assert out["guardrail"]["variant_reports"][0]["title"]["dimensions"][
            "keyword_match"
        ] is None


def test_junk_keyword_entries_are_coerced_not_crashed(monkeypatch):
    """调研输出是模型给的，可能夹着数字或空白：只能强制转字符串并丢掉空项。"""
    _patch_channel(monkeypatch, _channel())
    state = _state(_variant("正文"))
    state["research"] = {"keywords": [1, "", "  ", None]}
    out = make_guardrail_node()(state)
    assert out["guardrail"]["passed"] is True


def test_channel_without_max_chars_must_not_wipe_the_body(monkeypatch):
    """回归：max_chars 缺省时曾把 0 传给降档，结果正文被清空。"""
    _patch_channel(monkeypatch, {"name": "无上限渠道", "require_hashtags": False})
    body = "这是一段不该被清空的正文。"
    out = make_guardrail_node()(_state(_variant(body)))

    assert out["copywriting"][0]["body"] == body
    assert out["guardrail"]["passed"] is True


def test_no_variants_says_why_instead_of_silently_failing(monkeypatch):
    _patch_channel(monkeypatch, _channel())
    out = make_guardrail_node()(_state())

    report = out["guardrail"]
    assert report["passed"] is False
    assert any("无内容可查" in note for note in report["needs_human"])
    assert out["copywriting"] == []


def test_non_dict_variants_are_dropped_not_crashed(monkeypatch):
    _patch_channel(monkeypatch, _channel())
    out = make_guardrail_node()(_state(_variant("正文"), "字符串不是变体"))  # type: ignore[arg-type]
    assert len(out["copywriting"]) == 1
    assert out["guardrail"]["stats"]["variants"] == 1


def test_state_key_is_configurable_from_the_definition(monkeypatch):
    """状态键由流水线定义提供，工厂不再硬编码 —— YAML 是单一真源。"""
    _patch_channel(monkeypatch, _channel())
    out = make_guardrail_node(state_key="quality_report")(_state(_variant("正文")))
    assert "quality_report" in out
    assert out["stage_status"] == {"quality_report": "completed"}
    assert "guardrail" not in out


def test_real_channel_wiring_applies_xiaohongshu_emoji_cap(monkeypatch):
    """用真实 channels.yaml 跑一遍：至少证明渠道配置真的被读到了。

    小红书 require_emoji true → emoji 最多保留 3 个（cap3 策略）。
    这条断言同时守住「守门层读的是渠道文件，不是内置默认值」。
    """
    node = make_guardrail_node()
    body = "正文开始" + "🎉" * 6
    out = node(_state(_variant(body)))

    report = out["guardrail"]
    assert report["variant_reports"][0]["emoji_before"] == 6
    assert report["variant_reports"][0]["emoji_after"] == 3
    assert any("emoji" in note for note in report["auto_fixed"])


def test_hashtags_are_normalized_according_to_the_channel(monkeypatch):
    _patch_channel(
        monkeypatch, _channel(require_hashtags=True, hashtag_style="paired")
    )
    out = make_guardrail_node()(_state(_variant("正文", hashtags=["暖芯", "保温杯"])))
    assert out["copywriting"][0]["hashtags"] == ["#暖芯#", "#保温杯#"]
    assert out["guardrail"]["variant_reports"][0]["hashtag_notes"] == []


def test_notes_are_preserved_verbatim(monkeypatch):
    """notes 是模型自述，不对外发布，所以既不该被清空也不该被算进字数。"""
    _patch_channel(monkeypatch, _channel(max_chars=10))
    out = make_guardrail_node()(
        _state(_variant("钩子句。" + "要点一：这里是相当长的细节说明文字。" * 2,
                        notes="这里是模型的自我说明，需要原样保留"))
    )
    assert out["copywriting"][0]["notes"] == "这里是模型的自我说明，需要原样保留"


def test_guardrail_node_factory_takes_no_llm():
    """确定性节点不该有能力调用模型 —— 签名上就不给它 llm。

    （组合根的 NODE_BUILDERS 适配器会忽略传进来的 llm。）
    """
    import inspect

    signature = inspect.signature(make_guardrail_node)
    assert "llm" not in signature.parameters
    assert all("llm" not in str(p) for p in signature.parameters.values())


# ==========================================================================
# 二、图级：接进流水线的位置与语义
# ==========================================================================
RESEARCH = json.dumps(
    {"summary": "s", "trends": ["t"], "audience_insights": ["a"],
     "pain_points": ["p"], "keywords": ["保温杯"]}, ensure_ascii=False)
COMPETITOR = json.dumps(
    {"competitors": [{"name": "C", "positioning": "p", "selling_points": ["s"],
                      "content_strategy": "c", "strengths": [], "weaknesses": []}],
     "gaps": ["g"]}, ensure_ascii=False)
STRATEGY = json.dumps(
    {"target_audience": "白领", "core_value_proposition": "v", "key_messages": ["m"],
     "tone_guidance": "g", "content_angles": ["a"], "channel_strategy": "s"},
    ensure_ascii=False)
REVIEW_PASS = json.dumps({"passed": True, "feedback": "", "checklist": []},
                         ensure_ascii=False)


class RecordingLLM:
    """按顺序返回固定响应，并记录每次收到的提示词。"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    def chat(self, messages, **kwargs):
        prompt = "\n".join(m.get("content", "") for m in messages)
        self.prompts.append(prompt)
        content = self.responses[self.calls]
        self.calls += 1
        return LLMResult(
            content=content, model="recording", prompt_tokens=100,
            completion_tokens=50, latency_seconds=0.1,
        )


def _initial_state() -> dict:
    return {
        "task_id": 1,
        "input": {
            "topic": "智能保温杯",
            "brand_name": "暖芯",
            "target_audience": "25-35 都市白领",
            "channel_id": REAL_CHANNEL_ID,
            "extra_requirements": "",
        },
    }


def _copy(body: str, *, title: str = "智能保温杯怎么选", extra: dict | None = None) -> str:
    variant: dict = {"title": title, "body": body, "hashtags": [], "notes": "n"}
    if extra:
        variant.update(extra)
    return json.dumps({"variants": [variant]}, ensure_ascii=False)


# 小红书的渠道内容契约字段（见 channels.yaml 的 content_contract）。
# **断言「needs_human 为空」的用例必须一并给出这些字段** —— 否则守门层会如实
# 报出契约缺口，那是正确行为而不是残留。
XHS_CONTRACT_FIELDS = {
    "hook_type": "场景痛点",
    "scenario": "地铁上水早就凉了，通勤党最有体会",
    "experience_detail": "早上装的温水到下班还是热的，杯盖单手就能拧开",
    "selling_points": ["长效保温", "单手开盖", "密封防漏"],
    "tags_by_tier": {
        "hot": ["保温杯推荐", "通勤好物"],
        "longtail": ["秋冬必备", "真实体验", "好物分享"],
    },
    "cta": "先收藏这份判断思路，需要时再翻出来对照",
}


# 小红书标题区间（P5 起由 channels.yaml 显式声明并生效）为 18-20 字。
# 需要断言「needs_human 为空」的用例必须用落在区间内的标题，否则
# 「标题长度不合规」会如实出现在报告里，那是正确行为而不是残留。
TITLE_XHS_IN_RANGE = "冬季通勤保温杯实测：30天后的真实感受"  # 19 字


def _run_marketing(tmp_path, copy_body: str):
    fake = RecordingLLM([RESEARCH, COMPETITOR, STRATEGY, _copy(copy_body), REVIEW_PASS])
    graph = build_graph(fake, db_path=Path(tmp_path) / "ckpt.db")
    result = graph.invoke(_initial_state(), {"configurable": {"thread_id": "task-1"}})
    return fake, graph, result


def test_guardrail_stage_runs_between_copywriting_and_review_without_llm_cost(tmp_path):
    fake, _, result = _run_marketing(tmp_path, "这是一段干净的正文。")

    assert result["stage_status"] == {
        "research": "completed", "competitor": "completed", "strategy": "completed",
        "copywriting": "completed",  # P4 起 humanize 插在创作与守门之间（非公众号则跳过）
        "humanize": "skipped", "guardrail": "completed", "review": "completed",
    }
    # 7 个阶段（含去 AI 味与守门）但只有 5 次模型调用：
    # 守门层是确定性步骤（零 token），去 AI 味在小红书渠道直接跳过
    assert fake.calls == 5
    assert result["guardrail"]["channel_id"] == REAL_CHANNEL_ID
    assert result["guardrail"]["passed"] is True


def test_review_receives_the_guardrail_findings_in_its_prompt(tmp_path):
    """放在 review 之前的理由要落到实处：审校看得到工具的结论。"""
    fake, _, _ = _run_marketing(tmp_path, "全网最低价，错过后悔。")

    review_prompt = fake.prompts[4]  # 第 5 次调用是审校
    assert "确定性守门层检出" in review_prompt
    assert "全网最低" in review_prompt
    assert "不要自行重新估算" in review_prompt


def test_guardrail_survives_the_review_loop_and_reruns(tmp_path):
    """审校打回后重跑创作，守门层必须跟着重跑并刷新报告。"""
    reject = json.dumps({"passed": False, "feedback": "换个角度", "checklist": []},
                        ensure_ascii=False)
    fake = RecordingLLM([
        RESEARCH, COMPETITOR, STRATEGY, _copy("全网最低价"), reject,
        _copy("这是一段干净的正文。", title=TITLE_XHS_IN_RANGE, extra=XHS_CONTRACT_FIELDS),
        REVIEW_PASS,
    ])
    graph = build_graph(fake, db_path=Path(tmp_path) / "ckpt.db")
    result = graph.invoke(_initial_state(), {"configurable": {"thread_id": "task-1"}})

    assert result["revision_round"] == 1
    # 第二轮的守门报告对应第二轮（干净）的文案，而不是第一轮的残留
    assert result["guardrail"]["passed"] is True
    assert result["guardrail"]["draft_hits"] == []
    assert result["needs_human"] == []


def test_needs_human_reaches_the_interrupt_payload(tmp_path):
    """「只标记」的约定必须传到最后一站：用户看不到就没法处理。"""
    _, _, result = _run_marketing(tmp_path, "全网最低价，错过后悔。")

    interrupts = result["__interrupt__"]
    payload = interrupts[0].value
    assert any("全网最低" in note for note in payload["needs_human"])
    assert payload["guardrail"]["passed"] is False
    # 人工确认看到的文案 = 守门层检查过的那一份
    assert "全网最低" in payload["variants"][0]["body"]


def test_guardrail_stage_report_is_json_serializable_for_the_database(tmp_path):
    """报告要写进 stages.output（JSON 文本），序列化不能失败。"""
    _, _, result = _run_marketing(tmp_path, "全网最低价，错过后悔。")
    text = json.dumps(result["guardrail"], ensure_ascii=False)
    assert "channel_id" in text


@pytest.mark.parametrize("channel_id", ["xiaohongshu", "wechat", "weibo", "product_page"])
def test_every_real_channel_is_guardable(monkeypatch, channel_id):
    """四个渠道都能过守门层 —— 覆盖 channels.yaml 与守护逻辑的接口一致性。"""
    state = _state(_variant("正文"))
    state["input"]["channel_id"] = channel_id
    out = make_guardrail_node()(state)
    assert out["guardrail"]["channel_id"] == channel_id
    assert out["guardrail"]["max_chars"] > 0


def test_default_pipeline_declares_guardrail_between_copywriting_and_review():
    """位置断言：定义里守门层的入边来自去 AI 味、出边去审校。

    P4 起 humanize 插在创作与守门之间（去 AI 味接在守门层**之前**，
    这样守门报告描述的才是最终交付的那份文案）。humanize 的位置断言
    在 ``test_humanize_node.py``。
    """
    definition = load_default_pipeline()
    assert [edge.targets for edge in definition.edges_from("humanize")] == [
        ("guardrail",)
    ]
    assert [edge.targets for edge in definition.edges_from("guardrail")] == [("review",)]


def test_guardrail_is_exported_from_the_nodes_package():
    assert nodes_pkg.make_guardrail_node is make_guardrail_node
