"""Reproducible ContentForge copywriting evaluation runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.nodes import COST_INPUT_PER_M, COST_OUTPUT_PER_M, _safe_json  # noqa: E402
from app.agents.prompts import build_copywriting_messages  # noqa: E402
from app.llm import LLMResult  # noqa: E402
from app.templates import get_channel  # noqa: E402
from metrics import aggregate, score_brand_alignment, score_constraints, score_schema  # noqa: E402
from schema import EvalCase, load_dataset  # noqa: E402

PROMPT_VERSION = "copywriting-v1"


@dataclass(frozen=True)
class EvaluationFakeProvider:
    """Case-aware deterministic fixture; production FakeLLM remains unchanged."""

    model: str = "fake-eval-v1"

    def chat(self, messages, **kwargs) -> LLMResult:
        text = " ".join(m.get("content", "") for m in messages)

        def field(name: str, default: str) -> str:
            match = re.search(rf"- {name}:\s*([^\n]+)", text)
            return match.group(1).strip() if match else default

        brand = field("brand_name", "ContentForge")
        topic = field("topic", "营销主题")
        core = re.search(r'"core_value_proposition":\s*"([^"]*)"', text)
        required = [x.strip() for x in (core.group(1).split("；") if core else []) if x.strip()]
        if not required:
            required = re.findall(r"[「『]([^」』]+)[」』]", field("extra_requirements", ""))[:1]
        payload = {"variants": [{
            "title": f"{brand}{topic[:12]}",
            "body": f"{brand}围绕{topic}，带来{'、'.join(required) or '优质体验'}，适合真实生活场景。",
            "hashtags": [f"#{brand}"],
            "notes": "deterministic evaluation fixture",
        }]}
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), model=self.model,
                         prompt_tokens=100, completion_tokens=50, latency_seconds=0.001)


def build_provider(name: str):
    if name == "fake":
        return EvaluationFakeProvider()
    if name == "deepseek":
        from app.config import get_llm_client

        return get_llm_client()
    raise ValueError(f"unknown provider: {name}")


def _input_hash(case: EvalCase) -> str:
    raw = json.dumps(case.model_dump(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _error_category(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError)):
        return "schema_error"
    return "llm_error"


def run_case(case: EvalCase, provider, *, provider_name: str) -> dict[str, Any]:
    started = time.monotonic()
    base = {"case_id": case.case_id, "input_hash": _input_hash(case), "provider": provider_name,
            "model": getattr(provider, "model", provider_name), "prompt_version": PROMPT_VERSION}
    try:
        state = {"input": {"topic": case.topic, "brand_name": case.brand_name,
                            "target_audience": case.target_audience, "channel_id": case.channel_id,
                            "extra_requirements": case.extra_requirements},
                 "strategy": {"target_audience": case.target_audience,
                              "core_value_proposition": "；".join(case.hard_constraints),
                              "tone_guidance": case.extra_requirements}}
        response = provider.chat(build_copywriting_messages(state, get_channel(case.channel_id)),
                                 json_mode=True, temperature=0.0)
        payload = _safe_json(response.content)
        variants = payload.get("variants", []) if isinstance(payload, dict) else []
        metrics = [score_schema(variants), score_constraints(case, variants),
                   score_brand_alignment(case, variants)]
        cost = response.prompt_tokens / 1_000_000 * COST_INPUT_PER_M + response.completion_tokens / 1_000_000 * COST_OUTPUT_PER_M
        return {**base, "latency_seconds": response.latency_seconds, "cost": round(cost, 6),
                "variants": variants, "metrics": aggregate(metrics)["metrics"], "error": None}
    except Exception as exc:
        return {**base, "latency_seconds": round(time.monotonic() - started, 3), "cost": 0.0,
                "variants": [], "metrics": {}, "error": {"category": _error_category(exc), "message": str(exc)[:300]}}


def run_dataset(cases: Iterable[EvalCase], provider, provider_name: str) -> dict[str, Any]:
    rows = [run_case(case, provider, provider_name=provider_name) for case in cases]
    successful = [row for row in rows if row["error"] is None]

    def rate(metric: str) -> float:
        return round(sum(row.get("metrics", {}).get(metric, {}).get("passed") is True for row in rows) / len(rows), 4) if rows else 0.0

    return {"metadata": {"provider": provider_name, "prompt_version": PROMPT_VERSION,
                          "generated_at": datetime.now(timezone.utc).isoformat()},
            "summary": {"case_count": len(rows), "success_count": len(successful),
                        "error_count": len(rows) - len(successful), "schema_pass_rate": rate("schema"),
                        "constraint_pass_rate": rate("constraints"), "brand_alignment_rate": rate("brand_alignment"),
                        "average_cost": round(sum(row["cost"] for row in successful) / len(successful), 6) if successful else 0.0,
                        "average_latency_seconds": round(sum(row["latency_seconds"] for row in successful) / len(successful), 3) if successful else 0.0},
            "cases": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--provider", choices=("fake", "deepseek"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)
    result = run_dataset(load_dataset(args.dataset), build_provider(args.provider), args.provider)
    result["metadata"]["seed"] = args.seed
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["summary"]["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
