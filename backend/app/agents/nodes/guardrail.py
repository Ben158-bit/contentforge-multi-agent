"""确定性守门节点：把 ``app.guardrails`` 接进流水线。

为什么是**独立节点**而不是塞进 ``review``
------------------------------------------
1. **可单独重试**：``run_guardrail`` 是纯函数，重试零成本零 token；
   混在 review 里会让「模型调用失败」和「规则检出」共用一套重试语义。
2. **报告可单独存档**：报告作为独立阶段产出落库，前端单独展示，
   不与审校的散文混在一起，事后可审计「哪一条是工具判的、哪一条是模型判的」。
3. **让 review 拿到事实**：字数、违禁词命中、AI 味句式由工具核验后
   随提示词一起交给审校，模型不必（也不该）自己去数字数。

它**不是 Agent**：不调用模型、不产生随机性。在流水线定义里类型是
``kind: step``，因此没有 ``prompt_key`` 契约——「确定性步骤」和
「LLM 节点」在配置层面就是两种东西。

分界线（与守门层保持一致）：本节点只自动修「删除信息以外、不改语义」的事
（emoji 策略 / 标签规范化 / 字数降档）；**违禁词与 AI 味一律只标记**，
通过 ``needs_human`` 交给人工，绝不静默改写文案。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ...guardrails import run_guardrail, split_user_words
from ...memory import get_brand_context
from ...templates import get_channel
from ..state import AgentState

logger = logging.getLogger(__name__)

DEFAULT_STATE_KEY = "guardrail"


def _brand_banned_words(brand_id: Any) -> list[str]:
    """读取品牌档案里的禁忌词。无品牌 / 无档案 / 解析不出词都返回空列表。"""
    context = get_brand_context(brand_id)
    if not context:
        return []
    return split_user_words(context.get("taboos"))


def _research_keywords(research: Any) -> list[str]:
    """取调研产出的关键词，用于标题体检的「主题匹配」维度。

    调研输出是模型给的，可能夹着 null / 数字 / 空白，所以逐项清洗而不是
    直接 ``map(str, ...)``（那会把 ``None`` 变成字符串 ``"None"`` 当关键词用）。
    """
    if not isinstance(research, dict):
        return []
    keywords = research.get("keywords")
    if not isinstance(keywords, (list, tuple)):
        return []
    words: list[str] = []
    for item in keywords:
        if item is None:
            continue
        word = str(item).strip()
        if word:
            words.append(word)
    return words


def _source_texts(state: AgentState) -> list[str]:
    """「素材」= 用户给的原始简报。自带违禁词单独归档，与成稿违规分开统计。"""
    inp = state.get("input") or {}
    return [str(inp.get("topic") or ""), str(inp.get("extra_requirements") or "")]


def make_guardrail_node(*, state_key: str = DEFAULT_STATE_KEY) -> Callable:
    """守门节点工厂（确定性，无 LLM 依赖）。

    Args:
        state_key: 报告写入的状态通道，由流水线定义的 ``contract.state_key`` 提供。
    """

    def node(state: AgentState) -> dict:
        inp = state.get("input") or {}
        channel_id = str(inp.get("channel_id") or "")
        channel = get_channel(channel_id)
        variants = [v for v in (state.get("copywriting") or []) if isinstance(v, dict)]

        fixed, report = run_guardrail(
            variants,
            channel,
            channel_id=channel_id,
            source_texts=_source_texts(state),
            user_banned=_brand_banned_words(state.get("brand_id")),
            keywords=_research_keywords(state.get("research")),
            fallback_topic=str(inp.get("topic") or "") or None,
        )
        if not variants:
            # 创作节点本应保证至少一个变体；真出现空集时要说清「没东西可查」，
            # 而不是给出一个 passed=False 却没有任何理由的报告。
            report.needs_human.append("文案创作未产出任何变体，守门层无内容可查")

        logger.info(
            "守门层[%s]完成：%d 个变体，自动修正 %d 处，待人工 %d 处，passed=%s",
            state_key, len(fixed), len(report.auto_fixed), len(report.needs_human),
            report.passed,
        )
        return {
            state_key: report.model_dump(),
            # 归一化后的变体回写：下游 review 与人工确认看到的必须是
            # 守门层实际检查过的那一份，否则报告与交付物对不上。
            "copywriting": fixed,
            "needs_human": list(report.needs_human),
            "stage_status": {state_key: "completed"},
        }

    return node
