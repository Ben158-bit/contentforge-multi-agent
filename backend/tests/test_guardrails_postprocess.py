"""守门层编排（normalize → detect → report）的集成测试。

这是 P3 接入节点链时要调用的**唯一入口**，所以这里既验证「能自动修的修对了」，
也验证「只该标记的没被偷偷改写」。
"""
from __future__ import annotations

from app.guardrails.postprocess import (
    VARIANT_FIELDS,
    draft_text,
    normalize_variant,
    run_guardrail,
)


def _channel(**overrides):
    channel = {
        "name": "测试渠道",
        "max_chars": 100,
        "require_hashtags": False,
        "require_emoji": False,
    }
    channel.update(overrides)
    return channel


def _variant(body: str, **overrides):
    variant = {"title": "标题", "body": body, "hashtags": [], "notes": ""}
    variant.update(overrides)
    return variant


# --------------------------------------------------------------------------
# 干净文案：不该被动手
# --------------------------------------------------------------------------
def test_clean_variant_passes_untouched():
    fixed, report = run_guardrail([_variant("这是一段干净的正文。")], _channel())
    assert fixed[0]["body"] == "这是一段干净的正文。"
    assert report.passed is True
    assert report.auto_fixed == []
    assert report.needs_human == []
    assert report.draft_hits == []


def test_originals_are_never_mutated_in_place():
    original = _variant("钩子✨正文", hashtags=["话题"])
    snapshot = {key: list(value) if isinstance(value, list) else value for key, value in original.items()}
    run_guardrail([original], _channel(require_emoji=False, require_hashtags=True))
    assert original == snapshot


def test_unknown_channel_specific_fields_survive_normalisation():
    variant = _variant("正文", hook_type="反常识", selling_points=["甲", "乙"])
    fixed, _ = run_guardrail([variant], _channel())
    assert fixed[0]["hook_type"] == "反常识"
    assert fixed[0]["selling_points"] == ["甲", "乙"]


def test_variant_fields_constant_matches_the_contract():
    assert VARIANT_FIELDS == ("title", "body", "hashtags", "notes")


# --------------------------------------------------------------------------
# 能自动修的：emoji / 标签 / 字数
# --------------------------------------------------------------------------
def test_emoji_stripped_when_channel_does_not_want_it():
    fixed, report = run_guardrail([_variant("钩子✨正文🚀")], _channel(require_emoji=False))
    assert fixed[0]["body"] == "钩子正文"
    assert report.variant_reports[0].emoji_before == 2
    assert report.variant_reports[0].emoji_after == 0
    assert any("emoji" in note for note in report.auto_fixed)


def test_emoji_capped_when_channel_wants_them():
    fixed, report = run_guardrail([_variant("✨一🚀二⭐三🔥四")], _channel(require_emoji=True))
    assert fixed[0]["body"].count("🔥") == 0
    assert report.variant_reports[0].emoji_after == 3


def test_hashtags_normalised_to_paired_style():
    fixed, _ = run_guardrail(
        [_variant("正文", hashtags=["话题", "#带井号"])], _channel(require_hashtags=True)
    )
    assert fixed[0]["hashtags"] == ["#话题#", "#带井号#"]


def test_hashtags_cleared_when_channel_does_not_use_them():
    fixed, report = run_guardrail(
        [_variant("正文", hashtags=["#甲#", "#乙#"])], _channel(require_hashtags=False)
    )
    assert fixed[0]["hashtags"] == []
    assert any("已清空 2 个" in note for note in report.auto_fixed)


def test_long_body_is_downgraded_and_reported():
    body = "钩子句。" + "".join(f"要点{i}：这里是相当长的细节说明文字。".replace("i", str(i)) for i in range(1, 6))
    fixed, report = run_guardrail([_variant(body)], _channel(max_chars=30))
    variant_report = report.variant_reports[0]
    assert variant_report.body_chars <= 30
    assert variant_report.downgraded is True
    assert variant_report.downgrade_level > 0
    assert variant_report.over_limit is False
    assert any("降档 L" in note for note in report.auto_fixed)
    assert len(fixed[0]["body"]) <= 30


def test_dropped_points_are_preserved_for_long_form_reference():
    body = "钩子句。" + "".join(f"要点{i}：这里是相当长的细节说明文字。".replace("i", str(i)) for i in range(1, 6))
    _, report = run_guardrail([_variant(body)], _channel(max_chars=15))
    variant_report = report.variant_reports[0]
    assert variant_report.body_chars <= 15
    assert variant_report.dropped, "被移出正文的要点必须留痕"


def test_body_chars_binds_body_only():
    """标题不计入 max_chars——这是本项目与旧 evals 口径的分歧点。"""
    fixed, report = run_guardrail(
        [_variant("正文正文", title="这是一个很长很长的标题" * 3)],
        _channel(max_chars=100),
    )
    assert len(fixed[0]["title"]) > 10
    assert report.variant_reports[0].body_chars == len("正文正文")


# --------------------------------------------------------------------------
# 只能标记的：违禁词 / AI 味
# --------------------------------------------------------------------------
def test_forbidden_word_is_flagged_but_not_silently_rewritten():
    fixed, report = run_guardrail([_variant("这款产品全网最低")], _channel())
    assert "全网最低" in fixed[0]["body"], "违禁词替换必须交人工，守门层不得擅自改写"
    assert len(report.draft_hits) == 1
    assert report.passed is False
    assert any("违禁词" in note for note in report.needs_human)


def test_user_banned_word_is_enforced():
    _, report = run_guardrail(
        [_variant("本品含有某某成分")], _channel(), user_banned=["某某成分"]
    )
    assert report.draft_hits[0].category == "user_banned"
    assert report.passed is False


def test_notes_field_is_outside_the_published_scan_scope():
    """notes 是模型自述，不对外发布，不该因为里面写了词就判违规。"""
    _, report = run_guardrail(
        [_variant("干净正文", notes="提醒：原稿里有「全网最低」已被我改掉")], _channel()
    )
    assert report.draft_hits == []
    assert report.passed is True


def test_source_texts_are_scanned_separately_and_do_not_fail_the_report():
    """输入素材自带的违禁词不计入成稿违规（对齐内化的微博引擎做法）。"""
    _, report = run_guardrail(
        [_variant("干净正文")], _channel(), source_texts=["素材里写了全网最低"]
    )
    assert len(report.source_hits) == 1
    assert report.source_hits[0].scope == "source"
    assert report.draft_hits == []
    assert report.passed is True


def test_fatal_ai_tell_fails_the_report():
    _, report = run_guardrail([_variant("这不是价格战，而是价值战。")], _channel())
    assert report.ai_tell_stats["fatal"] >= 1
    assert report.passed is False
    assert any("致命 AI 味" in note for note in report.needs_human)


def test_warning_level_ai_tell_does_not_fail_the_report():
    _, report = run_guardrail([_variant("我们要赋能客户。")], _channel())
    assert report.ai_tell_stats["warning"] >= 1
    assert report.ai_tell_stats["fatal"] == 0
    assert report.passed is True


def test_non_fatal_ai_tells_are_still_recorded_per_variant():
    _, report = run_guardrail([_variant("我们要赋能客户。")], _channel())
    assert report.variant_reports[0].ai_tells


# --------------------------------------------------------------------------
# 硬裁：最需要人看的那一类
# --------------------------------------------------------------------------
def test_hard_cut_body_is_flagged_for_human_and_fails_the_report():
    _, report = run_guardrail([_variant("字" * 30)], _channel(max_chars=10))
    variant_report = report.variant_reports[0]
    assert variant_report.hard_cut is True
    assert variant_report.over_limit is False
    assert variant_report.body_chars <= 10
    assert report.passed is False
    assert any("人工" in note for note in report.needs_human)
    assert report.stats["hard_cut"] == 1


def test_channel_without_char_limit_does_no_downgrade():
    body = "字" * 500
    fixed, report = run_guardrail([_variant(body)], _channel(max_chars=0))
    assert fixed[0]["body"] == body
    assert report.variant_reports[0].downgraded is False


# --------------------------------------------------------------------------
# 标题体检接进来了
# --------------------------------------------------------------------------
def test_title_check_uses_channel_title_bounds():
    _, report = run_guardrail(
        [_variant("正文", title="字" * 40)], _channel(title_min_chars=5, title_max_chars=20)
    )
    title = report.variant_reports[0].title
    assert title is not None
    assert title.length == 40
    assert title.length_ok is False
    assert any("标题长度不合规" in note for note in report.needs_human)


def test_title_keywords_are_passed_through():
    _, report = run_guardrail(
        [_variant("正文", title="春季新款连衣裙")], _channel(), keywords=["春季", "连衣裙"]
    )
    assert report.variant_reports[0].title.dimensions["keyword_match"] == 10.0


# --------------------------------------------------------------------------
# 报告本身
# --------------------------------------------------------------------------
def test_empty_or_invalid_variants_cannot_pass():
    _, report = run_guardrail([], _channel())
    assert report.passed is False
    assert report.stats["variants"] == 0

    _, report = run_guardrail(["不是字典", None], _channel())  # type: ignore[list-item]
    assert report.passed is False
    assert report.stats["variants"] == 0


def test_report_carries_channel_id_and_stats():
    _, report = run_guardrail([_variant("钩子✨正文")], _channel(name="微博", require_emoji=False))
    assert report.channel_id == "微博"
    assert report.max_chars == 100
    assert report.stats["variants"] == 1
    assert report.stats["auto_fixed"] >= 1


def test_report_is_json_serialisable_for_state_and_db():
    _, report = run_guardrail(
        [_variant("这款产品全网最低✨", hashtags=["话题"])],
        _channel(require_hashtags=True, title_min_chars=2, title_max_chars=20),
    )
    payload = report.model_dump()
    assert payload["variant_reports"][0]["hard_cut"] is False
    assert payload["variant_reports"][0]["word_hits"][0]["word"] == "全网最低"
    assert "测试渠道" in report.model_dump_json()


def test_draft_text_excludes_notes_but_includes_tags():
    text = draft_text({"title": "标", "body": "文", "hashtags": ["#甲#"], "notes": "说明"})
    assert "标" in text and "文" in text and "#甲#" in text
    assert "说明" not in text


# --------------------------------------------------------------------------
# normalize_variant 直调
# --------------------------------------------------------------------------
def test_normalize_variant_reports_emoji_and_tag_changes():
    outcome = normalize_variant(
        _variant("钩子✨", hashtags=["话题"]), _channel(require_emoji=False, require_hashtags=True)
    )
    assert outcome.variant["body"] == "钩子"
    assert outcome.variant["hashtags"] == ["#话题#"]
    assert outcome.original_body == "钩子✨"
    assert outcome.pre_downgrade_body == "钩子"


def test_normalize_variant_keeps_full_body_before_downgrade():
    body = "钩子句。" + "要点：很长的细节说明文字。" * 5
    outcome = normalize_variant(_variant(body), _channel(max_chars=20))
    assert outcome.pre_downgrade_body == body
    assert len(outcome.variant["body"]) <= 20
    assert outcome.downgrade.full_text


# --------------------------------------------------------------------------
# 报告带出 hashtag_style（前端「可发布形态预览」据此渲染，不自己维护渠道映射）
# --------------------------------------------------------------------------
def test_report_carries_hashtag_style():
    _, report = run_guardrail(
        [_variant("正文", hashtags=["话题"])],
        _channel(hashtag_style="hash", require_hashtags=True),
    )
    assert report.hashtag_style == "hash"


def test_report_hashtag_style_infers_paired_when_missing_and_required():
    _, report = run_guardrail(
        [_variant("正文", hashtags=["话题"])],
        _channel(require_hashtags=True),  # 无显式 hashtag_style → 推断 paired
    )
    assert report.hashtag_style == "paired"


def test_report_hashtag_style_infers_none_when_not_required():
    _, report = run_guardrail(
        [_variant("正文", hashtags=["话题"])],
        _channel(require_hashtags=False),  # 无显式 hashtag_style 且不需要 → none
    )
    assert report.hashtag_style == "none"
