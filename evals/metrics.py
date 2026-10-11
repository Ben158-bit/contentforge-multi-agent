"""Deterministic metrics for ContentForge evaluation outputs.

口径单一真源
------------
评测必须用**和生产同一套口径**，否则分数只说明「评测脚本自己是怎么想的」，
换个判法就能换个结论。本模块因此不自带任何渠道参数：

- **字数** —— ``app.guardrails.text_metrics.count_variant()``，计数口径
  （``char_count_mode``）同样取自渠道配置；
- **渠道上限** —— ``app.templates.get_channel()`` 读 ``backend/app/templates/channels.yaml``
  的 ``max_chars``；
- **计分范围** —— ``app.guardrails.postprocess.draft_text()``（标题 + 正文 + 话题标签）。

两条边界（P5 修正）
------------------
1. **只有 ``body`` 受 ``max_chars`` 约束**。``channels.yaml`` 里 ``max_chars`` 一直是
   正文语义（小红书 1200 字对应 guide 的「300-600 字四段式」），生产守门层也只判 body。
   旧实现把 ``title + body + hashtags + notes`` 合起来比上限，等于用一把不同的尺子
   量同一份文案，评测分数与生产判定的关系是说不清的。
2. **``notes`` 不参与评分**。它是模型自述、不对外发布；把它算进字数或违禁词扫描，
   会让「模型在 notes 里解释自己为什么避开了『最好』」被判成命中违禁词
   （``test_metrics.py`` 里有专门用例钉住这条）。

本模块除评测所需的生产口径外，不导入 fastapi / langgraph / openai，可独立单测。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from schema import EvalCase

# 评测脚本要复用生产口径，就得能 import backend 包。生产侧是 `backend/` 下的包
# （`backend/pytest.ini` 以 backend 为 rootdir），所以这里显式把它挂上 sys.path，
# 与 `run_eval.py` 同一套做法。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.guardrails import count_variant, draft_text, mode_from_channel  # noqa: E402
from app.templates import get_channel  # noqa: E402


@dataclass(frozen=True)
class MetricResult:
    name: str
    score: float
    passed: bool
    reasons: list[str] = field(default_factory=list)


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


def channel_of(case: EvalCase) -> dict:
    """取样本对应渠道的权威配置（与生产同源）。

    未知渠道会在这里抛 ``KeyError``，而不是悄悄退回一个内置默认值 ——
    「评测跑在一个不存在的渠道上」必须响亮地失败。
    """
    return get_channel(case.channel_id)


def score_constraints(case: EvalCase, variants: Any) -> MetricResult:
    schema = score_schema(variants)
    if not schema.passed:
        return MetricResult("constraints", 0.0, False, ["schema invalid", *schema.reasons])

    channel = channel_of(case)
    max_chars = int(channel.get("max_chars") or 0)
    mode = mode_from_channel(channel)

    reasons: list[str] = []
    texts = [draft_text(variant) for variant in variants]
    combined = "\n".join(texts)
    for term in case.forbidden_terms:
        if term and term in combined:
            reasons.append(f"forbidden term present: {term}")
    for phrase in case.hard_constraints:
        if phrase and phrase not in combined:
            reasons.append(f"required phrase missing: {phrase}")

    for index, variant in enumerate(variants):
        body_chars = count_variant(variant, mode)["body"]
        if max_chars and body_chars > max_chars:
            reasons.append(
                f"variant[{index}] body exceeds {case.channel_id} limit {max_chars}: {body_chars}"
            )
    return MetricResult("constraints", 0.0 if reasons else 1.0, not reasons, reasons)


def score_brand_alignment(case: EvalCase, variants: Any) -> MetricResult:
    schema = score_schema(variants)
    if not schema.passed:
        return MetricResult("brand_alignment", 0.0, False, ["schema invalid"])
    combined = "\n".join(draft_text(variant) for variant in variants)
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
