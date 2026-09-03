from __future__ import annotations

from pathlib import Path

from run_eval import build_provider, run_case, run_dataset
from schema import EvalCase


def _case(case_id: str = "runner-001") -> EvalCase:
    return EvalCase(
        case_id=case_id,
        topic="通勤保温杯",
        brand_name="屿暖",
        target_audience="通勤白领",
        channel_id="xiaohongshu",
        extra_requirements="突出 24 小时保温",
        hard_constraints=["24 小时"],
    )


def test_fake_provider_runs_without_network() -> None:
    result = run_case(_case(), build_provider("fake"), provider_name="fake")
    assert result["case_id"] == "runner-001"
    assert result["provider"] == "fake"
    assert result["input_hash"]
    assert result["error"] is None
    assert result["metrics"]["schema"]["passed"] is True


class FailingProvider:
    model = "failing"

    def chat(self, messages, **kwargs):
        raise TimeoutError("planned timeout")


def test_single_failure_does_not_abort_dataset(tmp_path: Path) -> None:
    results = run_dataset([_case("a"), _case("b")], FailingProvider(), "fake")
    assert len(results["cases"]) == 2
    assert results["cases"][0]["error"]["category"] == "timeout"
    assert results["summary"]["case_count"] == 2
    assert results["summary"]["error_count"] == 2
