"""评测指标的口径测试。

这个文件守的不是「分数算得对不对」，而是**评测和生产是不是同一把尺子**：
P5 之前 `metrics.py` 自带一张渠道上限表、把 `title+body+hashtags+notes`
合起来量，与 `channels.yaml` 的正文语义、与守门层的判定都不是一回事。
口径漂移不会让任何测试变红，只会让分数失去意义 —— 所以要用用例把它钉住。
"""
from __future__ import annotations

import pytest

import metrics
from metrics import (
    aggregate,
    score_brand_alignment,
    score_constraints,
    score_schema,
)
from schema import EvalCase

WEIBO_LIMIT = 280

# 干净正文：含品牌名、两个硬约束，不含任何禁用词
CLEAN_BODY = "屿暖保温杯通勤随身带，支持 24 小时保温。"


def _case(**overrides) -> EvalCase:
    data = {
        "case_id": "metric-001",
        "topic": "便携保温杯",
        "brand_name": "屿暖",
        "target_audience": "通勤白领",
        "channel_id": "weibo",
        "extra_requirements": "不超过 280 字",
        "forbidden_terms": ["全网第一"],
        "hard_constraints": ["24 小时保温", "通勤"],
        "reference_notes": "",
    }
    data.update(overrides)
    return EvalCase(**data)


def _variant(body: str = CLEAN_BODY, **overrides) -> dict:
    value = {
        "title": "通勤保温杯",
        "body": body,
        "hashtags": ["#保温杯"],
        "notes": "",
    }
    value.update(overrides)
    return value


# ==========================================================================
# 一、schema 与聚合（原有行为）
# ==========================================================================
def test_score_schema_accepts_valid_variants() -> None:
    result = score_schema([_variant()])
    assert result.score == 1.0
    assert result.passed is True
    assert result.reasons == []


def test_score_schema_reports_missing_required_field() -> None:
    result = score_schema([{"title": "只有标题", "hashtags": []}])
    assert result.passed is False
    assert "body" in result.reasons[0]


def test_score_constraints_detects_forbidden_term_and_missing_phrase() -> None:
    result = score_constraints(_case(), [_variant("全网第一的保温杯，适合通勤。")])
    assert result.passed is False
    assert any("全网第一" in reason for reason in result.reasons)
    assert any("24 小时保温" in reason for reason in result.reasons)


def test_aggregate_exposes_machine_readable_failures() -> None:
    summary = aggregate([
        score_schema([_variant()]),
        score_constraints(_case(), [_variant()]),
        score_brand_alignment(_case(), [_variant()]),
    ])
    assert summary["passed"] is True
    assert summary["score"] == 1.0
    assert summary["failures"] == []


# ==========================================================================
# 二、字数口径：渠道上限只约束 body
# ==========================================================================
def test_body_limit_comes_from_channels_yaml_not_a_local_table() -> None:
    """回归：metrics.py 曾自带 `CHANNEL_MAX_CHARS`，与 channels.yaml 各写一份。"""
    assert not hasattr(metrics, "CHANNEL_MAX_CHARS"), (
        "评测侧不允许再出现第二套渠道上限表，必须从 channels.yaml 取"
    )
    assert metrics.channel_of(_case())["max_chars"] == WEIBO_LIMIT


def test_only_the_body_counts_toward_the_channel_limit() -> None:
    """标题 / 标签 / notes 再长都不算超限 —— `max_chars` 是正文语义。"""
    heavy = _variant(
        CLEAN_BODY,
        title="标" * 400,
        hashtags=["#" + "签" * 400],
        notes="注" * 400,
    )
    result = score_constraints(_case(), [heavy])

    assert result.passed is True, result.reasons


def test_body_over_the_yaml_limit_is_reported_with_the_body_length() -> None:
    body = CLEAN_BODY + "好" * WEIBO_LIMIT
    result = score_constraints(_case(), [_variant(body)])

    assert result.passed is False
    assert any(
        "body exceeds" in reason and str(WEIBO_LIMIT) in reason
        for reason in result.reasons
    ), result.reasons


def test_the_limit_differs_per_channel_because_it_is_read_from_yaml() -> None:
    """同一个超长正文：微博超限、公众号不超限 —— 上限确实随渠道变。"""
    body = CLEAN_BODY + "好" * (WEIBO_LIMIT + 20)

    assert score_constraints(_case(), [_variant(body)]).passed is False
    wechat = _case(channel_id="wechat")
    assert score_constraints(wechat, [_variant(body)]).passed is True


def test_count_mode_is_taken_from_the_channel(monkeypatch) -> None:
    """计数字径也走渠道配置：换口径不该改评测代码。"""
    channel = dict(metrics.channel_of(_case()))
    channel["max_chars"] = 9

    body = "通勤 24 小时保温"  # raw 10 字，去空白 8 字
    assert len(body) == 10 and len(body.replace(" ", "")) == 8

    channel["char_count_mode"] = "raw"
    monkeypatch.setattr(metrics, "get_channel", lambda _cid: channel)
    assert score_constraints(_case(), [_variant(body)]).passed is False

    channel["char_count_mode"] = "no_whitespace"
    assert score_constraints(_case(), [_variant(body)]).passed is True


# ==========================================================================
# 三、计分范围：notes 不参与
# ==========================================================================
def test_notes_are_not_scanned_for_forbidden_terms() -> None:
    """notes 是模型自述、不对外发布：在里面解释用词不该被判违规。"""
    variant = _variant(CLEAN_BODY, notes="说明：本篇刻意避开了「全网第一」这类说法")
    result = score_constraints(_case(), [variant])

    assert result.passed is True, result.reasons


def test_notes_do_not_satisfy_hard_constraints() -> None:
    """反过来：硬约束只写在 notes 里不算命中，它必须出现在成稿文本里。"""
    variant = _variant(
        "屿暖保温杯很好用。", title="保温杯测评", notes="已包含 24 小时保温 与 通勤"
    )
    result = score_constraints(_case(), [variant])

    assert result.passed is False
    assert sum("required phrase missing" in reason for reason in result.reasons) == 2


def test_brand_name_in_notes_only_does_not_count_as_alignment() -> None:
    variant = _variant("这款保温杯通勤随身带，支持 24 小时保温。", notes="品牌：屿暖")
    assert score_brand_alignment(_case(), [variant]).passed is False


def test_tags_and_title_still_count_for_forbidden_terms() -> None:
    """排除了 notes，而不是把范围缩到只剩 body —— 标签里的违规同样要抓。"""
    variant = _variant(CLEAN_BODY, hashtags=["#全网第一#"])
    assert score_constraints(_case(), [variant]).passed is False


# ==========================================================================
# 四、与生产守门层同源
# ==========================================================================
def test_metrics_and_production_guardrail_use_the_same_limit() -> None:
    """评测判的和生产判的必须是同一个数 —— 这是整个 P5.2 的目的。"""
    from app.guardrails import run_guardrail

    case = _case()
    channel = metrics.channel_of(case)
    _, report = run_guardrail(
        [_variant()], channel, channel_id=case.channel_id
    )

    assert report.max_chars == channel["max_chars"] == WEIBO_LIMIT


def test_unknown_channel_fails_loudly_instead_of_falling_back() -> None:
    """渠道不存在时报错，而不是悄悄退回一个内置默认上限。"""
    bogus = _case().model_copy(update={"channel_id": "douyin"})
    with pytest.raises(KeyError):
        metrics.channel_of(bogus)
