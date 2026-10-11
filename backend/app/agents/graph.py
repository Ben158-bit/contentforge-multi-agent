"""LangGraph 图组装：流水线 + 审校循环 + 人工确认。

图结构**不再硬编码**，而是由 ``pipelines/<pipeline_id>.yaml`` 定义
（见 ``pipeline_definition`` 模块）。默认流水线 ``marketing`` 把改造前的
硬编码图加上确定性守门阶段，因此 ``build_graph()`` 省略 ``pipeline_id``
时的节点集合是可预期的。

本模块是**组合根**：节点实现仍在 ``nodes/`` 包，这里负责把定义里声明的
``factory`` / ``router`` 名字解析成真正可调用的对象。这样
``pipeline_definition`` 只做纯数据（不 import 任何节点实现，避免循环依赖），
而「新增一条流水线」只需要加一个 YAML。

本模块同时持有**由定义派生的阶段契约**（``definition_stage_keys`` /
``definition_editable_stages`` / ``definition_editable_stage_set``）：
runner 与 api 都要用同一份，放这里才不会各自抄一遍。

LangGraph 0.6.11 只支持**静态边**，所以所有节点都照常 ``add_node``，
需要分支的地方由**查配置的路由函数**决定走向；人工闸门仍用 interrupt 暂停。

checkpoint 持久化到 SQLite（SqliteSaver），thread_id 与 task_id 一一对应，
使任务可在进程重启后从断点恢复。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..config import get_settings
from ..llm import LLMClient
from .nodes import (
    STAGE_KEYS,
    make_competitor_node,
    make_copywriting_node,
    make_guardrail_node,
    make_human_confirm_node,
    make_humanize_node,
    make_research_node,
    make_review_node,
    make_strategy_node,
)
from .pipeline_definition import (
    DEFAULT_PIPELINE_ID,
    NODE_END,
    NODE_START,
    NodeSpec,
    PipelineDefinition,
    load_pipeline,
    resolve_factories,
)
from .state import AgentState

logger = logging.getLogger(__name__)

# checkpoint 线程 id 前缀：task-<task_id>
THREAD_PREFIX = "task-"

# 未挂流水线定义的图回落到这两个常量，保证老调用方行为不变
# （迁移前 runner 就是用这两份；现在它们只是兼容层，不是第二套真源）。
UPSTREAM_STAGE_KEYS = ["research", "competitor", "strategy"]

# 定义文件里的 factory / router 名 → 真正实现。
#
# 集中放在组合根，是为了让 pipeline_definition 保持纯数据（不 import 节点）。
# 每个 builder 统一收 (spec, llm, web_search)：签名一致，新增节点不用改调用方；
# spec 让节点能读到 YAML 里的配置 —— 目前是 temperature 与 state_key，
# 也就是说「节点写哪个状态通道」由 YAML 决定，不在代码里再抄一份。
NodeBuilder = Callable[[NodeSpec, LLMClient, Any], Callable]

NODE_BUILDERS: dict[str, NodeBuilder] = {
    "make_research_node": lambda spec, llm, web_search: make_research_node(
        llm, web_search, temperature=spec.temperature, state_key=spec.state_key
    ),
    "make_competitor_node": lambda spec, llm, web_search: make_competitor_node(
        llm, temperature=spec.temperature, state_key=spec.state_key
    ),
    "make_strategy_node": lambda spec, llm, web_search: make_strategy_node(
        llm, temperature=spec.temperature, state_key=spec.state_key
    ),
    "make_copywriting_node": lambda spec, llm, web_search: make_copywriting_node(
        llm, temperature=spec.temperature, state_key=spec.state_key
    ),
    "make_review_node": lambda spec, llm, web_search: make_review_node(
        llm, temperature=spec.temperature, state_key=spec.state_key
    ),
    # 确定性守门节点：不接收 llm（它不是 Agent，不该有调用模型的能力）
    "make_guardrail_node": lambda spec, llm, web_search: make_guardrail_node(
        state_key=spec.state_key
    ),
    # 去 AI 味改写：是 Agent（消耗 token），但只在公众号渠道真正调用模型
    "make_humanize_node": lambda spec, llm, web_search: make_humanize_node(
        llm, temperature=spec.temperature, state_key=spec.state_key
    ),
    # 人工闸门不是工厂：它由 interrupt 暂停，不消费 llm
    "make_human_confirm_node": lambda spec, llm, web_search: make_human_confirm_node(
        state_key=spec.state_key
    ),
}


def _route_after_review(state: AgentState) -> str:
    """审校后路由：不通过且未达上限 → 打回创作；通过 → 人工确认。

    上限来自 ``Settings.max_revision_rounds``（marketing.yaml 的
    ``max_rounds_source`` 只声明这个出处，值不写进 YAML）。
    """
    max_rounds = get_settings().max_revision_rounds
    passed = bool(state.get("review", {}).get("passed", False))
    if not passed and state.get("revision_round", 0) < max_rounds:
        return "copywriting"
    return "human_confirm"


ROUTERS: dict[str, Callable[[AgentState], str]] = {
    "route_after_review": _route_after_review,
}


def _with_retries(node_fn: Callable, max_retries: int) -> Callable:
    """按定义里的 ``max_retries`` 包装节点级的异常重试。

    ``max_retries <= 0`` 时**原样返回**（零包装、零行为差异）。
    只重试「抛异常」的节点；返回了不合格内容的节点不在这里重试 ——
    内容质量判断属于守门层的职责，混在一起会让成本统计失真。
    """
    if max_retries <= 0:
        return node_fn

    def wrapper(state: AgentState) -> dict:
        last: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return node_fn(state)
            except Exception as exc:  # noqa: BLE001 - 重试后仍失败则原样抛出
                last = exc
                logger.warning(
                    "节点执行失败（第 %d/%d 次尝试）：%s", attempt + 1, max_retries + 1, exc
                )
        assert last is not None
        raise last

    return wrapper


def build_graph(
    llm: LLMClient,
    db_path: Optional[Path] = None,
    web_search=None,
    competitor_source=None,
    pipeline_id: str = DEFAULT_PIPELINE_ID,
    *,
    pipelines_dir: Optional[Path] = None,
):
    """构建并编译流水线图（SqliteSaver 持久化 checkpoint）。

    Args:
        llm: LLM 客户端（注入，便于测试用 fake）
        db_path: checkpoint 数据库路径，默认 <data_dir>/checkpoints.db
        web_search: 联网搜索客户端（可选）；配置后调研 Agent 会联网检索真实数据
        competitor_source: 竞品数据源（可选，``agents/competitor_source.py``）；
            配置后竞品分析会抓真实竞品内容（小红书/公众号/微博）并注入。无 key
            或抓取失败自动降级为模型内部知识，不影响流水线。
        pipeline_id: 流水线定义 id，默认 marketing（省略时行为与改造前一致）
        pipelines_dir: 定义文件所在目录；默认内置 ``agents/pipelines``。
            传其它目录即可加载仓库外/测试专用定义，无需改任何 Python 代码。
    """
    definition = load_pipeline(pipeline_id, directory=pipelines_dir)

    # 先整体校验 factory / router 都能解析，再动 builder ——
    # 避免建到一半失败留下半成品定义难以排查
    builders = resolve_factories(definition, {**NODE_BUILDERS, **ROUTERS})

    # 竞品分析节点需要一个额外的数据源依赖（真实竞品数据）。NODE_BUILDERS 的
    # 固定签名只传 (spec, llm, web_search)，这里用闭包把 competitor_source 捕获
    # 进去 —— 与 research 的 web_search 一样是「可选增强」，未配置时行为不变。
    if competitor_source is not None and "competitor" in builders:
        builders["competitor"] = lambda spec, llm, ws: make_competitor_node(
            llm, competitor_source, temperature=spec.temperature, state_key=spec.state_key
        )

    builder = StateGraph(AgentState)
    for spec in definition.nodes:
        # 所有 kind 都走同一条装配路径：builder(spec, llm, web_search)。
        # 新增一种节点类型不需要在组合根加分支。
        node_fn = builders[spec.key](spec, llm, web_search)
        builder.add_node(spec.key, _with_retries(node_fn, spec.max_retries))

    builder.add_edge(START, definition.entry)
    for edge in definition.edges:
        if edge.source == NODE_START:
            continue  # 入口已在上面统一接上
        if edge.is_conditional:
            builder.add_conditional_edges(
                edge.source,
                ROUTERS[edge.router],
                {branch: _endpoint(target) for branch, target in edge.mapping},
            )
        else:
            builder.add_edge(edge.source, _endpoint(edge.targets[0]))
    if not definition.edges_from(definition.human_gate):
        # 人工闸门没有出边会导致图跑不完，这里显式补一条到 END（并留日志）
        logger.warning(
            "流水线 %s 的人工闸门 %s 没有出边，自动接到 END",
            definition.pipeline_id,
            definition.human_gate,
        )
        builder.add_edge(definition.human_gate, END)

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
    # runner 通过这个属性读取当前图实际用的定义，从而不必硬编码阶段列表
    graph._pipeline_definition = definition
    return graph


def _endpoint(target: str) -> str:
    """把定义里的 END 哨兵翻译成 LangGraph 的 END。"""
    return END if target == NODE_END else target


def pipeline_definition_of(graph) -> Optional[PipelineDefinition]:
    """取图所用的流水线定义（老代码/第三方构造函数可能没挂，返回 None）。"""
    return getattr(graph, "_pipeline_definition", None)


# --------------------------------------------------------------------------
# 由定义派生的「阶段契约」
#
# 放在组合根而不是 runner/api：这几条都是从**图所挂的定义**推导出来的，
# 谁需要都得算同一份。回落常量只为兼容没挂定义的老图，不是第二套真源。
# --------------------------------------------------------------------------
def definition_stage_keys(graph) -> list[str]:
    """图的阶段列表（按定义声明顺序）。未挂定义的图回落模块常量。"""
    definition = pipeline_definition_of(graph)
    if definition is None:
        return list(STAGE_KEYS)
    return list(definition.stage_keys())


def definition_editable_stages(graph) -> list[str]:
    """允许人工编辑、编辑后需重跑下游的**上游**阶段。

    这是 runner 决定回填范围的依据；顺序即「最早被编辑的阶段」判定顺序。
    """
    definition = pipeline_definition_of(graph)
    if definition is None or not definition.upstream_editable_stages:
        return list(UPSTREAM_STAGE_KEYS)
    return list(definition.upstream_editable_stages)


def definition_editable_stage_set(graph) -> set[str]:
    """所有可人工编辑的阶段 = **产出即交付物**的阶段。

    确定性步骤（如守门层）的产出是工具的结论，手改无意义且会被下次执行覆盖；
    ``editable: false`` 的 Agent 阶段（如 humanize，产出是处置报告、真正改的是
    copywriting）同理 —— 开放它就等于开放一个静默失效的编辑入口。

    没挂定义的图返回空集（fail closed）：无法判断哪些阶段可编辑时，
    宁可谁都不许改，也不要放行一个可能改错的入口。
    """
    definition = pipeline_definition_of(graph)
    if definition is None:
        return set()
    return {node.key for node in definition.stage_nodes() if node.is_editable}


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
