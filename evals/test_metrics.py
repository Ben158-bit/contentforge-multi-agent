from __future__ import annotations

from metrics import (
    aggregate,
    score_brand_alignment,
    score_constraints,
    score_schema,
)
from schema import EvalCase


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


def _variant(body: str = "屿暖陪你通勤，支持 24 小时保温。", **overrides) -> dict:
    value = {
        "title": "通勤保温杯",
        "body": body,
        "hashtags": ["#保温杯"],
        "notes": "",
    }
    value.update(overrides)
    return value


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


def test_score_constraints_enforces_channel_max_chars() -> None:
    result = score_constraints(_case(), [_variant("通勤 24 小时保温" + "好" * 300)])
    assert result.passed is False
    assert any("280" in reason for reason in result.reasons)


def test_score_brand_alignment_requires_brand_name() -> None:
    result = score_brand_alignment(_case(), [_variant("适合通勤，支持 24 小时保温。")])
    assert result.passed is False
    assert result.score == 0.0


def test_aggregate_exposes_machine_readable_failures() -> None:
    summary = aggregate([
        score_schema([_variant()]),
        score_constraints(_case(), [_variant()]),
        score_brand_alignment(_case(), [_variant()]),
    ])
    assert summary["passed"] is True
    assert summary["score"] == 1.0
    assert summary["failures"] == []
