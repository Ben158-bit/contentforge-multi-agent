from __future__ import annotations

import json
from pathlib import Path

import pytest

from check_regression import check_result


def _result(**summary):
    defaults = {
        "case_count": 30,
        "success_count": 30,
        "error_count": 0,
        "schema_pass_rate": 1.0,
        "constraint_pass_rate": 1.0,
        "brand_alignment_rate": 1.0,
        "average_cost": 0.0006,
        "average_latency_seconds": 0.001,
    }
    defaults.update(summary)
    return {"summary": defaults}


def test_regression_passes_at_threshold(tmp_path: Path) -> None:
    baseline = _result()
    candidate = _result()
    assert check_result(candidate, baseline, {
        "schema_pass_rate": 0.98,
        "constraint_pass_rate": 0.90,
        "cost_multiplier": 1.5,
        "latency_multiplier": 2.0,
    }) == []


def test_regression_reports_named_failures() -> None:
    failures = check_result(
        _result(schema_pass_rate=0.7, average_cost=0.002),
        _result(),
        {"schema_pass_rate": 0.98, "constraint_pass_rate": 0.90,
         "cost_multiplier": 1.5, "latency_multiplier": 2.0},
    )
    assert any(item["metric"] == "schema_pass_rate" for item in failures)
    assert any(item["metric"] == "average_cost" for item in failures)
