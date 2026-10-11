"""去 AI 味节点测试：混合闭环（预扫 → 改写 → 复扫 → 验收）。

分三层：
1. **纯函数**（``evaluate_rewrite``）——验收判定必须可脱离模型单独验证；
2. **节点级**（直接调工厂，不起图）——四种处置（采纳/拒绝/无需/失败）、
   成本闸门、报告结构；
3. **图级**（真跑 marketing 流水线）——位置正确、只对公众号生效、
   **守门层报告描述的是改写后的那一份文案**。

核心约定（全文件反复验证）：
- **改写失败/更差 → 永远保留原文**。否则「去 AI 味」会变成「把交付物搞丢」；
- 提示词与扫描器**共用同一份规则资产**（``ai_tells_zh.yaml``）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.graph import build_graph, definition_editable_stage_set
from app.agents.nodes.humanize import (
    DECISION_ACCEPTED,
    DECISION_FAILED,
    DECISION_REJECTED,
    DECISION_SKIPPED,
    ENABLED_CHANNEL_ID,
    MAX_REWRITES_PER_RUN,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    evaluate_rewrite,
    make_humanize_node,
    needs_rewrite,
)
from app.agents.pipeline_definition import load_default_pipeline
from app.agents.prompts import build_humanize_messages
from app.guardrails import load_ai_tell_rules, render_patterns_for_prompt, scan_ai_tells
from app.llm import LLMResult
from app.templates import get_channel

WECHAT = ENABLED_CHANNEL_ID
OTHER = "xiaohongshu"

# 含 fatal（否定对举）+ warning（时代铺垫、企业黑话）的「有 AI 味」正文
DIRTY = "在当今社会，我们赋能客户。这不是价格战，而是价值战。"
# 单句版：守门层的降档会把多句正文按句重排（换行），单句能避免这层噪声干扰断言
DIRTY_ONE_LINE = "这不是价格战，而是价值战。"
# 干净的营销文案：无 fatal / warning 命中
CLEAN = "上周把包装换成可回收纸盒，退货率降了 1.2%。"
# 只命中 info 级（中文里混入半角标点）
INFO_ONLY = "中文,中文,中文"


class FakeLLM:
    """按序返回固定响应，并记录提示词与调用次数。"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    def chat(self, messages, **kwargs):
        self.prompts.append("\n".join(m.get("content", "") for m in messages))
        content = self.responses[self.calls]
        self.calls += 1
        return LLMResult(
            content=content, model="fake", prompt_tokens=100,
            completion_tokens=50, latency_seconds=0.1,
        )


class BoomLLM:
    def chat(self, *args, **kwargs):
        raise RuntimeError("模型不可用")


def _variant(body: str, *, title: str = "标题", **extra) -> dict:
    variant = {"title": title, "body": body, "hashtags": [], "notes": "模型自述"}
    variant.update(extra)
    return variant


def _state(*variants: dict, channel_id: str = WECHAT) -> dict:
    return {
        "task_id": 1,
        "input": {
            "topic": "可回收包装",
            "brand_name": "暖芯",
            "target_audience": "白领",
            "channel_id": channel_id,
            "extra_requirements": "",
        },
        "copywriting": list(variants),
    }


def _rewrite(body: str, changes: list[str] | None = None) -> str:
    return json.dumps(
        {"body": body, "changes": changes or ["去套话"]}, ensure_ascii=False
    )


# ==========================================================================
# 一、纯函数：验收判定
# ==========================================================================
def test_evaluate_accepts_a_genuine_improvement():
    ok, reason = evaluate_rewrite(
        before={"total": 5, "fatal": 2}, after={"total": 0, "fatal": 0},
        chars_before=100, chars_after=90,
    )
    assert ok is True
    assert "5 → 0" in reason


def test_evaluate_rejects_when_tells_do_not_decrease():
    """没减少就不算「去味成功」——没有理由用改过的文字替换原文。"""
    ok, reason = evaluate_rewrite(
        before={"total": 1, "fatal": 0}, after={"total": 1, "fatal": 0},
        chars_before=100, chars_after=95,
    )
    assert ok is False
    assert "未减少" in reason


def test_evaluate_rejects_a_rewrite_that_makes_tells_worse():
    ok, _ = evaluate_rewrite(
        before={"total": 1, "fatal": 0}, after={"total": 8, "fatal": 2},
        chars_before=100, chars_after=95,
    )
    assert ok is False


def test_evaluate_rejects_new_fatal_even_if_total_drops():
    """致命句式变多必须拒绝，哪怕总命中数下降了。"""
    ok, reason = evaluate_rewrite(
        before={"total": 3, "fatal": 0}, after={"total": 1, "fatal": 1},
        chars_before=100, chars_after=95,
    )
    assert ok is False
    assert "致命" in reason


def test_evaluate_rejects_an_empty_rewrite():
    ok, reason = evaluate_rewrite(
        before={"total": 3, "fatal": 0}, after={"total": 0, "fatal": 0},
        chars_before=100, chars_after=0,
    )
    assert ok is False
    assert "为空" in reason


def test_evaluate_rejects_a_collapsed_rewrite():
    """长度塌陷通常意味着「删掉一整段」而不是「改写」——那是丢信息。"""
    ok, reason = evaluate_rewrite(
        before={"total": 3, "fatal": 0}, after={"total": 1, "fatal": 0},
        chars_before=100, chars_after=10,
    )
    assert ok is False
    assert "变短" in reason


def test_needs_rewrite_ignores_info_level_hits():
    hits = scan_ai_tells(INFO_ONLY)
    assert hits, "本用例前提：该正文至少命中一条规则"
    assert all(hit.severity == "info" for hit in hits)
    assert needs_rewrite(hits) is False


def test_needs_rewrite_triggers_on_warning_and_fatal():
    assert needs_rewrite(scan_ai_tells(DIRTY)) is True


# ==========================================================================
# 二、提示词：与扫描器共用同一份资产
# ==========================================================================
def test_prompt_renders_every_pattern_id_exactly_once():
    """渲染漏掉哪条，模型就永远不会改哪条；重复哪条，说明两处抄写了资产。"""
    messages = build_humanize_messages(
        _state(), _variant(DIRTY), scan_ai_tells(DIRTY), {"name": "微信公众号"}
    )
    prompt = "\n".join(m["content"] for m in messages)
    lines = prompt.splitlines()
    for pattern in load_ai_tell_rules().patterns:
        matching = [
            line for line in lines
            if line.startswith(f"- {pattern.id}：") or line.startswith(f"- {pattern.id}【")
        ]
        assert len(matching) == 1, f"{pattern.id} 在 humanize prompt 里出现 {len(matching)} 次"


def test_prompt_embeds_the_rendered_asset_verbatim():
    messages = build_humanize_messages(
        _state(), _variant(DIRTY), scan_ai_tells(DIRTY), {"name": "微信公众号"}
    )
    prompt = "\n".join(m["content"] for m in messages)
    for fragment in ("### 内容套路", "【必须消除】", "结构指标 em_dash_count ≥ 1"):
        assert fragment in prompt
    assert render_patterns_for_prompt().splitlines()[-1] in prompt


def test_prompt_lists_the_detected_hits_with_their_samples():
    hits = scan_ai_tells(DIRTY)
    messages = build_humanize_messages(_state(), _variant(DIRTY), hits, {})
    prompt = "\n".join(m["content"] for m in messages)
    assert "必须逐条消除" in prompt
    assert DIRTY in prompt, "原标题正文要原样放进 prompt，模型才有东西可改"


def test_prompt_forbids_inventing_facts_and_shrinking():
    messages = build_humanize_messages(_state(), _variant(DIRTY), scan_ai_tells(DIRTY), {})
    prompt = "\n".join(m["content"] for m in messages)
    assert "不得新增原文没有的信息" in prompt
    assert "60%" in prompt


# ==========================================================================
# 三、节点级：四种处置
# ==========================================================================
def test_non_wechat_channel_is_skipped_without_calling_the_model():
    fake = FakeLLM([])
    out = make_humanize_node(fake)(_state(_variant(DIRTY), channel_id=OTHER))

    report = out["humanize"]
    assert report["enabled"] is False
    assert "仅对公众号" in report["reason"]
    assert out["stage_status"] == {"humanize": STATUS_SKIPPED}
    assert fake.calls == 0, "渠道不适用时不得消耗 token"
    # 不改写就不要碰 copywriting（避免制造无意义的回写）
    assert "copywriting" not in out


def test_clean_copy_needs_no_rewrite_and_costs_nothing():
    assert needs_rewrite(scan_ai_tells(CLEAN)) is False, "本用例前提：正文无 warning/fatal"
    fake = FakeLLM([])
    out = make_humanize_node(fake)(_state(_variant(CLEAN)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_SKIPPED
    assert out["stage_status"] == {"humanize": STATUS_COMPLETED}
    assert fake.calls == 0


def test_info_only_hits_do_not_spend_a_model_call():
    fake = FakeLLM([])
    out = make_humanize_node(fake)(_state(_variant(INFO_ONLY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_SKIPPED
    assert fake.calls == 0


def test_accepted_rewrite_replaces_the_body_and_records_the_delta():
    fake = FakeLLM([_rewrite(CLEAN)])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))

    report = out["humanize"]
    variant_report = report["variant_reports"][0]
    assert variant_report["decision"] == DECISION_ACCEPTED
    assert out["copywriting"][0]["body"] == CLEAN
    assert variant_report["ai_tells_after"] < variant_report["ai_tells_before"]
    assert variant_report["changes"] == ["去套话"]
    assert out["stage_status"] == {"humanize": STATUS_COMPLETED}


def test_rejected_rewrite_keeps_the_original_body_byte_for_byte():
    """回归护栏：改写更差时必须一字不动地保留原文。"""
    worse = DIRTY + "赋能赋能赋能。"  # 命中数变多
    fake = FakeLLM([_rewrite(worse)])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_REJECTED
    assert out["copywriting"][0]["body"] == DIRTY


def test_rewrite_that_introduces_a_fatal_tell_is_rejected():
    fake = FakeLLM([_rewrite("并非运气，而是准备。")])
    out = make_humanize_node(fake)(_state(_variant(DIRTY + "我们做得很好，值得信赖。")))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_REJECTED


def test_rewrite_that_drops_too_much_is_rejected():
    fake = FakeLLM([_rewrite("赋能。")])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_REJECTED


def test_model_error_degrades_to_the_original_and_does_not_raise():
    """失败必须降级而不是抛异常：抛出去会让整个任务 failed。"""
    out = make_humanize_node(BoomLLM())(_state(_variant(DIRTY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_FAILED
    assert out["copywriting"][0]["body"] == DIRTY


def test_non_json_output_degrades_to_the_original():
    fake = FakeLLM(["这不是 JSON"])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_FAILED
    assert out["copywriting"][0]["body"] == DIRTY


def test_json_array_output_degrades_to_the_original():
    fake = FakeLLM(['["不是对象"]'])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_FAILED
    assert out["copywriting"][0]["body"] == DIRTY


def test_rewrite_missing_the_body_field_degrades_to_the_original():
    fake = FakeLLM([json.dumps({"changes": ["忘了给正文"]}, ensure_ascii=False)])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))

    assert out["humanize"]["variant_reports"][0]["decision"] == DECISION_FAILED
    assert out["copywriting"][0]["body"] == DIRTY


def test_notes_and_extra_fields_survive_the_rewrite():
    """改写只替换 body：模型自述与渠道专属字段不该被吞掉。"""
    fake = FakeLLM([_rewrite(CLEAN)])
    out = make_humanize_node(fake)(
        _state(_variant(DIRTY, notes="原自述", hook_type="数字对比"))
    )

    kept = out["copywriting"][0]
    assert kept["body"] == CLEAN
    assert kept["notes"] == "原自述"
    assert kept["hook_type"] == "数字对比"


def test_multiple_variants_are_handled_independently():
    fake = FakeLLM([_rewrite(CLEAN), _rewrite(DIRTY + "赋能赋能。")])
    out = make_humanize_node(fake)(_state(_variant(DIRTY), _variant(DIRTY)))

    decisions = [r["decision"] for r in out["humanize"]["variant_reports"]]
    assert decisions == [DECISION_ACCEPTED, DECISION_REJECTED]
    assert out["copywriting"][0]["body"] == CLEAN
    assert out["copywriting"][1]["body"] == DIRTY


def test_rewrite_budget_caps_model_calls():
    """成本闸门：脏变体再多，单次执行也不会无限调用模型。"""
    fake = FakeLLM([_rewrite(CLEAN)] * MAX_REWRITES_PER_RUN)
    variants = [_variant(DIRTY, title=f"标题{i}") for i in range(MAX_REWRITES_PER_RUN + 2)]
    out = make_humanize_node(fake)(_state(*variants))

    assert fake.calls == MAX_REWRITES_PER_RUN
    decisions = [r["decision"] for r in out["humanize"]["variant_reports"]]
    assert decisions.count(DECISION_SKIPPED) == 2
    assert len(out["copywriting"]) == MAX_REWRITES_PER_RUN + 2, "超出预算的变体原样保留"


def test_cost_is_accumulated_per_model_call():
    fake = FakeLLM([_rewrite(CLEAN), _rewrite(CLEAN)])
    out = make_humanize_node(fake)(_state(_variant(DIRTY), _variant(DIRTY)))

    # 单次调用：100/1e6*2 + 50/1e6*8 = 0.0006
    assert out["total_cost"] == pytest.approx(0.0012)
    assert out["total_latency"] == pytest.approx(0.2)


def test_no_model_call_means_no_cost_keys_at_all():
    fake = FakeLLM([])
    out = make_humanize_node(fake)(_state(_variant(CLEAN)))
    assert "total_cost" not in out and "total_latency" not in out


def test_empty_variants_do_not_crash():
    fake = FakeLLM([])
    out = make_humanize_node(fake)(_state())
    assert out["humanize"]["variant_reports"] == []
    assert out["copywriting"] == []
    assert fake.calls == 0


def test_non_dict_variants_are_dropped_not_crashed():
    fake = FakeLLM([])
    out = make_humanize_node(fake)(_state(_variant(CLEAN), "字符串不是变体"))  # type: ignore[arg-type]
    assert len(out["copywriting"]) == 1


def test_state_key_is_configurable_from_the_definition():
    fake = FakeLLM([])
    out = make_humanize_node(fake, state_key="human_face")(_state(_variant(CLEAN)))
    assert "human_face" in out
    assert out["stage_status"] == {"human_face": STATUS_COMPLETED}
    assert "humanize" not in out


def test_report_is_json_serializable_for_the_database():
    fake = FakeLLM([_rewrite(CLEAN)])
    out = make_humanize_node(fake)(_state(_variant(DIRTY)))
    assert "channel_id" in json.dumps(out["humanize"], ensure_ascii=False)


# ==========================================================================
# 四、图级：位置、范围与「报告描述的是最终文案」
# ==========================================================================
RESEARCH = json.dumps(
    {"summary": "s", "trends": ["t"], "audience_insights": ["a"],
     "pain_points": ["p"], "keywords": ["可回收"]}, ensure_ascii=False)
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


def _copy(body: str) -> str:
    return json.dumps(
        {"variants": [{"title": "标题", "body": body, "hashtags": [], "notes": "n"}]},
        ensure_ascii=False,
    )


def _run(tmp_path, body: str, *, channel_id: str, rewritten: str | None = None):
    responses = [RESEARCH, COMPETITOR, STRATEGY, _copy(body)]
    if rewritten is not None:
        responses.append(_rewrite(rewritten))
    responses.append(REVIEW_PASS)
    fake = FakeLLM(responses)
    graph = build_graph(fake, db_path=Path(tmp_path) / "ckpt.db")
    state = _state(channel_id=channel_id)
    state["task_id"] = 1
    result = graph.invoke(state, {"configurable": {"thread_id": "task-1"}})
    return fake, graph, result


def test_wechat_pipeline_runs_humanize_and_costs_one_extra_call(tmp_path):
    fake, _, result = _run(tmp_path, DIRTY, channel_id=WECHAT, rewritten=CLEAN)

    assert result["stage_status"] == {
        "research": "completed", "competitor": "completed", "strategy": "completed",
        "copywriting": "completed", "humanize": "completed", "guardrail": "completed",
        "review": "completed",
    }
    assert fake.calls == 6, "7 个阶段，其中守门层零 token → 6 次模型调用"
    assert result["humanize"]["variant_reports"][0]["decision"] == DECISION_ACCEPTED


def test_non_wechat_pipeline_still_costs_five_calls(tmp_path):
    fake, _, result = _run(tmp_path, DIRTY, channel_id=OTHER)

    assert fake.calls == 5, "非公众号：去 AI 味跳过一次模型都不调"
    assert result["stage_status"]["humanize"] == STATUS_SKIPPED


def test_guardrail_report_describes_the_rewritten_copy(tmp_path):
    """P3 的不变式必须继续成立：报告与交付物是同一份文字。

    若把 humanize 放在守门层之后，这里就会失败 —— 报告会描述一份
    已经被改写掉的正文，用户按报告去核对交付物时对不上。
    """
    _, _, result = _run(tmp_path, DIRTY, channel_id=WECHAT, rewritten=CLEAN)

    # 交付物 = 改写稿
    assert result["copywriting"][0]["body"] == CLEAN
    # 守门层扫的也是改写稿：脏正文的 fatal 已消失，报告因此判「通过」
    assert result["guardrail"]["passed"] is True
    assert result["guardrail"]["ai_tell_stats"]["fatal"] == 0
    assert result["guardrail"]["variant_reports"][0]["word_hits"] == []


def test_guardrail_still_catches_what_the_rewrite_notices_nothing_about(tmp_path):
    """改写不是万能的：改写稿本身超长时，守门层的降档照旧生效。

    这条同时证明去 AI 味**没有绕过**守门层 —— 它接在守门层之前，
    而不是替掉守门层。
    """
    max_chars = int(get_channel(WECHAT)["max_chars"])
    long_clean = "我们的包装成本在下降，" * (max_chars // 10 + 20) + "。"
    # 前提：这版改写稿本身干净（不触发改写闸门），但长度确实超限
    assert needs_rewrite(scan_ai_tells(long_clean)) is False
    assert len(long_clean) > max_chars

    _, _, result = _run(tmp_path, DIRTY, channel_id=WECHAT, rewritten=long_clean)

    assert result["humanize"]["variant_reports"][0]["decision"] == DECISION_ACCEPTED
    assert result["guardrail"]["stats"]["downgraded"] == 1
    assert len(result["copywriting"][0]["body"]) <= max_chars


def test_humanize_failure_still_lets_the_task_reach_human_confirm(tmp_path):
    """改写失败只是降级，不能让整条流水线失败。"""
    responses = [RESEARCH, COMPETITOR, STRATEGY, _copy(DIRTY_ONE_LINE), "不是 JSON",
                 REVIEW_PASS]
    fake = FakeLLM(responses)
    graph = build_graph(fake, db_path=Path(tmp_path) / "ckpt.db")
    state = _state(channel_id=WECHAT)
    state["task_id"] = 1
    result = graph.invoke(state, {"configurable": {"thread_id": "task-1"}})

    assert "__interrupt__" in result
    assert result["humanize"]["variant_reports"][0]["decision"] == DECISION_FAILED
    assert result["copywriting"][0]["body"] == DIRTY_ONE_LINE, "失败时交付的是原文"
    # 原文的 AI 味仍在 → 守门层会替它把这个问题喊出来
    assert result["guardrail"]["passed"] is False
    assert any("AI 味" in note for note in result["needs_human"])


def test_interrupt_payload_carries_the_humanize_report(tmp_path):
    """降级原因要能被人看到，否则「保留原文」只存在于 JSON 里。"""
    _, _, result = _run(tmp_path, DIRTY, channel_id=WECHAT, rewritten=CLEAN)
    payload = result["__interrupt__"][0].value
    assert payload["humanize"]["enabled"] is True
    assert payload["humanize"]["variant_reports"][0]["decision"] == DECISION_ACCEPTED


def test_definition_places_humanize_between_copywriting_and_guardrail():
    definition = load_default_pipeline()
    assert [edge.targets for edge in definition.edges_from("copywriting")] == [("humanize",)]
    assert [edge.targets for edge in definition.edges_from("humanize")] == [("guardrail",)]


def test_humanize_is_an_agent_stage_but_not_editable():
    node = load_default_pipeline().node("humanize")
    assert node.is_agent and node.calls_llm
    assert node.is_stage
    assert node.is_editable is False
    assert node.prompt_key == "humanize"
    assert node.temperature == pytest.approx(0.4)


def test_graph_helper_excludes_humanize_from_editable_stages():
    class _Graph:
        def __init__(self, definition):
            self._pipeline_definition = definition

    editable = definition_editable_stage_set(_Graph(load_default_pipeline()))
    assert "humanize" not in editable
    assert "guardrail" not in editable
    assert editable == {"research", "competitor", "strategy", "copywriting", "review"}
