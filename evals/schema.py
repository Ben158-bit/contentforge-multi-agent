"""评测样本（EvalCase）定义、加载与校验。

评测口径：对每条样本执行一次「文案创作」调用（复用生产 build_copywriting_messages
消息构建路径），按 case 内容 + 渠道模板生成 2-3 个文案变体，再交由 metrics.py
确定性评分。

约定（保证评分器可自动判定）：
- forbidden_terms:  生成文本（title/body/hashtags/notes 合并文本）中不得出现的字面词。
- hard_constraints: 生成文本中必须出现的字面短语（至少一个变体命中即可）。
  示例："24 小时保温"、"30 天无理由退换"。
- 所有品牌/公司/数据均为虚构，禁止真实客户与真实品牌数据。

本模块为纯 Python + pydantic，不依赖 backend 包，便于独立单测。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field, field_validator

# 与 backend/app/templates/channels.yaml 的渠道 id 保持一致（test_dataset 会做同步断言）
VALID_CHANNEL_IDS: tuple[str, ...] = ("xiaohongshu", "wechat", "weibo", "product_page")

# 疑似密钥/敏感信息扫描：评测样本禁止携带任何真实密钥或占位符
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", re.IGNORECASE),
)

_DATASET_FILE = Path(__file__).resolve().parent / "dataset.jsonl"


class EvalCase(BaseModel):
    """一条营销文案生成评测样本（全部字段为虚构数据）。"""

    case_id: str = Field(min_length=1, description="样本唯一 id，如 xhs-001")
    topic: str = Field(min_length=1, description="营销主题")
    brand_name: str = Field(min_length=1, description="虚构品牌名")
    target_audience: str = Field(min_length=1, description="目标受众描述")
    channel_id: str = Field(description="渠道 id，必须是 VALID_CHANNEL_IDS 之一")
    extra_requirements: str = Field(
        default="", description="额外的自由文本要求（语气/结构/CTA 等）"
    )
    forbidden_terms: list[str] = Field(
        default_factory=list, description="生成文本中不得出现的字面词"
    )
    hard_constraints: list[str] = Field(
        default_factory=list,
        description="生成文本中必须出现的字面短语（任一变体命中即可）",
    )
    reference_notes: str = Field(default="", description="评分/人工复核备注")

    @field_validator("channel_id")
    @classmethod
    def _check_channel(cls, v: str) -> str:
        if v not in VALID_CHANNEL_IDS:
            raise ValueError(
                f"未知渠道 {v!r}，可选: {', '.join(VALID_CHANNEL_IDS)}"
            )
        return v

    def text_blob(self) -> str:
        """case 全部文本字段拼接，用于疑似密钥扫描。"""
        parts = [
            self.case_id,
            self.topic,
            self.brand_name,
            self.target_audience,
            self.extra_requirements,
            self.reference_notes,
            *self.forbidden_terms,
            *self.hard_constraints,
        ]
        return "\n".join(p for p in parts if p)


def load_dataset(path: str | Path | None = None) -> list[EvalCase]:
    """加载并校验评测样本集。

    Args:
        path: jsonl 文件路径；None 时使用本模块旁的 dataset.jsonl。

    Returns:
        校验通过的 EvalCase 列表（顺序与文件一致）。

    Raises:
        FileNotFoundError / ValueError: 文件不存在或样本违反校验规则。
    """
    jsonl_path = Path(path) if path is not None else _DATASET_FILE
    if not jsonl_path.exists():
        raise FileNotFoundError(f"评测数据集不存在: {jsonl_path}")

    cases: list[EvalCase] = []
    for line_no, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{jsonl_path}:{line_no} JSON 解析失败: {exc}") from exc
        try:
            case = EvalCase.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"{jsonl_path}:{line_no} 样本校验失败: {exc}") from exc
        _check_no_secret(case, f"{jsonl_path}:{line_no}")
        cases.append(case)

    _validate_dataset(cases, str(jsonl_path))
    return cases


def _check_no_secret(case: EvalCase, where: str) -> None:
    blob = case.text_blob()
    for pattern in _SECRET_PATTERNS:
        if pattern.search(blob):
            raise ValueError(f"{where} 样本 {case.case_id} 疑似包含密钥/敏感信息，禁止入库")


def _validate_dataset(cases: list[EvalCase], where: str) -> None:
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        dup = sorted({cid for cid in ids if ids.count(cid) > 1})
        raise ValueError(f"{where} case_id 重复: {dup}")
    if not cases:
        raise ValueError(f"{where} 数据集为空")


def dataset_summary(cases: Iterable[EvalCase]) -> dict[str, Any]:
    """样本分布的统计摘要（测试与报告用）。"""
    case_list = list(cases)
    by_channel: dict[str, int] = {}
    for c in case_list:
        by_channel[c.channel_id] = by_channel.get(c.channel_id, 0) + 1
    return {
        "total": len(case_list),
        "by_channel": by_channel,
        "with_forbidden": sum(1 for c in case_list if c.forbidden_terms),
        "with_hard_constraints": sum(1 for c in case_list if c.hard_constraints),
        "with_extra_requirements": sum(1 for c in case_list if c.extra_requirements),
    }
