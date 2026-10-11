"""Agent 节点实现（包）。

节点分三类：

- **6 个 LLM 阶段**：research / competitor / strategy / copywriting /
  humanize（去 AI 味，仅公众号）/ review；
- **1 个确定性守门节点**：``guardrail``（见 ``nodes/guardrail.py``）——
  不调用模型，只跑可复现的规则；
- **1 个人工闸门**：human_confirm（interrupt）。

所有节点通过依赖注入拿 LLM 客户端（便于测试用 fake），返回 dict 由
LangGraph 合并进状态；**同步函数**——langgraph 0.6.x 在 Python 3.10 下，
interrupt 的 config contextvar 只在同步执行路径中被设置。

状态键的来源：每个节点写入哪个状态通道由流水线定义里的 ``contract.state_key``
决定（工厂以 ``state_key=`` 形参接收）。形参缺省值与改造前的硬编码值一致，
所以 ``make_research_node(fake)`` 这类直接调用行为不变。

LLM 管道（解析 / 成本累加 / 通用阶段工厂）在 ``_common.py``：抽出去是为了让
``humanize.py`` 能复用它而不形成包内循环导入，同时保证**成本公式只有一份**。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.types import interrupt

from ...guardrails import apply_derived_hashtags
from ...memory import get_brand_context
from ...templates import get_channel
from ..pipeline_definition import load_default_pipeline
from ..prompts import (
    build_competitor_messages,
    build_copywriting_messages,
    build_research_messages,
    build_review_messages,
    build_strategy_messages,
)
from ..state import AgentState
from ._common import (
    COST_INPUT_PER_M,
    COST_OUTPUT_PER_M,
    accumulate,
    accumulate_all,
    safe_json,
    stage_node,
    temperature,
)
from .guardrail import make_guardrail_node
from .humanize import make_humanize_node

logger = logging.getLogger(__name__)

# 兼容既有调用方（evals/run_eval.py 导入的是下划线名）
_temperature = temperature
_safe_json = safe_json
_accumulate = accumulate
_accumulate_all = accumulate_all
_stage_node = stage_node

# 阶段展示顺序（前端流水线可视化）
#
# **单一真源**：阶段列表来自 pipelines/marketing.yaml 里 is_stage: true 的节点，
# 不在这里再抄一份 —— 两处抄写必然漂移，而漂移的表现是「前端少显示一个阶段」
# 这类很难归因的问题。要改阶段，改 YAML。
_DEFAULT_PIPELINE = load_default_pipeline()
STAGE_KEYS = list(_DEFAULT_PIPELINE.stage_keys())
STAGE_LABELS = {node.key: node.label for node in _DEFAULT_PIPELINE.stage_nodes()}


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


# ===================== 节点工厂 =====================

def make_research_node(
    llm,
    web_search=None,
    *,
    temperature: float | None = None,
    state_key: str = "research",
) -> Callable:
    """1. 市场调研 Agent（支持联网检索，无 key 自动降级）。"""
    temp = _temperature(0.5, temperature)

    def node(state: AgentState) -> dict:
        results = _web_search_for_research(web_search, state)
        messages = build_research_messages(state, results)
        result = llm.chat(messages, json_mode=True, temperature=temp)
        output = _safe_json(result.content)
        # 兜底：即使 LLM 未输出 sources，也附上检索来源（保证可溯源）
        if results and not output.get("sources"):
            output["sources"] = [
                {"title": r.title, "url": r.url} for r in results[:5]
            ]
        logger.info("阶段[%s]完成 (tokens=%s, %.2fs, sources=%d)",
                    state_key, result.total_tokens, result.latency_seconds,
                    len(output.get("sources", [])))
        return {
            state_key: output,
            "stage_status": {state_key: "completed"},
            **_accumulate(state, result),
        }

    return node


def make_competitor_node(
    llm,
    competitor_source=None,
    *,
    temperature: float | None = None,
    state_key: str = "competitor",
) -> Callable:
    """2. 竞品分析 Agent（可选注入真实竞品数据源）。

    ``competitor_source`` 是与 ``web_search`` 同构的依赖：配置后节点会先抓真实
    竞品内容（小红书笔记 / 公众号文章 / 微博账号），注入提示词让 Agent 基于
    真实实例拆解，并把证据附到输出的 ``evidence`` 字段供前端溯源。未配置 /
    抓取失败时自动降级为模型内部知识（原行为），绝不中断流水线。
    """
    temp = _temperature(0.5, temperature)

    def node(state: AgentState) -> dict:
        evidence: list = []
        if competitor_source is not None:
            try:
                evidence = list(competitor_source.fetch(state) or [])
            except Exception:  # noqa: BLE001 - 数据源失败绝不影响主流水线
                logger.warning("竞品数据源抓取异常，降级为模型内部知识", exc_info=True)
                evidence = []
        messages = build_competitor_messages(state, evidence)
        result = llm.chat(messages, json_mode=True, temperature=temp)
        output = _safe_json(result.content)
        # 证据附加到输出（可溯源），但只对 dict 形态生效——list 是异常兜底
        if evidence and isinstance(output, dict):
            output["evidence"] = evidence
        logger.info("阶段[%s]完成 (tokens=%s, %.2fs, evidence=%d)",
                    state_key, result.total_tokens, result.latency_seconds, len(evidence))
        return {
            state_key: output,
            "stage_status": {state_key: "completed"},
            **_accumulate(state, result),
        }

    return node


def make_strategy_node(
    llm, *, temperature: float | None = None, state_key: str = "strategy"
) -> Callable:
    """3. 策略制定 Agent。"""
    return _stage_node(
        state_key, build_strategy_messages, llm, temperature=_temperature(0.5, temperature)
    )


def make_copywriting_node(
    llm, *, temperature: float | None = None, state_key: str = "copywriting"
) -> Callable:
    """4. 文案创作 Agent（审校打回时会带 feedback 重新进入；记忆层注入品牌上下文）。"""
    temp = _temperature(0.8, temperature)

    def node(state: AgentState) -> dict:
        channel = get_channel(state["input"]["channel_id"])
        brand_ctx = get_brand_context(state.get("brand_id"))
        messages = build_copywriting_messages(state, channel, brand_context=brand_ctx)
        result = llm.chat(messages, json_mode=True, temperature=temp)
        output = _safe_json(result.content)
        variants = output.get("variants", [])
        # 至少保证 1 个变体，避免下游解析失败
        if not isinstance(variants, list) or not variants:
            raise ValueError("文案创作 Agent 未返回任何变体")
        # 分层标签（小红书）在**节点出口**就扁平化进 hashtags —— 标签只有这一个来源。
        #
        # 为什么不能等守门层来推导：`runner._write_chunk` 是按**节点名**写阶段产出的，
        # 守门节点回写 `copywriting` 状态通道**不会**更新 `copywriting` 那行阶段记录。
        # 于是「创作阶段产出」这份持久化数据里 hashtags 是空的，而前端变体编辑器、
        # 导出与下游读的都是它 —— 实测跑出过「守门报告契约全通过、界面标签却是空」。
        for variant in variants:
            if isinstance(variant, dict):
                apply_derived_hashtags(variant, channel)
        logger.info("阶段[%s]完成，%d 个变体 (round=%s)",
                    state_key, len(variants), state.get("revision_round", 0))
        return {
            state_key: variants,
            "stage_status": {state_key: "completed"},
            **_accumulate(state, result),
        }

    return node


def make_review_node(
    llm, *, temperature: float | None = None, state_key: str = "review"
) -> Callable:
    """5. 审校优化 Agent：不通过则返回 feedback 与递增的 revision_round。"""
    temp = _temperature(0.3, temperature)

    def node(state: AgentState) -> dict:
        channel = get_channel(state["input"]["channel_id"])
        variants = state.get("copywriting", [])
        messages = build_review_messages(state, channel, variants)
        result = llm.chat(messages, json_mode=True, temperature=temp)
        review = _safe_json(result.content)
        passed = bool(review.get("passed", False))
        update: dict[str, Any] = {
            state_key: review,
            "stage_status": {state_key: "completed"},
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


def make_human_confirm_node(*, state_key: str = "confirmed") -> Callable:
    """人工确认节点工厂（human-in-the-loop）。

    通过 interrupt 暂停图执行，等待前端确认/编辑后以
    Command(resume=...) 恢复。确认内容会被写入 ``state_key`` 指定的状态通道，
    并把守门层与审校的「需人工处理」事项一并交给前端展示。
    """

    def node(state: AgentState) -> dict:
        payload = {
            "stage": "human_confirm",
            "task_id": state.get("task_id"),
            "strategy": state.get("strategy", {}),
            "variants": state.get("copywriting", []),
            "review": state.get("review", {}),
            # 守门层的确定性结论必须一起送出去，否则「标记而不静默改写」
            # 的约定会在最后一步丢掉——用户看不到就没法处理。
            "guardrail": state.get("guardrail") or {},
            # 去 AI 味只在公众号启用，其报告（采纳/拒绝及原因）也要能看到，
            # 否则「改写失败已保留原文」这件事只存在于 JSON 里没人读。
            "humanize": state.get("humanize") or {},
            "needs_human": list(state.get("needs_human") or []),
        }
        result = interrupt(payload)
        logger.info("人工确认完成: %s", result)
        return {state_key: result}

    return node
