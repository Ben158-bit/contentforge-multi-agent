"""LangGraph 图状态定义。

流水线：research(市场调研) → competitor(竞品分析) → strategy(策略制定)
      → copywriting(文案创作) → review(审校) → [不通过则打回创作] → human_confirm(人工确认)
"""
from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict


def _merge_dict(current: dict, update: dict) -> dict:
    """状态 reducer：合并 dict（stage_status 需要跨节点累积）。"""
    return {**(current or {}), **(update or {})}


class TaskInput(TypedDict, total=False):
    """一次营销任务的输入参数。"""

    topic: str
    brand_name: str
    target_audience: str
    channel_id: str
    extra_requirements: str


class AgentState(TypedDict, total=False):
    """跨节点的流水线状态（LangGraph 状态通道）。"""

    # 任务信息
    task_id: int
    input: TaskInput

    # 各阶段产出
    research: dict[str, Any]        # 市场调研
    competitor: dict[str, Any]      # 竞品分析
    strategy: dict[str, Any]        # 策略制定
    copywriting: list[dict[str, Any]]  # 文案变体列表
    review: dict[str, Any]          # 审校结果
    review_feedback: str            # 审校打回时的修改建议
    revision_round: int             # 当前审校迭代轮数

    # 进度与统计
    stage_status: Annotated[dict[str, str], _merge_dict]  # stage_key -> status
    total_cost: float               # 累计 LLM 成本（人民币元）
    total_latency: float            # 累计 LLM 耗时（秒）

    # 人工确认结果（human-in-the-loop）
    confirmed: Optional[dict[str, Any]]
