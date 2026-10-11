"""中文 AI 味确定性预扫描测试。

守门层只判定「确定性可判定」的部分；语义改写归 humanize 节点。
这里同时给规则资产加质量闸门，防止 YAML 悄悄漂移。
"""
from __future__ import annotations

from app.guardrails.ai_tells import (
    METRICS,
    SEVERITY_FATAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    ai_tell_stats,
    load_ai_tell_rules,
    render_patterns_for_prompt,
    scan_ai_tells,
)


# --------------------------------------------------------------------------
# 规则资产质量闸门
# --------------------------------------------------------------------------
def test_rules_asset_loads_and_covers_transferred_patterns():
    rules = load_ai_tell_rules()
    # humanizer 资产原有 24 种模式，中文重建后不应少于这个量级
    assert len(rules.patterns) >= 24


def test_every_pattern_carries_an_actionable_suggestion():
    for pattern in load_ai_tell_rules().patterns:
        assert pattern.suggestion.strip(), f"{pattern.id} 缺少改写建议"
        assert len(pattern.suggestion) >= 8, f"{pattern.id} 的建议过于笼统"


def test_every_pattern_declares_a_known_severity_and_category():
    rules = load_ai_tell_rules()
    known_severity = {SEVERITY_FATAL, SEVERITY_WARNING, SEVERITY_INFO}
    for pattern in rules.patterns:
        assert pattern.severity in known_severity, f"{pattern.id} 级别未知"
        assert pattern.category, f"{pattern.id} 缺少类别"


def test_every_metric_rule_points_at_a_registered_metric():
    """规则里写了 metric 名但注册表没有，会静默失效——必须挡住。"""
    for pattern in load_ai_tell_rules().patterns:
        if pattern.kind != "metric":
            continue
        assert pattern.metric in METRICS, f"{pattern.id} 引用了未注册指标 {pattern.metric}"
        assert pattern.threshold >= 1


def test_pattern_ids_are_unique():
    ids = [pattern.id for pattern in load_ai_tell_rules().patterns]
    assert len(ids) == len(set(ids))


def test_all_three_kinds_are_present():
    kinds = {pattern.kind for pattern in load_ai_tell_rules().patterns}
    assert kinds == {"keyword", "regex", "metric"}


def test_hit_carries_readable_category_label():
    """命中要带中文分类名（前端直接渲染，不硬编码 category id 映射）。"""
    rules = load_ai_tell_rules()
    hits = scan_ai_tells("这不是价格战，而是价值战。我们赋能客户。")
    for hit in hits:
        assert hit.category_label == rules.category_labels.get(hit.category, hit.category)
        # to_dict 也要带出，否则报告序列化后前端拿不到
        assert hit.to_dict()["category_label"] == hit.category_label


# --------------------------------------------------------------------------
# 致命句式（fatal）
# --------------------------------------------------------------------------
def test_negative_parallel_is_fatal():
    hits = scan_ai_tells("这不是价格战，而是价值战。")
    assert any(hit.pattern_id == "negative_parallel" and hit.severity == SEVERITY_FATAL for hit in hits)


def test_negation_frame_is_fatal():
    hits = scan_ai_tells("并非运气，而是准备。")
    assert any(hit.pattern_id == "negation_frame" for hit in hits)


def test_not_x_but_y_framework_is_fatal():
    hits = scan_ai_tells("与其说他在写作，不如说他在思考。")
    assert any(hit.pattern_id == "not_x_but_y" for hit in hits)


def test_severity_filter_returns_only_fatal():
    hits = scan_ai_tells("这不是价格战，而是价值战。我们要赋能业务。", severities=(SEVERITY_FATAL,))
    assert hits, "应至少命中一条 fatal"
    assert {hit.severity for hit in hits} == {SEVERITY_FATAL}


def test_fatal_ids_are_exposed_for_review_gate():
    fatal = load_ai_tell_rules().fatal_ids()
    assert "negative_parallel" in fatal
    assert len(fatal) >= 3


# --------------------------------------------------------------------------
# 词表命中
# --------------------------------------------------------------------------
def test_business_jargon_hits_and_counts_each_occurrence():
    hits = scan_ai_tells("赋能客户，赋能团队，赋能自己。")
    jargon = next(hit for hit in hits if hit.pattern_id == "corporate_jargon")
    assert jargon.count == 3
    assert len(jargon.samples) == 3


def test_samples_are_truncated_to_max_three():
    hits = scan_ai_tells("赋能。赋能。赋能。赋能。赋能。")
    jargon = next(hit for hit in hits if hit.pattern_id == "corporate_jargon")
    assert jargon.count == 5
    assert len(jargon.samples) == 3


def test_era_preamble_and_vague_attribution_detected():
    hits = scan_ai_tells("在当今社会，业内人士认为这个行业变了。")
    ids = {hit.pattern_id for hit in hits}
    assert "era_preamble" in ids
    assert "vague_attribution" in ids


def test_chatbot_residue_is_flagged():
    """对话式残留出现在成稿正文里是明确的失败信号。"""
    hits = scan_ai_tells("以下是为你准备的内容。")
    assert any(hit.pattern_id == "chatbot_residue" for hit in hits)


def test_clean_marketing_copy_has_no_fatal_hits():
    """正常中文营销文案不该被判 fatal。"""
    text = "上周我们把包装换成了可回收纸盒。成本涨了 3 分钱，退货率降了 1.2%。"
    hits = scan_ai_tells(text, severities=(SEVERITY_FATAL,))
    assert hits == []


# --------------------------------------------------------------------------
# 结构指标（metric）
# --------------------------------------------------------------------------
def test_em_dash_metric_counts_each_occurrence():
    hits = scan_ai_tells("他说——这是对的——我信。")
    dash = next(hit for hit in hits if hit.pattern_id == "em_dash_overuse")
    assert dash.count == 2


def test_single_em_dash_is_already_flagged():
    """阈值就是 1：破折号在中文营销文案里基本不该出现。"""
    hits = scan_ai_tells("只有一句话——就这样。")
    assert any(hit.pattern_id == "em_dash_overuse" for hit in hits)


def test_paragraph_too_long_metric_reports_sentence_count():
    hits = scan_ai_tells("第一句。第二句。第三句。第四句。")
    para = next(hit for hit in hits if hit.pattern_id == "para_too_long")
    assert para.count == 4


def test_paragraph_at_threshold_boundary_is_flagged():
    """阈值是 ≥3，所以刚好 3 句的段落已经命中（3 句是引导线，不是安全区）。"""
    hits = scan_ai_tells("第一句。第二句。第三句。")
    para = next(hit for hit in hits if hit.pattern_id == "para_too_long")
    assert para.count == 3


def test_paragraph_below_threshold_is_not_flagged():
    hits = scan_ai_tells("第一句。第二句。")
    assert not any(hit.pattern_id == "para_too_long" for hit in hits)


def test_short_paragraphs_are_not_flagged():
    hits = scan_ai_tells("第一句。第二句。\n\n另一段。")
    assert not any(hit.pattern_id == "para_too_long" for hit in hits)


def test_bold_list_heading_pattern_is_flagged():
    hits = scan_ai_tells("- **重点一**：内容\n- **重点二**：内容")
    assert any(hit.pattern_id == "inline_heading_list" for hit in hits)


def test_emoji_in_markdown_heading_is_flagged():
    hits = scan_ai_tells("# 标题✨\n正文没有 emoji。")
    heading = next(hit for hit in hits if hit.pattern_id == "emoji_in_heading")
    assert heading.count == 1


def test_halfwidth_punctuation_in_chinese_is_flagged():
    hits = scan_ai_tells("中文,中文,中文")
    assert any(hit.pattern_id == "halfwidth_punct" for hit in hits)


def test_plain_chinese_punctuation_is_not_flagged_as_halfwidth():
    hits = scan_ai_tells("中文，中文。中文！")
    assert not any(hit.pattern_id == "halfwidth_punct" for hit in hits)


# --------------------------------------------------------------------------
# 统计、渲染、边界
# --------------------------------------------------------------------------
def test_stats_aggregate_by_severity_and_category():
    hits = scan_ai_tells("这不是价格战，而是价值战。我们赋能客户。")
    stats = ai_tell_stats(hits)
    assert stats["patterns_hit"] == len(hits)
    assert stats["total"] == sum(hit.count for hit in hits)
    assert stats["fatal"] >= 1
    assert stats["warning"] >= 1
    assert stats["fatal"] + stats["warning"] + stats["info"] == stats["total"]
    assert stats["by_category"]["language"] == stats["fatal"] + 1


def test_stats_of_empty_hits_is_all_zero():
    stats = ai_tell_stats([])
    assert stats["total"] == 0
    assert stats["patterns_hit"] == 0
    assert stats["by_category"] == {}


def test_fatal_hits_sort_before_warnings():
    hits = scan_ai_tells("我们赋能客户。这不是价格战，而是价值战。")
    severities = [hit.severity for hit in hits]
    assert severities[0] == SEVERITY_FATAL


def test_empty_and_none_text_return_no_hits():
    assert scan_ai_tells("") == []
    assert scan_ai_tells(None) == []  # type: ignore[arg-type]


def test_render_for_prompt_covers_every_pattern_exactly_once():
    """prompt 与扫描器共用同一份资产——渲染漏掉哪条，模型就永远不会改哪条。"""
    rules = load_ai_tell_rules()
    lines = render_patterns_for_prompt(rules).splitlines()
    for pattern in rules.patterns:
        matching = [
            line
            for line in lines
            if line.startswith(f"- {pattern.id}：") or line.startswith(f"- {pattern.id}【")
        ]
        assert len(matching) == 1, f"{pattern.id} 在 prompt 里的条目数异常：{matching}"
    assert "【必须消除】" in "\n".join(lines)
    assert "### 内容套路" in lines  # 类别标签也要带上


def test_render_for_prompt_shows_metric_thresholds_for_metric_rules():
    rendered = render_patterns_for_prompt()
    assert "结构指标 em_dash_count ≥ 1" in rendered
