"""流水线定义（PipelineDefinition）：把节点链从代码里抽出来变成配置。

设计要点
--------
1. **本模块只做数据**：解析 + 校验 YAML，**不 import 任何节点实现**
   —— 否则会与 ``nodes/`` 形成循环依赖。节点的实际工厂函数由组合根
   ``graph.py`` 通过注册表解析。
2. **校验在加载期完成，失败就要响**：未知节点、缺 prompt 契约、
   输入无人产出、入口不唯一、不可达节点 —— 全部抛 ``PipelineDefinitionError``。
   配置错误绝不能被静默忽略（那会表现为「某个 Agent 悄悄不跑了」）。
3. **节点分三类**（``kind``）：``agent``（LLM 节点，需 prompt 契约）、
   ``step``（确定性步骤，不调模型，**不写** prompt 契约）、
   ``human_gate``（interrupt 闸门）。判据是「需要判断力 vs 可穷举复现」。
4. **单一真源**：节点写哪个状态通道由 ``contract.state_key`` 声明，
   节点工厂以形参接收 —— 代码里不再重复抄一份状态键。

与 LangGraph 0.6.11 的关系：该版本是**静态边**，所以所有节点都照常
``add_node``，需要「跳过/条件」的地方由**查配置的路由函数**决定走向。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

DEFAULT_PIPELINE_ID = "marketing"
PIPELINES_DIR = Path(__file__).resolve().parent / "pipelines"
PIPELINE_SUFFIXES = (".yaml", ".yml")

# 图启动时就已存在的状态键：节点可以读，但不需要任何节点产出它们
INITIAL_STATE_KEYS = frozenset({"task_id", "input", "brand_id"})

# 边端点里可以出现的图级哨兵（不是真正的节点）
NODE_START = "START"
NODE_END = "END"
ENDPOINT_SENTINELS = frozenset({NODE_START, NODE_END})

KIND_AGENT = "agent"
KIND_STEP = "step"
KIND_HUMAN_GATE = "human_gate"
KINDS = (KIND_AGENT, KIND_STEP, KIND_HUMAN_GATE)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PipelineDefinitionError(ValueError):
    """流水线定义非法。加载期抛出，不静默放行。"""


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeSpec:
    """一个逻辑节点。

    ``kind`` 区分三类节点，契约要求也随之不同：

    - ``agent``：**LLM 节点**，需要 ``factory`` + ``prompt_key``。
    - ``step``：**确定性步骤**（如守门层），需要 ``factory``，**不要** ``prompt_key``
      ——它不调模型，声明提示词契约只会误导。这类节点同输入必同输出。
    - ``human_gate``：人工闸门，需要 ``factory``，由 interrupt 暂停。

    判据只有一条：**需要判断力的交给 agent，可穷举/可复现的交给 step**。
    """

    key: str
    label: str
    kind: str
    factory: str = ""
    prompt_key: str = ""
    # 本节点写入的**主状态键**。节点工厂以 state_key 形参接收它，
    # 所以 YAML 是这件事的单一真源。阶段节点的 key 必须等于 state_key，
    # 原因见 parse_definition 里的校验与说明。
    state_key: str = ""
    temperature: float | None = None
    max_retries: int = 0
    # 是否作为「阶段」回写数据库 / 展示在前端流水线里（人工闸门不是阶段）
    is_stage: bool = True
    # 该阶段的产出能否被人工编辑并回填（None = 按 kind 推导）。
    # 显式 false 用于「虽然是 Agent，但产出本身不是交付物」的节点：
    # humanize 的产出是改写处置报告，真正被改的是 copywriting ——
    # 开放它的编辑入口只会得到一个「静默失效的编辑」（存了但不会回填）。
    editable: bool | None = None
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    description: str = ""

    @property
    def is_agent(self) -> bool:
        return self.kind == KIND_AGENT

    @property
    def is_step(self) -> bool:
        return self.kind == KIND_STEP

    @property
    def is_human_gate(self) -> bool:
        return self.kind == KIND_HUMAN_GATE

    @property
    def calls_llm(self) -> bool:
        """本节点是否消耗 token（成本统计与可测试性都关心这件事）。"""
        return self.kind == KIND_AGENT

    @property
    def is_editable(self) -> bool:
        """产出能否被人工编辑并回填（决定前端「编辑」入口与 runner 的回填范围）。

        判据不是「是不是 Agent」，而是**产出本身是不是交付物**：

        - 分析/创作类 Agent 阶段：产出即交付物 → 可编辑；
        - 确定性步骤（守门层）：工具结论，手改无意义、且会被下次执行覆盖 → 不可编辑；
        - 显式 ``editable: false`` 的 Agent 阶段（去 AI 味）：产出是改写处置报告，
          真正生效的是它回写的 ``copywriting`` → 不可编辑。

        这条约束是**防静默失效**：编辑入口存在但值不会回填，用户会以为改了。
        """
        if not (self.is_stage and self.is_agent):
            return False
        return self.editable is not False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "factory": self.factory,
            "prompt_key": self.prompt_key,
            "state_key": self.state_key,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "is_stage": self.is_stage,
            "editable": self.editable,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "description": self.description,
        }


@dataclass(frozen=True)
class EdgeSpec:
    """一条边。

    - 无条件边：``targets`` 只有一个元素，``router`` 为空。
    - 条件边：``router`` 为路由函数名，``mapping`` 为「路由返回值 → 目标节点」。
    ``max_rounds_source`` 用于回路：写清「重试上限从哪里读」，而不是把上限
    硬编码进 YAML —— 上限由 ``Settings`` 掌管（测试会 monkeypatch 它）。
    """

    source: str
    targets: tuple[str, ...] = ()
    router: str = ""
    mapping: tuple[tuple[str, str], ...] = ()
    max_rounds_source: str = ""

    @property
    def is_conditional(self) -> bool:
        return bool(self.router)

    def all_endpoints(self) -> tuple[str, ...]:
        return (self.source, *self.targets, *(target for _, target in self.mapping))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"source": self.source}
        if self.is_conditional:
            data["router"] = self.router
            data["mapping"] = {key: value for key, value in self.mapping}
            if self.max_rounds_source:
                data["max_rounds_source"] = self.max_rounds_source
        else:
            data["target"] = self.targets[0] if self.targets else ""
        return data


@dataclass(frozen=True)
class PipelineDefinition:
    pipeline_id: str
    version: int
    description: str
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    human_gate: str
    entry: str
    success_target: str = NODE_END
    failure_target: str = NODE_END
    upstream_editable_stages: tuple[str, ...] = ()
    source_path: Path | None = None

    # ---------- 查询 ----------
    def node_keys(self) -> tuple[str, ...]:
        return tuple(node.key for node in self.nodes)

    def node(self, key: str) -> NodeSpec:
        for candidate in self.nodes:
            if candidate.key == key:
                return candidate
        raise PipelineDefinitionError(
            f"流水线 {self.pipeline_id!r} 中没有节点 {key!r}；已声明：{list(self.node_keys())}"
        )

    def stage_nodes(self) -> tuple[NodeSpec, ...]:
        return tuple(node for node in self.nodes if node.is_stage)

    def stage_keys(self) -> tuple[str, ...]:
        """按声明顺序返回「阶段」节点键（对应前端流水线顺序）。"""
        return tuple(node.key for node in self.stage_nodes())

    def labels(self) -> dict[str, str]:
        return {node.key: node.label for node in self.nodes}

    def agent_nodes(self) -> tuple[NodeSpec, ...]:
        """LLM 节点（消耗 token；成本、prompt 契约与它们相关）。"""
        return tuple(node for node in self.nodes if node.is_agent)

    def step_nodes(self) -> tuple[NodeSpec, ...]:
        """确定性步骤节点（不调模型，同输入必同输出）。"""
        return tuple(node for node in self.nodes if node.is_step)

    def buildable_nodes(self) -> tuple[NodeSpec, ...]:
        """所有需要从工厂注册表解析实现的节点（agent + step + human_gate）。"""
        return tuple(node for node in self.nodes if node.factory)

    def human_gate_spec(self) -> NodeSpec:
        return self.node(self.human_gate)

    def edges_from(self, source: str) -> tuple[EdgeSpec, ...]:
        return tuple(edge for edge in self.edges if edge.source == source)

    def declared_inputs(self) -> set[str]:
        return {key for node in self.nodes for key in node.inputs}

    def declared_outputs(self) -> set[str]:
        return {key for node in self.nodes for key in node.outputs}

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "version": self.version,
            "description": self.description,
            "human_gate": self.human_gate,
            "success_target": self.success_target,
            "failure_target": self.failure_target,
            "upstream_editable_stages": list(self.upstream_editable_stages),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


# --------------------------------------------------------------------------
# 解析 + 校验
# --------------------------------------------------------------------------
def _as_str(value: Any, *, where: str, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text


def _as_tuple(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise PipelineDefinitionError(f"{where} 应为列表，不能是字符串")
    if not isinstance(value, (list, tuple)):
        raise PipelineDefinitionError(f"{where} 应为列表，实际是 {type(value).__name__}")
    return tuple(str(item).strip() for item in value if str(item).strip())


def parse_definition(data: Mapping[str, Any], *, source: str = "<dict>") -> PipelineDefinition:
    """把原始 dict 解析成 ``PipelineDefinition``，并做完整校验。"""
    if not isinstance(data, Mapping):
        raise PipelineDefinitionError(f"{source}: 顶层应为映射（mapping）")

    pipeline_id = _as_str(data.get("pipeline_id"), where="pipeline_id")
    if not pipeline_id:
        raise PipelineDefinitionError(f"{source}: 缺少 pipeline_id")
    if not _KEY_RE.match(pipeline_id):
        raise PipelineDefinitionError(
            f"{source}: pipeline_id {pipeline_id!r} 非法（应匹配 {_KEY_RE.pattern}）"
        )

    try:
        version = int(data.get("version", 1))
    except (TypeError, ValueError) as exc:
        raise PipelineDefinitionError(f"{source}: version 应为整数") from exc
    if version < 1:
        raise PipelineDefinitionError(f"{source}: version 必须 ≥ 1，实际 {version}")

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)) or not raw_nodes:
        raise PipelineDefinitionError(f"{source}: nodes 必须是非空列表")

    nodes: list[NodeSpec] = []
    for index, raw in enumerate(raw_nodes):
        where = f"{source}: nodes[{index}]"
        if not isinstance(raw, Mapping):
            raise PipelineDefinitionError(f"{where} 应为映射")
        key = _as_str(raw.get("key"), where=f"{where}.key")
        if not key:
            raise PipelineDefinitionError(f"{where} 缺少 key")
        if not _KEY_RE.match(key):
            raise PipelineDefinitionError(f"{where}: key {key!r} 非法（应匹配 {_KEY_RE.pattern}）")

        kind = _as_str(raw.get("kind"), where=f"{where}.kind") or KIND_AGENT
        if kind not in KINDS:
            raise PipelineDefinitionError(
                f"{where}: kind {kind!r} 未知，只支持 {list(KINDS)}"
            )

        factory = _as_str(raw.get("factory"), where=f"{where}.factory")
        prompt_key = _as_str(raw.get("prompt_key"), where=f"{where}.prompt_key")
        state_key = _as_str(raw.get("state_key"), where=f"{where}.state_key")

        raw_contract = raw.get("contract")
        if raw_contract is not None and not isinstance(raw_contract, Mapping):
            raise PipelineDefinitionError(f"{where}.contract 应为映射")
        if isinstance(raw_contract, Mapping):
            # contract 里的同名字段可覆盖顶层，便于把契约集中书写
            factory = _as_str(raw_contract.get("factory"), where=f"{where}.contract.factory") or factory
            prompt_key = (
                _as_str(raw_contract.get("prompt_key"), where=f"{where}.contract.prompt_key")
                or prompt_key
            )
            state_key = (
                _as_str(raw_contract.get("state_key"), where=f"{where}.contract.state_key")
                or state_key
            )

        # 契约完整性：每个节点都要有实现；LLM 节点还必须有 prompt 契约。
        # 缺失就在这里失败，而不是等到运行时表现为「某个 Agent 悄悄不跑了」。
        if not factory:
            raise PipelineDefinitionError(f"{where}: 节点缺少 contract.factory（实现）")
        if kind == KIND_AGENT and not prompt_key:
            raise PipelineDefinitionError(f"{where}: agent 节点缺少 contract.prompt_key")
        if kind == KIND_STEP and prompt_key:
            # 确定性步骤不调模型。写了 prompt_key 说明作者以为它在调模型，
            # 这种误解必须当场纠正，而不是留一个永远用不上的字段。
            raise PipelineDefinitionError(
                f"{where}: step 节点不应声明 contract.prompt_key（{prompt_key!r}）——"
                "它不调用模型。需要 LLM 判断力的节点请用 kind: agent。"
            )
        if not state_key:
            raise PipelineDefinitionError(f"{where}: 节点缺少 contract.state_key（主状态键）")

        outputs = _as_tuple(raw.get("outputs"), where=f"{where}.outputs")
        if kind in (KIND_AGENT, KIND_STEP) and not outputs:
            raise PipelineDefinitionError(f"{where}: {kind} 节点必须声明 outputs（输出契约）")
        if outputs and state_key not in outputs:
            raise PipelineDefinitionError(
                f"{where}: state_key {state_key!r} 不在 outputs {list(outputs)} 里"
            )

        is_stage = bool(raw.get("is_stage", kind in (KIND_AGENT, KIND_STEP)))
        # 「可编辑」的语义是「产出即交付物」。声明成 true 却又不满足这个前提，
        # 说明作者理解错了字段含义 —— 加载期就纠正，而不是留一个永不生效的开关。
        raw_editable = raw.get("editable")
        if raw_editable is not None and not isinstance(raw_editable, bool):
            raise PipelineDefinitionError(f"{where}: editable 应为布尔值")
        if raw_editable is True and not (is_stage and kind == KIND_AGENT):
            raise PipelineDefinitionError(
                f"{where}: editable: true 只对 Agent 阶段的产出有意义"
                f"（当前 kind={kind!r}, is_stage={is_stage}）。"
                "确定性步骤与人工闸门的产出不是交付物，不能开放人工编辑。"
            )
        # 为什么阶段节点不能改名：阶段的身份键（key）同时是
        # ① DB stages.stage_key ② 前端流水线的 key ③ stage_status 的键，
        # 而**提示词构造器按状态通道名读状态**（build_competitor_messages 里
        # 写死了 state["research"]）。放宽这条，症状会是「改个名字后竞品分析
        # 突然收不到调研结果」这种静默失效 —— 所以宁可在这里拒绝。
        # 要让阶段可改名，得先让提示词构造器也按定义取状态键（另立任务）。
        if is_stage and key != state_key:
            raise PipelineDefinitionError(
                f"{where}: 阶段节点 key({key!r}) 必须等于 state_key({state_key!r})："
                "阶段身份同时用于 DB 阶段行、前端流水线与 stage_status，"
                "且提示词构造器按状态键读上下文（如 state['research']）。"
                "只想换展示名请改 label，不要改 key。"
            )

        try:
            max_retries = int(raw.get("max_retries", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise PipelineDefinitionError(f"{where}: max_retries 应为整数") from exc
        if max_retries < 0:
            raise PipelineDefinitionError(f"{where}: max_retries 不能为负")

        temperature = raw.get("temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError) as exc:
                raise PipelineDefinitionError(f"{where}: temperature 应为数字") from exc

        nodes.append(
            NodeSpec(
                key=key,
                label=_as_str(raw.get("label"), where=f"{where}.label") or key,
                kind=kind,
                factory=factory,
                prompt_key=prompt_key,
                state_key=state_key,
                temperature=temperature,
                max_retries=max_retries,
                is_stage=is_stage,
                editable=raw_editable,
                inputs=_as_tuple(raw.get("inputs"), where=f"{where}.inputs"),
                outputs=outputs,
                description=_as_str(raw.get("description"), where=f"{where}.description"),
            )
        )

    seen_keys = [node.key for node in nodes]
    duplicates = sorted({key for key in seen_keys if seen_keys.count(key) > 1})
    if duplicates:
        raise PipelineDefinitionError(f"{source}: 节点 key 重复：{duplicates}")

    declared = {node.key for node in nodes}

    raw_edges = data.get("edges")
    if not isinstance(raw_edges, (list, tuple)) or not raw_edges:
        raise PipelineDefinitionError(f"{source}: edges 必须是非空列表")

    edges: list[EdgeSpec] = []
    for index, raw in enumerate(raw_edges):
        where = f"{source}: edges[{index}]"
        if not isinstance(raw, Mapping):
            raise PipelineDefinitionError(f"{where} 应为映射")
        source_key = _as_str(raw.get("from"), where=f"{where}.from")
        if not source_key:
            raise PipelineDefinitionError(f"{where} 缺少 from")

        router = _as_str(raw.get("router"), where=f"{where}.router")
        raw_branches = raw.get("branches")

        if router:
            if not isinstance(raw_branches, Mapping) or not raw_branches:
                raise PipelineDefinitionError(
                    f"{where}: 条件边必须提供非空 branches（路由返回值 → 目标节点）"
                )
            mapping = tuple(
                (str(branch).strip(), str(target).strip())
                for branch, target in raw_branches.items()
                if str(branch).strip()
            )
            targets = tuple(target for _, target in mapping)
            edges.append(
                EdgeSpec(
                    source=source_key,
                    targets=targets,
                    router=router,
                    mapping=mapping,
                    max_rounds_source=_as_str(
                        raw.get("max_rounds_source"), where=f"{where}.max_rounds_source"
                    ),
                )
            )
        else:
            target = _as_str(raw.get("to"), where=f"{where}.to")
            if not target:
                raise PipelineDefinitionError(f"{where}: 无条件边必须提供 to（或用 router 提供 branches）")
            edges.append(EdgeSpec(source=source_key, targets=(target,)))

    # 端点必须是已声明节点或图级哨兵
    for edge in edges:
        for endpoint in edge.all_endpoints():
            if endpoint in ENDPOINT_SENTINELS:
                continue
            if endpoint not in declared:
                raise PipelineDefinitionError(
                    f"{source}: 边引用了未声明的节点 {endpoint!r}（已声明：{sorted(declared)}）"
                )

    starts = [edge for edge in edges if edge.source == NODE_START]
    if len(starts) != 1:
        raise PipelineDefinitionError(
            f"{source}: 必须且只能有一条从 START 出发的边，实际 {len(starts)} 条"
        )
    if starts[0].is_conditional:
        raise PipelineDefinitionError(f"{source}: START 出发的边不能是条件边")
    entry = starts[0].targets[0]

    # 可达性：从 START 走一遍，未被访问到的节点是死配置
    seen: set[str] = set()
    frontier = [entry]
    while frontier:
        current = frontier.pop()
        if current in seen or current in ENDPOINT_SENTINELS:
            continue
        seen.add(current)
        for edge in edges:
            if edge.source == current:
                frontier.extend(edge.targets)
    unreachable = sorted(declared - seen)
    if unreachable:
        raise PipelineDefinitionError(
            f"{source}: 节点从 START 不可达（死配置）：{unreachable}"
        )

    # 输入契约：每个被读的键要么是初始状态键，要么有节点产出它
    available = INITIAL_STATE_KEYS | {key for node in nodes for key in node.outputs}
    missing = sorted(key for key in {k for node in nodes for k in node.inputs} if key not in available)
    if missing:
        raise PipelineDefinitionError(
            f"{source}: 以下输入没有任何节点产出，契约不闭合：{missing}"
        )

    human_gate = _as_str(data.get("human_gate"), where="human_gate")
    if not human_gate:
        raise PipelineDefinitionError(f"{source}: 缺少 human_gate（人工闸门节点 key）")
    if human_gate not in declared:
        raise PipelineDefinitionError(
            f"{source}: human_gate {human_gate!r} 不是已声明的节点"
        )
    gate_spec = next(node for node in nodes if node.key == human_gate)
    if not gate_spec.is_human_gate:
        raise PipelineDefinitionError(
            f"{source}: human_gate {human_gate!r} 的 kind 应为 {KIND_HUMAN_GATE!r}，"
            f"实际 {gate_spec.kind!r}"
        )

    # 可人工编辑的上游阶段：**产出即交付物**的 Agent 阶段。
    # 确定性步骤（如守门层）的产出是工具的结论，手改既无意义，
    # 改完也会在下次执行时被覆盖；humanize 这类「产出是处置报告、
    # 真正改的是 copywriting」的 Agent 阶段同理（由 editable: false 声明）。
    # 允许它们就等于允许一个静默失效的编辑入口。
    editable_stages = {node.key for node in nodes if node.is_editable}
    editable = _as_tuple(data.get("upstream_editable_stages"), where="upstream_editable_stages")
    invalid_editable = sorted(key for key in editable if key not in editable_stages)
    if invalid_editable:
        raise PipelineDefinitionError(
            f"{source}: upstream_editable_stages 里不是可编辑的 Agent 阶段：{invalid_editable}"
            f"（可选：{sorted(editable_stages)}）"
        )
    if human_gate in editable:
        raise PipelineDefinitionError(
            f"{source}: 人工闸门 {human_gate!r} 不能出现在 upstream_editable_stages"
        )

    return PipelineDefinition(
        pipeline_id=pipeline_id,
        version=version,
        description=_as_str(data.get("description"), where="description"),
        nodes=tuple(nodes),
        edges=tuple(edges),
        human_gate=human_gate,
        entry=entry,
        success_target=_as_str(data.get("success_target"), where="success_target") or NODE_END,
        failure_target=_as_str(data.get("failure_target"), where="failure_target") or NODE_END,
        upstream_editable_stages=editable,
        source_path=Path(source) if source != "<dict>" else None,
    )


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------
def pipeline_path(pipeline_id: str, *, directory: Path | None = None) -> Path:
    """按 id 在目录里找定义文件（支持 .yaml / .yml）。"""
    base = Path(directory) if directory is not None else PIPELINES_DIR
    for suffix in PIPELINE_SUFFIXES:
        candidate = base / f"{pipeline_id}{suffix}"
        if candidate.is_file():
            return candidate
    raise PipelineDefinitionError(
        f"未找到流水线定义 {pipeline_id!r}（在 {base} 下查找 "
        f"{[pipeline_id + s for s in PIPELINE_SUFFIXES]}）"
    )


def load_pipeline(
    pipeline_id: str = DEFAULT_PIPELINE_ID,
    *,
    directory: Path | None = None,
) -> PipelineDefinition:
    """加载并校验一个流水线定义。

    传入 ``directory`` 可加载仓库外的定义（测试用的临时定义走这条路），
    这样「新增一条流水线」只需要加一个 YAML，不用改任何 Python。
    """
    path = pipeline_path(pipeline_id, directory=directory)
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 依赖 PyYAML 报错文本
        raise PipelineDefinitionError(f"{path}: YAML 解析失败：{exc}") from exc
    return parse_definition(data, source=str(path))


@lru_cache
def load_default_pipeline() -> PipelineDefinition:
    """默认流水线（内置 marketing）。"""
    return load_pipeline(DEFAULT_PIPELINE_ID)


def list_pipelines(*, directory: Path | None = None) -> tuple[str, ...]:
    """列出目录里可用的流水线 id（内置目录 + 可选附加目录）。"""
    base = Path(directory) if directory is not None else PIPELINES_DIR
    if not base.is_dir():
        return ()
    found = {
        path.stem
        for path in base.iterdir()
        if path.is_file() and path.suffix.lower() in PIPELINE_SUFFIXES
    }
    return tuple(sorted(found))


def resolve_factories(
    definition: PipelineDefinition,
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """把每个节点声明的 factory 名解析成真正的实现。

    注册表由组合根（``graph.py``）提供，本模块不 import 任何节点实现。
    未知 factory / 未知 router 一律失败，而不是静默跳过 —— 「节点悄悄不跑了」
    是最难排查的一类故障。

    Returns:
        ``{节点 key: 可调用对象}``，**覆盖全部节点**（含确定性步骤与人工闸门），
        这样组合根可以用同一个循环装配所有节点，不必给不同 kind 写分支。
    """
    resolved: dict[str, Any] = {}
    unknown: list[str] = []
    for node in definition.buildable_nodes():
        factory = registry.get(node.factory)
        if factory is None:
            unknown.append(f"{node.key}({node.factory})")
        else:
            resolved[node.key] = factory
    if unknown:
        raise PipelineDefinitionError(
            f"流水线 {definition.pipeline_id!r} 引用了未知节点工厂：{unknown}；"
            f"可用：{sorted(registry)}"
        )

    for edge in definition.edges:
        if edge.is_conditional and edge.router not in registry:
            raise PipelineDefinitionError(
                f"流水线 {definition.pipeline_id!r} 引用了未知路由 "
                f"{edge.router!r}（来自 {edge.source!r}）；可用：{sorted(registry)}"
            )
    return resolved
