"""Agent 节点实现。

5 个 agent 节点 + 1 个人工确认节点，全部通过依赖注入 LLM 客户端
（便于测试时注入 fake LLM）。节点返回 dict，由 LangGraph 合并进状态。

注意：节点均为同步函数——langgraph 0.6.x 在 Python 3.10 下，
interrupt 的 config contextvar 只在同步执行路径中被设置。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from langgraph.types import interrupt

from ..llm import LLMClient, LLMResult
from ..memory import get_brand_context
from ..templates import get_channel
from .prompts import (
    build_competitor_messages,
    build_copywriting_messages,
    build_research_messages,
    build_review_messages,
    build_strategy_messages,
)
from .state import AgentState

logger = logging.getLogger(__name__)

# DeepSeek deepseek-chat 定价（人民币 元/百万 tokens，2025 年基准，用于成本统计）
COST_INPUT_PER_M = 2.0
COST_OUTPUT_PER_M = 8.0

# 阶段展示顺序（前端流水线可视化）
STAGE_KEYS = ["research", "competitor", "strategy", "copywriting", "review"]
STAGE_LABELS = {
    "research": "市场调研",
    "competitor": "竞品分析",
    "strategy": "策略制定",
    "copywriting": "文案创作",
    "review": "审校优化",
}


def _safe_json(text: str) -> Any:
    """容错解析 LLM 的 JSON 输出：先整体解析，失败则提取首个 {…} 或 […] 块。

    返回类型可能是 dict 或 list（调用方按各自节点 schema 兜底）。
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 对象优先，其次数组
        for start_ch, end_ch in (("{", "}"), ("[", "]")):
            start, end = text.find(start_ch), text.rfind(end_ch)
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise ValueError(f"LLM 输出中未找到 JSON: {text[:200]}")


def _accumulate(state: AgentState, result: LLMResult) -> dict:
    """将一次 LLM 调用的成本与耗时累加进状态。"""
    cost = result.prompt_tokens / 1_000_000 * COST_INPUT_PER_M + \
        result.completion_tokens / 1_000_000 * COST_OUTPUT_PER_M
    return {
        "total_cost": state.get("total_cost", 0.0) + round(cost, 6),
        "total_latency": state.get("total_latency", 0.0) + result.latency_seconds,
    }


def _stage_node(
    stage_key: str,
    messages_fn: Callable[[AgentState], list[dict[str, str]]],
    llm: LLMClient,
    temperature: float = 0.7,
) -> Callable:
    """通用节点工厂：调用 LLM → 解析 JSON → 写回状态并累计成本。"""

    def node(state: AgentState) -> dict:
        messages = messages_fn(state)
        result = llm.chat(messages, json_mode=True, temperature=temperature)
        output = _safe_json(result.content)
        logger.info("阶段[%s]完成 (tokens=%s, %.2fs)", stage_key, result.total_tokens,
                    result.latency_seconds)
        return {
            stage_key: output,
            "stage_status": {stage_key: "completed"},
            **_accumulate(state, result),
        }

    return node


# ===================== 节点工厂 =====================

def _web_search_for_research(web_search, state: AgentState) -> list:
    """为调研节点构造搜索 query 并检索（失败自动降级为空列表）。"""
    if web_search is None or not getattr(web_search, "available", False):
        return []
    inp = state["input"]
    topic = inp.get("topic", "")
    brand = inp.get("brand_name", "")
    queries = [topic]
    if brand and brand not in topic:
        queries.append(f"{topic} {brand}")
    queries.append(f"{topic} 行业趋势 市场")
    results: list = []
    for q in queries[:2]:
        try:
            results.extend(web_search.search(q, max_results=4))
        except Exception:
            logger.warning("联网搜索失败，调研降级为模型内部知识: %s", q, exc_info=True)
            break
    # 按 URL 去重，最多保留 8 条
    seen: set[str] = set()
    dedup = []
    for r in results:
        if r.url not in seen:
            seen.add(r.url)
            dedup.append(r)
    logger.info("调研联网检索完成，%d 条来源", len(dedup))
    return dedup[:8]


def make_research_node(llm: LLMClient, web_search=None) -> Callable:
    """1. 市场调研 Agent（支持联网检索，无 key 自动降级）。"""

    def node(state: AgentState) -> dict:
        results = _web_search_for_research(web_search, state)
        messages = build_research_messages(state, results)
        result = llm.chat(messages, json_mode=True, temperature=0.5)
        output = _safe_json(result.content)
        # 兜底：即使 LLM 未输出 sources，也附上检索来源（保证可溯源）
        if results and not output.get("sources"):
            output["sources"] = [
                {"title": r.title, "url": r.url} for r in results[:5]
            ]
        logger.info("阶段[research]完成 (tokens=%s, %.2fs, sources=%d)",
                    result.total_tokens, result.latency_seconds,
                    len(output.get("sources", [])))
        return {
            "research": output,
            "stage_status": {"research": "completed"},
            **_accumulate(state, result),
        }

    return node


def make_competitor_node(llm: LLMClient) -> Callable:
    """2. 竞品分析 Agent。"""
    return _stage_node("competitor", build_competitor_messages, llm, temperature=0.5)


def make_strategy_node(llm: LLMClient) -> Callable:
    """3. 策略制定 Agent。"""
    return _stage_node("strategy", build_strategy_messages, llm, temperature=0.5)


def make_copywriting_node(llm: LLMClient) -> Callable:
    """4. 文案创作 Agent（审校打回时会带 feedback 重新进入；记忆层注入品牌上下文）。"""

    def node(state: AgentState) -> dict:
        channel = get_channel(state["input"]["channel_id"])
        brand_ctx = get_brand_context(state.get("brand_id"))
        messages = build_copywriting_messages(state, channel, brand_context=brand_ctx)
        result = llm.chat(messages, json_mode=True, temperature=0.8)
        output = _safe_json(result.content)
        variants = output.get("variants", [])
        # 至少保证 1 个变体，避免下游解析失败
        if not isinstance(variants, list) or not variants:
            raise ValueError("文案创作 Agent 未返回任何变体")
        logger.info("阶段[copywriting]完成，%d 个变体 (round=%s)",
                    len(variants), state.get("revision_round", 0))
        return {
            "copywriting": variants,
            "stage_status": {"copywriting": "completed"},
            **_accumulate(state, result),
        }

    return node


def make_review_node(llm: LLMClient) -> Callable:
    """5. 审校优化 Agent：不通过则返回 feedback 与递增的 revision_round。"""

    def node(state: AgentState) -> dict:
        channel = get_channel(state["input"]["channel_id"])
        variants = state.get("copywriting", [])
        messages = build_review_messages(state, channel, variants)
        result = llm.chat(messages, json_mode=True, temperature=0.3)
        review = _safe_json(result.content)
        passed = bool(review.get("passed", False))
        update: dict[str, Any] = {
            "review": review,
            "stage_status": {"review": "completed"},
            **_accumulate(state, result),
        }
        if not passed:
            update["review_feedback"] = str(review.get("feedback", ""))
            update["revision_round"] = state.get("revision_round", 0) + 1
            logger.info("审校未通过，打回创作 (round=%s)", update["revision_round"])
        else:
            logger.info("审校通过，进入人工确认")
        return update

    return node


def human_confirm_node(state: AgentState) -> dict:
    """人工确认节点（human-in-the-loop）。

    通过 interrupt 暂停图执行，等待前端确认/编辑后以
    Command(resume=...) 恢复。确认内容会被写入 confirmed 状态。
    """
    payload = {
        "stage": "human_confirm",
        "task_id": state.get("task_id"),
        "strategy": state.get("strategy", {}),
        "variants": state.get("copywriting", []),
        "review": state.get("review", {}),
    }
    result = interrupt(payload)
    logger.info("人工确认完成: %s", result)
    return {"confirmed": result}
