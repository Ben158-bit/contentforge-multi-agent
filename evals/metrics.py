"""Deterministic metrics for ContentForge evaluation outputs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from schema import EvalCase


CHANNEL_MAX_CHARS = {
    "xiaohongshu": 1200,
    "wechat": 3000,
    "weibo": 280,
    "product_page": 1500,
}


@dataclass(frozen=True)
class MetricResult:
    name: str
    score: float
    passed: bool
    reasons: list[str] = field(default_factory=list)


def _variant_text(variant: dict[str, Any]) -> str:
    hashtags = variant.get("hashtags", [])
    if not isinstance(hashtags, list):
        hashtags = [str(hashtags)]
    parts = [
        str(variant.get("title", "")),
        str(variant.get("body", "")),
        " ".join(str(tag) for tag in hashtags),
        str(variant.get("notes", "")),
    ]
    return "\n".join(part for part in parts if part)


def score_schema(variants: Any) -> MetricResult:
    reasons: list[str] = []
    if not isinstance(variants, list) or not variants:
        return MetricResult("schema", 0.0, False, ["variants must be a non-empty list"])
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict):
            reasons.append(f"variant[{index}] must be an object")
            continue
        for field_name in ("title", "body", "hashtags", "notes"):
            if field_name not in variant:
                reasons.append(f"variant[{index}] missing {field_name}")
        if "title" in variant and not isinstance(variant["title"], str):
            reasons.append(f"variant[{index}].title must be a string")
        if "body" in variant and not isinstance(variant["body"], str):
            reasons.append(f"variant[{index}].body must be a string")
        if "hashtags" in variant and not isinstance(variant["hashtags"], list):
            reasons.append(f"variant[{index}].hashtags must be a list")
        if "notes" in variant and not isinstance(variant["notes"], str):
            reasons.append(f"variant[{index}].notes must be a string")
    return MetricResult("schema", 0.0 if reasons else 1.0, not reasons, reasons)


def score_constraints(case: EvalCase, variants: Any) -> MetricResult:
    schema = score_schema(variants)
    if not schema.passed:
        return MetricResult("constraints", 0.0, False, ["schema invalid", *schema.reasons])

    reasons: list[str] = []
    texts = [_variant_text(variant) for variant in variants]
    combined = "\n".join(texts)
    for term in case.forbidden_terms:
        if term and term in combined:
            reasons.append(f"forbidden term present: {term}")
    for phrase in case.hard_constraints:
        if phrase and phrase not in combined:
            reasons.append(f"required phrase missing: {phrase}")

    max_chars = CHANNEL_MAX_CHARS[case.channel_id]
    for index, text in enumerate(texts):
        if len(text) > max_chars:
            reasons.append(
                f"variant[{index}] exceeds {case.channel_id} limit {max_chars}: {len(text)}"
            )
    return MetricResult("constraints", 0.0 if reasons else 1.0, not reasons, reasons)


def score_brand_alignment(case: EvalCase, variants: Any) -> MetricResult:
    schema = score_schema(variants)
    if not schema.passed:
        return MetricResult("brand_alignment", 0.0, False, ["schema invalid"])
    combined = "\n".join(_variant_text(variant) for variant in variants)
    if case.brand_name in combined:
        return MetricResult("brand_alignment", 1.0, True, [])
    return MetricResult(
        "brand_alignment",
        0.0,
        False,
        [f"brand name missing: {case.brand_name}"],
    )


def aggregate(results: Iterable[MetricResult]) -> dict[str, Any]:
    items = list(results)
    score = sum(item.score for item in items) / len(items) if items else 0.0
    failures = [
        {"metric": item.name, "reasons": item.reasons}
        for item in items
        if not item.passed
    ]
    return {
        "passed": bool(items) and not failures,
        "score": round(score, 4),
        "failures": failures,
        "metrics": {
            item.name: {
                "score": item.score,
                "passed": item.passed,
                "reasons": item.reasons,
            }
            for item in items
        },
    }
