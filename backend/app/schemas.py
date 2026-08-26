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


class StageEdit(BaseModel):
    """编辑阶段产出（human-in-the-loop 中间干预）。"""

    output: Optional[Any] = None  # 替换该阶段产出（JSON）
    variants: Optional[list[Any]] = None  # 编辑文案变体（仅 copywriting 阶段）


class ConfirmRequest(BaseModel):
    """提交人工确认：携带编辑后的文案变体。"""

    variants: list[Any] = []


class ChannelOut(BaseModel):
    id: str
    name: str
    desc: str = ""
    tone: str = ""
    max_chars: int = 0
