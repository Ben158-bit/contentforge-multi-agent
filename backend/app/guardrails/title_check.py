"""标题体检。

内化自小红书标题六维评分卡。原评分卡的「加权汇总 + S/A/B/C 定档」是**纯算术**，
不该交给模型算；依赖实时爆款数据的维度，本模块改用可确定性判定的词表近似，
使体检结果零成本、可复现、可写单测。

配合决策：**不新增标题 Agent 节点**——标题质量通过「渠道 guide 内化（P5）
+ 本模块的确定性体检」解决；体检发现问题只标记，交人工在确认环节定夺。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

from .ai_tells import AiTellHit, scan_ai_tells
from .compliance import WordHit, scan_text

RULES_DIR = Path(__file__).resolve().parent / "rules"
TITLE_RULES_FILE = RULES_DIR / "title_rules.yaml"

GRADE_NA = "N/A(需数据)"


@dataclass(frozen=True)
class TitleRules:
    weights: dict[str, float]
    labels: dict[str, str]
    grades: tuple[tuple[float, str, str], ...]
    keywords: dict[str, tuple[str, ...]]
    separators: tuple[str, ...]
    oversize_tolerance: float
    scoring: dict[str, Any]


@lru_cache
def load_title_rules() -> TitleRules:
    with open(TITLE_RULES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    dimensions = data.get("dimensions") or {}
    weights = {str(k): float(v.get("weight", 0.0)) for k, v in dimensions.items()}
    labels = {str(k): str(v.get("label", k)) for k, v in dimensions.items()}

    grades = tuple(
        sorted(
            (
                (float(item.get("min", 0.0)), str(item.get("grade", "C")), str(item.get("note", "")))
                for item in (data.get("grades") or [])
            ),
            key=lambda item: item[0],
            reverse=True,
        )
    )
    keywords = {
        str(k): tuple(str(w) for w in v)
        for k, v in (data.get("keywords") or {}).items()
    }
    structure = data.get("structure") or {}
    return TitleRules(
        weights=weights,
        labels=labels,
        grades=grades,
        keywords=keywords,
        separators=tuple(str(s) for s in (structure.get("separators") or [])),
        oversize_tolerance=float(structure.get("oversize_tolerance", 1.2)),
        scoring=dict(data.get("scoring") or {}),
    )


def grade_from_score(score: float, *, rules: TitleRules | None = None) -> tuple[str, str]:
    """把加权总分折算成 S/A/B/C 档位。"""
    active = rules or load_title_rules()
    for threshold, grade, note in active.grades:
        if score >= threshold:
            return grade, note
    return "C", ""


def weighted_grade(
    scores: dict[str, float | None],
    *,
    rules: TitleRules | None = None,
    allow_partial: bool = False,
) -> tuple[float | None, str, str]:
    """六维加权汇总并定档。

    Args:
        scores: 维度 → 0-10 分；值为 None 表示该维度缺少数据。
        allow_partial: False 时，任一维度缺数据即返回 ``(None, "N/A(需数据)", ...)``；
            True 时按已有维度重新归一化权重后计分。

    Returns:
        (加权总分 | None, 档位, 档位说明)
    """
    active = rules or load_title_rules()
    known = {dim: value for dim, value in scores.items() if dim in active.weights}
    if not known:
        return None, GRADE_NA, "没有任何可打分的维度"

    missing = [dim for dim in active.weights if dim not in known or known[dim] is None]
    if missing and not allow_partial:
        return None, GRADE_NA, f"缺少维度数据：{'、'.join(active.labels.get(d, d) for d in missing)}"

    total_weight = sum(active.weights[dim] for dim, value in known.items() if value is not None)
    if total_weight <= 0:
        return None, GRADE_NA, "可用维度权重为 0"

    score = sum(
        float(value) * active.weights[dim]
        for dim, value in known.items()
        if value is not None
    ) / total_weight
    score = round(score, 2)
    grade, note = grade_from_score(score, rules=active)
    return score, grade, note


def _keyword_score(text: str, words: Iterable[str], scoring: dict[str, Any]) -> float:
    tiers = scoring.get("keyword_hits") or {}
    none = float(tiers.get("none", 3))
    one = float(tiers.get("one", 7))
    many = float(tiers.get("many", 10))
    hits = sum(1 for word in words if word and word in text)
    if hits >= 2:
        return many
    if hits == 1:
        return one
    return none


@dataclass
class TitleCheck:
    title: str
    length: int
    length_ok: bool
    # 渠道标题达标区间（来自 channels.yaml 的 title_min_chars / title_max_chars）。
    # 回写进报告，前端编辑器据此做实时字数提示——前端不硬编码渠道区间表。
    # 二者均为 0 表示该渠道未配置长度区间（如微博、产品页），跳过长度判定。
    min_chars: int = 0
    max_chars: int = 0
    dimensions: dict[str, float | None] = field(default_factory=dict)
    weighted_score: float | None = None
    grade: str = ""
    grade_note: str = ""
    forbidden_hits: list[WordHit] = field(default_factory=list)
    ai_tell_hits: list[AiTellHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "length": self.length,
            "length_ok": self.length_ok,
            "min_chars": self.min_chars,
            "max_chars": self.max_chars,
            "dimensions": self.dimensions,
            "weighted_score": self.weighted_score,
            "grade": self.grade,
            "grade_note": self.grade_note,
            "forbidden_hits": [hit.to_dict() for hit in self.forbidden_hits],
            "ai_tell_hits": [hit.to_dict() for hit in self.ai_tell_hits],
            "notes": list(self.notes),
        }


def _score_compliance(hits: list[WordHit], scoring: dict[str, Any]) -> float:
    tiers = scoring.get("compliance") or {}
    count = len(hits)
    if count == 0:
        return float(tiers.get("clean", 10))
    if count == 1:
        return float(tiers.get("low", 5))
    if count <= 3:
        return float(tiers.get("medium", 2))
    return float(tiers.get("high", 0))


def _score_structure(
    length: int,
    *,
    min_chars: int,
    max_chars: int,
    rules: TitleRules,
) -> tuple[float, bool, list[str]]:
    notes: list[str] = []
    tiers = rules.scoring.get("length") or {}
    within = float(tiers.get("within", 10))
    tolerable = float(tiers.get("tolerable", 6))
    over = float(tiers.get("over", 3))

    if not max_chars and not min_chars:
        notes.append("渠道未配置标题长度区间，跳过长度合规判定")
        return within, True, notes

    if min_chars and length < min_chars:
        notes.append(f"标题 {length} 字，短于下限 {min_chars} 字")
        return tolerable, False, notes
    if max_chars and length > max_chars:
        if length <= max_chars * rules.oversize_tolerance:
            notes.append(f"标题 {length} 字，略超上限 {max_chars} 字")
            return tolerable, False, notes
        notes.append(f"标题 {length} 字，远超上限 {max_chars} 字")
        return over, False, notes
    return within, True, notes


def check_title(
    title: str,
    *,
    min_chars: int = 0,
    max_chars: int = 0,
    keywords: Iterable[str] | None = None,
    user_banned: Iterable[str] | None = None,
    rules: TitleRules | None = None,
) -> TitleCheck:
    """对单个标题做体检。

    Args:
        title: 标题文本。
        min_chars / max_chars: 渠道标题长度区间；均为 0 时跳过长度判定。
        keywords: 调研节点的关键词（用于「主题匹配」维度）；未提供时该维度记 None。
        user_banned: 品牌自定义禁用词。
    """
    active = rules or load_title_rules()
    text = str(title or "").strip()
    length = len(text)

    if not text:
        return TitleCheck(
            title="",
            length=0,
            length_ok=False,
            min_chars=min_chars,
            max_chars=max_chars,
            dimensions={dim: None for dim in active.weights},
            grade=GRADE_NA,
            grade_note="标题为空",
            notes=["标题为空，无法体检"],
        )

    forbidden = scan_text(text, scope="draft", user_banned=user_banned)
    ai_hits = scan_ai_tells(text)

    structure_score, length_ok, notes = _score_structure(
        length, min_chars=min_chars, max_chars=max_chars, rules=active
    )
    keyword_list = [str(k) for k in (keywords or []) if str(k).strip()]
    keyword_score: float | None
    if keyword_list:
        matched = sum(1 for word in keyword_list if word in text)
        keyword_score = round(min(10.0, matched / len(keyword_list) * 10), 2)
        if matched == 0:
            notes.append("标题未包含任何调研关键词，检索命中会受影响")
    else:
        keyword_score = None
        notes.append("未提供调研关键词，「主题匹配」维度记 N/A")

    dimensions: dict[str, float | None] = {
        "keyword_match": keyword_score,
        "structure": structure_score,
        "benefit": _keyword_score(text, active.keywords.get("benefit", ()), active.scoring),
        "emotion": _keyword_score(text, active.keywords.get("emotion", ()), active.scoring),
        "scarcity": _keyword_score(text, active.keywords.get("scarcity", ()), active.scoring),
        "compliance": _score_compliance(forbidden, active.scoring),
    }

    if any(separator in text for separator in active.separators):
        notes.append("标题含分隔符，结构清晰度较好")

    score, grade, grade_note = weighted_grade(dimensions, rules=active, allow_partial=True)

    return TitleCheck(
        title=text,
        length=length,
        length_ok=length_ok,
        min_chars=min_chars,
        max_chars=max_chars,
        dimensions=dimensions,
        weighted_score=score,
        grade=grade,
        grade_note=grade_note,
        forbidden_hits=forbidden,
        ai_tell_hits=ai_hits,
        notes=notes,
    )
