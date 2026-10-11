"""中文 AI 味确定性预扫描。

只做**确定性可判定**的部分：词表命中、正则句式、结构指标。
需要语义理解的改写交给 ``humanize`` Agent 节点。

设计要点：本模块与 humanize 节点的 prompt **共用同一份规则资产**
（``rules/ai_tells_zh.yaml``），避免「守门层扫的词」与「模型被告知要改的词」
两处漂移。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from .emoji_policy import is_emoji

RULES_DIR = Path(__file__).resolve().parent / "rules"
AI_TELLS_FILE = RULES_DIR / "ai_tells_zh.yaml"

SEVERITY_FATAL = "fatal"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"
SEVERITY_ORDER = {SEVERITY_FATAL: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

MAX_SAMPLES = 3
SAMPLE_PAD = 8

_CJK = r"\u4e00-\u9fff"
_CLAUSE_SPLIT = re.compile(r"[。！？；!?;\n]")
_SENTENCE_SPLIT = re.compile(r"[。！？!?…]+")
_PARA_SPLIT = re.compile(r"\n\s*\n|\n")
_BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*")
_BOLD_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、])\s*\*\*[^*\n]+\*\*\s*[:：]", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s*(.+)$", re.MULTILINE)
_HALFWIDTH_RE = re.compile(rf"[{_CJK}][,\.!?;:]|[,\.!?;:][{_CJK}]")
_EM_DASH_RE = re.compile(r"——")


# --------------------------------------------------------------------------
# 结构指标注册表（metric 规则由这里计算）
# --------------------------------------------------------------------------
def _metric_em_dash_count(text: str) -> int:
    return len(_EM_DASH_RE.findall(text))


def _metric_max_sentences_per_para(text: str) -> int:
    longest = 0
    for para in _PARA_SPLIT.split(text):
        if not para.strip():
            continue
        sentences = [chunk for chunk in _SENTENCE_SPLIT.split(para) if chunk.strip()]
        longest = max(longest, len(sentences))
    return longest


def _metric_parallel_triple(text: str) -> int:
    """三段式堆砌：同一分句里出现 ≥2 个顿号（即 A、B、C 三连）记一次。"""
    return sum(1 for clause in _CLAUSE_SPLIT.split(text) if clause.count("、") >= 2)


def _metric_bold_density(text: str) -> int:
    return len(_BOLD_RE.findall(text))


def _metric_bold_list_density(text: str) -> int:
    return len(_BOLD_LIST_RE.findall(text))


def _metric_emoji_in_heading(text: str) -> int:
    total = 0
    for heading in _HEADING_RE.findall(text):
        total += sum(1 for ch in heading if is_emoji(ch))
    return total


def _metric_halfwidth_punct(text: str) -> int:
    """中文语境里混入的半角标点（英文规范里的「弯引号」规则不适用中文，改此规则）。"""
    return len(_HALFWIDTH_RE.findall(text))


METRICS: dict[str, Callable[[str], int]] = {
    "em_dash_count": _metric_em_dash_count,
    "max_sentences_per_para": _metric_max_sentences_per_para,
    "parallel_triple": _metric_parallel_triple,
    "bold_density": _metric_bold_density,
    "bold_list_density": _metric_bold_list_density,
    "emoji_in_heading": _metric_emoji_in_heading,
    "halfwidth_punct": _metric_halfwidth_punct,
}


# --------------------------------------------------------------------------
# 规则加载
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AiTellPattern:
    id: str
    category: str
    kind: str  # keyword | regex | metric
    severity: str
    suggestion: str
    words: tuple[str, ...] = ()
    pattern: re.Pattern[str] | None = None
    metric: str = ""
    threshold: int = 1
    label: str = ""


@dataclass(frozen=True)
class AiTellRules:
    patterns: tuple[AiTellPattern, ...]
    category_labels: dict[str, str] = field(default_factory=dict)

    def fatal_ids(self) -> tuple[str, ...]:
        return tuple(p.id for p in self.patterns if p.severity == SEVERITY_FATAL)


@lru_cache
def load_ai_tell_rules() -> AiTellRules:
    with open(AI_TELLS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    categories = data.get("categories") or {}
    labels = {
        str(key): str(value.get("label", key)) if isinstance(value, dict) else str(key)
        for key, value in categories.items()
    }
    default_severity = {
        str(key): (str(value.get("severity", SEVERITY_WARNING)) if isinstance(value, dict) else SEVERITY_WARNING)
        for key, value in categories.items()
    }

    patterns: list[AiTellPattern] = []
    for item in data.get("patterns") or []:
        if not isinstance(item, dict):
            continue
        pattern_id = str(item.get("id") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not pattern_id or kind not in ("keyword", "regex", "metric"):
            continue
        category = str(item.get("category") or "language")

        compiled: re.Pattern[str] | None = None
        if kind == "regex":
            raw = str(item.get("pattern") or "")
            if not raw:
                continue
            compiled = re.compile(raw)

        words = tuple(str(w) for w in (item.get("words") or []) if str(w).strip())
        metric = str(item.get("metric") or "")
        if kind == "keyword" and not words:
            continue
        if kind == "metric":
            if metric not in METRICS:
                continue

        patterns.append(
            AiTellPattern(
                id=pattern_id,
                category=category,
                kind=kind,
                severity=str(item.get("severity") or default_severity.get(category, SEVERITY_WARNING)),
                suggestion=str(item.get("suggestion") or ""),
                words=words,
                pattern=compiled,
                metric=metric,
                threshold=int(item.get("threshold", 1) or 1),
                label=str(item.get("label") or ""),
            )
        )

    if not patterns:
        raise RuntimeError(f"AI 味规则为空: {AI_TELLS_FILE}")
    return AiTellRules(patterns=tuple(patterns), category_labels=labels)


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------
@dataclass
class AiTellHit:
    pattern_id: str
    category: str
    severity: str
    count: int
    suggestion: str
    samples: list[str] = field(default_factory=list)
    label: str = ""
    # 分类的中文可读名（如「语言与句式」），从 rules.category_labels 取，
    # 让前端直接渲染可读分类，不必自己维护 category id → 中文 的映射。
    category_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "category": self.category,
            "category_label": self.category_label,
            "severity": self.severity,
            "count": self.count,
            "suggestion": self.suggestion,
            "samples": list(self.samples),
        }


def _sample(text: str, start: int, end: int) -> str:
    return text[max(0, start - SAMPLE_PAD) : end + SAMPLE_PAD].replace("\n", " ")


def scan_ai_tells(
    text: str,
    *,
    rules: AiTellRules | None = None,
    severities: tuple[str, ...] | None = None,
) -> list[AiTellHit]:
    """扫描一段文本的 AI 味命中。

    Args:
        text: 待扫描文本。
        rules: 规则集；默认加载内置资产。
        severities: 只返回指定级别（如只看 fatal），None 表示全部。
    """
    if not text:
        return []

    active = rules or load_ai_tell_rules()
    hits: list[AiTellHit] = []

    for pattern in active.patterns:
        if severities is not None and pattern.severity not in severities:
            continue

        count = 0
        samples: list[str] = []

        if pattern.kind == "keyword":
            for word in pattern.words:
                start = 0
                while True:
                    index = text.find(word, start)
                    if index < 0:
                        break
                    count += 1
                    if len(samples) < MAX_SAMPLES:
                        samples.append(_sample(text, index, index + len(word)))
                    start = index + len(word)
        elif pattern.kind == "regex" and pattern.pattern is not None:
            for match in pattern.pattern.finditer(text):
                count += 1
                if len(samples) < MAX_SAMPLES:
                    samples.append(_sample(text, match.start(), match.end()))
        elif pattern.kind == "metric":
            value = METRICS[pattern.metric](text)
            if value >= pattern.threshold:
                count = value
                samples.append(f"{pattern.metric} = {value}（阈值 {pattern.threshold}）")

        if count > 0:
            hits.append(
                AiTellHit(
                    pattern_id=pattern.id,
                    category=pattern.category,
                    severity=pattern.severity,
                    count=count,
                    suggestion=pattern.suggestion,
                    samples=samples,
                    label=pattern.label,
                    category_label=active.category_labels.get(pattern.category, pattern.category),
                )
            )

    hits.sort(key=lambda hit: (SEVERITY_ORDER.get(hit.severity, 9), -hit.count))
    return hits


def ai_tell_stats(hits: list[AiTellHit]) -> dict[str, Any]:
    """聚合统计。``total`` 为命中**次数**之和，用于衡量改写效果。"""
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    total = 0
    for hit in hits:
        total += hit.count
        by_severity[hit.severity] = by_severity.get(hit.severity, 0) + hit.count
        by_category[hit.category] = by_category.get(hit.category, 0) + hit.count
    return {
        "total": total,
        "patterns_hit": len(hits),
        "fatal": by_severity.get(SEVERITY_FATAL, 0),
        "warning": by_severity.get(SEVERITY_WARNING, 0),
        "info": by_severity.get(SEVERITY_INFO, 0),
        "by_category": by_category,
        "by_severity": by_severity,
    }


def render_patterns_for_prompt(rules: AiTellRules | None = None) -> str:
    """把规则资产渲染成 humanize 节点 prompt 用的文本（单一真源，避免漂移）。"""
    active = rules or load_ai_tell_rules()
    lines: list[str] = []
    current_category = ""
    for pattern in active.patterns:
        if pattern.category != current_category:
            current_category = pattern.category
            label = active.category_labels.get(current_category, current_category)
            lines.append(f"\n### {label}")
        if pattern.kind == "keyword":
            detail = "、".join(pattern.words)
        elif pattern.kind == "regex":
            detail = f"句式匹配 /{pattern.pattern.pattern}/"
        else:
            detail = f"结构指标 {pattern.metric} ≥ {pattern.threshold}"
        marker = "【必须消除】" if pattern.severity == SEVERITY_FATAL else ""
        lines.append(f"- {pattern.id}{marker}：{detail} → {pattern.suggestion}")
    return "\n".join(lines).strip()
