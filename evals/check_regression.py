"""Compare an evaluation result with a checked-in baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def check_result(candidate: dict[str, Any], baseline: dict[str, Any], thresholds: dict[str, float]) -> list[dict[str, Any]]:
    current = candidate["summary"]
    base = baseline["summary"]
    checks = [
        ("schema_pass_rate", current.get("schema_pass_rate", 0), ">=", thresholds["schema_pass_rate"]),
        ("constraint_pass_rate", current.get("constraint_pass_rate", 0), ">=", thresholds["constraint_pass_rate"]),
        ("average_cost", current.get("average_cost", 0), "<=", base.get("average_cost", 0) * thresholds["cost_multiplier"]),
        ("average_latency_seconds", current.get("average_latency_seconds", 0), "<=", base.get("average_latency_seconds", 0) * thresholds["latency_multiplier"]),
    ]
    failures = []
    for metric, actual, operator, expected in checks:
        passed = actual >= expected if operator == ">=" else actual <= expected
        if not passed:
            failures.append({"metric": metric, "actual": actual, "operator": operator, "expected": round(expected, 6)})
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--thresholds", required=True)
    args = parser.parse_args()
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    thresholds = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))
    failures = check_result(candidate, baseline, thresholds)
    if failures:
        print(json.dumps({"passed": False, "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"passed": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
