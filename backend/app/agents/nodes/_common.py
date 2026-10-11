"""节点共用管道：LLM 输出的解析、成本累加、通用 LLM 阶段工厂。

为什么单独一个模块
------------------
**成本公式只能有一份**。humanize 节点一次执行会调用多次模型（每个需要改写的
变体一次），累加逻辑如果再抄一遍单价常量，就会出现第二套成本口径 ——
这与「字数口径只能有一套」是同一条纪律。

放在独立模块而不是 ``nodes/__init__.py``，是为了让 ``nodes/humanize.py``
能 import 它而不形成包内循环：``__init__.py`` 需要 import 各子节点模块，
子节点若反过来 import ``__init__`` 就成了环，且 ImportError 只在
「``__init__`` 尚未执行到该名字」时才出现，非常难排查。

``nodes/__init__.py`` 会把这里的公开名字重新导出（含下划线别名），
以保证既有调用方（如 ``evals/run_eval.py`` 导入 ``_safe_json``）不受影响。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable

from ...llm import LLMClient, LLMResult
from ..state import AgentState

logger = logging.getLogger(__name__)

# DeepSeek deepseek-chat 定价（人民币 元/百万 tokens，2025 年基准，用于成本统计）
COST_INPUT_PER_M = 2.0
COST_OUTPUT_PER_M = 8.0


def temperature(default: float, override: float | None) -> float:
    """温度取「YAML 声明优先」。

    未声明（None）时回落到节点内置默认值，保证不经过流水线定义的旧调用方
    （如 test_nodes.py 直接构造节点）行为不变。
    """
    return default if override is None else override


def safe_json(text: str) -> Any:
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


def _cost_of(result: LLMResult) -> float:
    return (
        result.prompt_tokens / 1_000_000 * COST_INPUT_PER_M
        + result.completion_tokens / 1_000_000 * COST_OUTPUT_PER_M
    )


def accumulate(state: AgentState, result: LLMResult) -> dict:
    """将一次 LLM 调用的成本与耗时累加进状态。"""
    return {
        "total_cost": state.get("total_cost", 0.0) + round(_cost_of(result), 6),
        "total_latency": state.get("total_latency", 0.0) + result.latency_seconds,
    }


def accumulate_all(state: AgentState, results: Iterable[LLMResult]) -> dict:
    """把**多次** LLM 调用的成本与耗时一次性累加（humanize 一次跑多个变体）。

    与 ``accumulate`` 用同一个单价常量与同一个取整时机（先求和再 round），
    所以多次调用的累加结果与逐次调用完全一致。
    """
    results = list(results)
    if not results:
        return {}
    cost = sum(_cost_of(result) for result in results)
    latency = sum(result.latency_seconds for result in results)
    return {
        "total_cost": state.get("total_cost", 0.0) + round(cost, 6),
        "total_latency": state.get("total_latency", 0.0) + latency,
    }


def stage_node(
    state_key: str,
    messages_fn: Callable[[AgentState], list[dict[str, str]]],
    llm: LLMClient,
    temperature: float = 0.7,
) -> Callable:
    """通用节点工厂：调用 LLM → 解析 JSON → 写回状态并累计成本。"""

    def node(state: AgentState) -> dict:
        messages = messages_fn(state)
        result = llm.chat(messages, json_mode=True, temperature=temperature)
        output = safe_json(result.content)
        logger.info("阶段[%s]完成 (tokens=%s, %.2fs)", state_key, result.total_tokens,
                    result.latency_seconds)
        return {
            state_key: output,
            "stage_status": {state_key: "completed"},
            **accumulate(state, result),
        }

    return node
