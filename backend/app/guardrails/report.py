"""守门层报告模型。

用 Pydantic 定义，便于直接 ``model_dump()`` 存进 LangGraph state 与数据库
（项目的 ``stages.output`` 是 JSON 文本），前端也能直接按同构类型渲染。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WordHitModel(BaseModel):
    """违禁词命中。``scope`` 区分成稿与输入素材。"""

    scope: str
    category: str
    word: str
    severity: str
    context: str
    suggestion: str
    basis: str = ""


class AiTellHitModel(BaseModel):
    """AI 味命中（确定性可判定的部分）。"""

    pattern_id: str
    category: str
    # 分类的中文可读名（如「语言与句式」），前端直接渲染，不硬编码映射。
    category_label: str = ""
    severity: str
    count: int
    suggestion: str = ""
    samples: list[str] = Field(default_factory=list)


class TitleCheckModel(BaseModel):
    title: str
    length: int
    length_ok: bool
    # 渠道标题达标区间（channels.yaml 的 title_min_chars / title_max_chars），
    # 二者均为 0 表示该渠道未配置长度区间。前端编辑器据此做实时字数提示。
    min_chars: int = 0
    max_chars: int = 0
    dimensions: dict[str, float | None] = Field(default_factory=dict)
    weighted_score: float | None = None
    grade: str = ""
    grade_note: str = ""
    forbidden_hits: list[WordHitModel] = Field(default_factory=list)
    ai_tell_hits: list[AiTellHitModel] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ContractCheckModel(BaseModel):
    """单条内容契约的核验结果（``ok=False`` 即缺口）。

    契约来自 ``channels.yaml`` 的 ``content_contract``，把
    ``copywriting_guide`` 里**可穷举**的那部分变成可核验的要求。
    """

    field: str
    label: str
    kind: str = ""
    ok: bool = True
    detail: str = ""


class VariantReport(BaseModel):
    """单条文案变体的体检报告。"""

    index: int
    body_chars: int
    max_chars: int
    # ``over_limit`` 是**安全断言**：降档保证不超限，所以它正常情况下恒为 False。
    # 一旦为 True，说明降档逻辑出了 bug——保留它是为了不静默失败。
    over_limit: bool
    # 钩子本身长过渠道上限、只能硬裁（信息被切掉），必须交人工
    hard_cut: bool = False
    char_breakdown: dict[str, int] = Field(default_factory=dict)

    downgraded: bool = False
    downgrade_level: int = 0
    downgrade_notes: str = ""
    dropped: list[str] = Field(default_factory=list)

    hashtags_before: list[str] = Field(default_factory=list)
    hashtags_after: list[str] = Field(default_factory=list)
    hashtag_notes: list[str] = Field(default_factory=list)

    emoji_before: int = 0
    emoji_after: int = 0
    emoji_notes: list[str] = Field(default_factory=list)

    word_hits: list[WordHitModel] = Field(default_factory=list)
    ai_tells: list[AiTellHitModel] = Field(default_factory=list)
    title: TitleCheckModel | None = None

    # 内容契约核验（渠道未声明 content_contract 时为空 —— 不核验，不是通过）
    contract: list[ContractCheckModel] = Field(default_factory=list)


class GuardrailReport(BaseModel):
    """整次守门结果。

    ``passed`` 只看**成稿**的硬约束（字数、违禁词、致命 AI 味）；
    ``source_hits`` 是输入素材自带的命中，仅展示，不计入判定。
    """

    channel_id: str
    max_chars: int
    # 渠道话题标签风格（channels.yaml 的 hashtag_style，经缺省推断后的最终值：
    # paired=#话题# / hash=#话题 / none=不使用）。前端编辑器据此做「可发布形态」
    # 实时预览 —— 前端不自己维护「哪个渠道用哪种样式」的映射表。
    hashtag_style: str = ""
    variant_reports: list[VariantReport] = Field(default_factory=list)

    draft_hits: list[WordHitModel] = Field(default_factory=list)
    source_hits: list[WordHitModel] = Field(default_factory=list)

    passed: bool = True
    auto_fixed: list[str] = Field(default_factory=list)
    needs_human: list[str] = Field(default_factory=list)

    # 内容契约缺口汇总（只标记，**不参与 passed 判定** ——
    # passed 表达硬合规，「内容不够好」交给 review 判断）。
    contract_gaps: list[str] = Field(default_factory=list)

    stats: dict[str, Any] = Field(default_factory=dict)
    ai_tell_stats: dict[str, Any] = Field(default_factory=dict)
