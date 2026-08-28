"""API 请求/响应模型。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""


class TaskCreate(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200, description="营销主题")
    channel_id: str = Field(..., description="目标渠道，见 GET /api/channels")
    brand_name: str = Field("", max_length=100, description="品牌/产品名")
    target_audience: str = Field("", max_length=200, description="目标受众")
    extra_requirements: str = Field("", max_length=500, description="附加要求")
    project_id: Optional[int] = None
    brand_id: Optional[int] = Field(None, description="关联的品牌档案 id（记忆层，可选）")


class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    tone: str = Field("", max_length=200, description="品牌调性")
    core_claims: str = Field("", max_length=500, description="核心卖点/主张")
    audience: str = Field("", max_length=200, description="目标受众")
    taboos: str = Field("", max_length=500, description="禁忌词/表达")
    notes: str = Field("", max_length=500)


class BrandUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    tone: Optional[str] = Field(None, max_length=200)
    core_claims: Optional[str] = Field(None, max_length=500)
    audience: Optional[str] = Field(None, max_length=200)
    taboos: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=500)


class StageEdit(BaseModel):
    """编辑阶段产出（human-in-the-loop 中间干预）。"""

    output: Optional[Any] = None  # 替换该阶段产出（JSON）
    variants: Optional[list[Any]] = None  # 编辑文案变体（仅 copywriting 阶段）


class ConfirmRequest(BaseModel):
    """提交人工确认：携带编辑后的文案变体，可选勾选纠错学习。"""

    variants: list[Any] = []
    learn: bool = Field(False, description="是否把本次修改记入品牌偏好（纠错学习）")
    brand_id: Optional[int] = Field(None, description="学习的品牌档案 id")


class ChannelOut(BaseModel):
    id: str
    name: str
    desc: str = ""
    tone: str = ""
    max_chars: int = 0


# ===================== 效果闭环 Feedback =====================

class FeedbackIn(BaseModel):
    views: int = Field(0, ge=0)
    conversions: int = Field(0, ge=0)
    score: float = Field(0, ge=0, le=5)
    note: str = Field("", max_length=500)


class FeedbackOut(BaseModel):
    content_id: int
    views: int
    conversions: int
    score: float
    is_simulated: int
    note: str
    created_at: str


class FeedbackRuleOut(BaseModel):
    id: int
    rule_text: str
    strength: float
    created_at: str
