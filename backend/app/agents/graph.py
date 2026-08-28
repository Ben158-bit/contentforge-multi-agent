"""LangGraph 图组装：流水线 + 审校循环 + 人工确认。

图结构：
START → research → competitor → strategy → copywriting → review
review --不通过且未达上限--> copywriting（打回重写）
review --通过--> human_confirm（interrupt 等待人工确认）→ END

checkpoint 持久化到 SQLite（SqliteSaver），thread_id 与 task_id 一一对应，
使任务可在进程重启后从断点恢复。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..config import get_settings
from ..llm import LLMClient
from .nodes import (
    human_confirm_node,
    make_competitor_node,
    make_copywriting_node,
    make_research_node,
    make_review_node,
    make_strategy_node,
)
from .state import AgentState

# checkpoint 线程 id 前缀：task-<task_id>
THREAD_PREFIX = "task-"


def _route_after_review(state: AgentState) -> str:
    """审校后路由：不通过且未达上限 → 打回创作；通过 → 人工确认。"""
    max_rounds = get_settings().max_revision_rounds
    passed = bool(state.get("review", {}).get("passed", False))
    if not passed and state.get("revision_round", 0) < max_rounds:
        return "copywriting"
    return "human_confirm"


def build_graph(llm: LLMClient, db_path: Optional[Path] = None, web_search=None):
    """构建并编译流水线图（SqliteSaver 持久化 checkpoint）。

    Args:
        llm: LLM 客户端（注入，便于测试用 fake）
        db_path: checkpoint 数据库路径，默认 <data_dir>/checkpoints.db
        web_search: 联网搜索客户端（可选）；配置后调研 Agent 会联网检索真实数据
    """
    builder = StateGraph(AgentState)
    builder.add_node("research", make_research_node(llm, web_search))
    builder.add_node("competitor", make_competitor_node(llm))
    builder.add_node("strategy", make_strategy_node(llm))
    builder.add_node("copywriting", make_copywriting_node(llm))
    builder.add_node("review", make_review_node(llm))
    builder.add_node("human_confirm", human_confirm_node)

    builder.add_edge(START, "research")
    builder.add_edge("research", "competitor")
    builder.add_edge("competitor", "strategy")
    builder.add_edge("strategy", "copywriting")
    builder.add_edge("copywriting", "review")
    builder.add_conditional_edges(
        "review",
        _route_after_review,
        {"copywriting": "copywriting", "human_confirm": "human_confirm"},
    )
    builder.add_edge("human_confirm", END)

    if db_path is None:
        db_path = get_settings().data_path / "checkpoints.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # from_conn_string 是 @contextmanager 包装：__enter__() 打开连接并返回 saver
    # 注意：必须把 context manager 挂到 graph 上保持引用，
    # 否则函数返回后 saver_cm 被 GC，generator finally 会关闭连接。
    saver_cm = SqliteSaver.from_conn_string(str(db_path))
    checkpointer = saver_cm.__enter__()
    graph = builder.compile(checkpointer=checkpointer)
    graph._saver_cm = saver_cm  # 保持连接存活
    return graph


def thread_id_for(task_id: int) -> str:
    return f"{THREAD_PREFIX}{task_id}"


def run_pipeline(graph, initial_state: dict) -> dict:
    """执行流水线直到中断点（人工确认）或完成，返回最终状态。

    Args:
        graph: build_graph 编译结果
        initial_state: 至少包含 task_id 与 input 的初始状态
    """
    thread_id = thread_id_for(initial_state["task_id"])
    return graph.invoke(initial_state, {"configurable": {"thread_id": thread_id}})


def resume_pipeline(graph, task_id: int, confirmation: dict) -> dict:
    """恢复被中断的流水线（提交人工确认/编辑结果）。"""
    thread_id = thread_id_for(task_id)
    return graph.invoke(
        Command(resume=confirmation), {"configurable": {"thread_id": thread_id}}
    )


def get_pipeline_state(graph, task_id: int) -> Optional[dict]:
    """查询任务当前的 checkpoint 状态（未执行过返回 None）。"""
    thread_id = thread_id_for(task_id)
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    if snap is None:
        return None
    values: dict[str, Any] = dict(snap.values or {})
    values["next"] = list(snap.next or [])
    return values


def update_pipeline_state(graph, task_id: int, values: dict, as_node: str) -> None:
    """更新 checkpoint 状态，并把执行位置重定位到 as_node 之后。

    用于人工编辑回填：用户编辑上游阶段后，把编辑值写回状态，
    下一次 stream(None) 会从 as_node 的下游节点重新执行。
    """
    cfg = {"configurable": {"thread_id": thread_id_for(task_id)}}
    graph.update_state(cfg, values, as_node=as_node)
