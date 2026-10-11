"""配置化 PipelineDefinition 测试。

两类断言：
1. **行为保持**：内置 marketing 定义必须与改造前的硬编码图逐字等价
   （阶段列表、标签、节点集合、审校回路、上限来源）。
2. **配置错误必须失败**：未知节点、缺契约、死配置、入口不唯一……
   全部在加载期抛错，不能静默放行 —— 「某个 Agent 悄悄不跑了」
   是最难排查的一类故障。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.agents.graph import NODE_BUILDERS, ROUTERS, build_graph, pipeline_definition_of
from app.agents.nodes import STAGE_KEYS, STAGE_LABELS
from app.agents.pipeline_definition import (
    DEFAULT_PIPELINE_ID,
    KIND_AGENT,
    KIND_HUMAN_GATE,
    KIND_STEP,
    PipelineDefinitionError,
    list_pipelines,
    load_default_pipeline,
    load_pipeline,
    parse_definition,
    resolve_factories,
)
from app.llm import LLMResult

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "pipelines"


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------
class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    def chat(self, messages, **kwargs):
        content = self.responses[self.calls]
        self.calls += 1
        return LLMResult(
            content=content, model="fake", prompt_tokens=100,
            completion_tokens=50, latency_seconds=0.1,
        )


RESEARCH = json.dumps(
    {"summary": "s", "trends": ["t"], "audience_insights": ["a"],
     "pain_points": ["p"], "keywords": ["k"]}, ensure_ascii=False)
COMPETITOR = json.dumps(
    {"competitors": [{"name": "C", "positioning": "p", "selling_points": ["s"],
                      "content_strategy": "c", "strengths": [], "weaknesses": []}],
     "gaps": ["g"]}, ensure_ascii=False)
STRATEGY = json.dumps(
    {"target_audience": "白领", "core_value_proposition": "v", "key_messages": ["m"],
     "tone_guidance": "g", "content_angles": ["a"], "channel_strategy": "s"},
    ensure_ascii=False)
COPY = json.dumps(
    {"variants": [{"title": "标题", "body": "正文", "hashtags": [], "notes": "n"}]},
    ensure_ascii=False)
REVIEW_PASS = json.dumps({"passed": True, "feedback": "", "checklist": []},
                         ensure_ascii=False)


def _initial_state(task_id: int = 1) -> dict:
    return {
        "task_id": task_id,
        "input": {
            "topic": "智能保温杯",
            "brand_name": "暖芯",
            "target_audience": "25-35 都市白领",
            "channel_id": "xiaohongshu",
            "extra_requirements": "",
        },
    }


def _minimal(**overrides) -> dict:
    """一份合法的最小定义；各失败用例只改动其中一处。

    注意阶段节点必须 key == state_key（见 test_stage_node_key_must_equal_state_key）。
    """
    data: dict = {
        "pipeline_id": "demo",
        "version": 1,
        "human_gate": "gate",
        "nodes": [
            {"key": "first", "kind": KIND_AGENT,
             "contract": {"factory": "f1", "prompt_key": "p1", "state_key": "first"},
             "inputs": ["input"], "outputs": ["first"]},
            {"key": "gate", "kind": KIND_HUMAN_GATE,
             "contract": {"factory": "g", "state_key": "confirmed"},
             "inputs": ["first"], "outputs": ["confirmed"]},
        ],
        "edges": [
            {"from": "START", "to": "first"},
            {"from": "first", "to": "gate"},
            {"from": "gate", "to": "END"},
        ],
    }
    data.update(overrides)
    return data


# ==========================================================================
# 一、内置 marketing 定义复刻了原硬编码图
# ==========================================================================
def test_default_pipeline_is_marketing():
    assert DEFAULT_PIPELINE_ID == "marketing"
    assert load_default_pipeline().pipeline_id == "marketing"
    assert "marketing" in list_pipelines()


def test_marketing_declares_eight_logical_nodes_but_seven_stages():
    """八个逻辑节点 = 6 个 Agent 阶段 + 1 个确定性守门阶段 + 1 个人工闸门。

    P3 起多了 guardrail（``kind: step``），P4 起多了 humanize（``kind: agent``，
    仅公众号实际调用模型）—— 两次都是**预期改动**，不是行为回归。
    """
    definition = load_default_pipeline()
    assert definition.node_keys() == (
        "research", "competitor", "strategy", "copywriting",
        "humanize", "guardrail", "review", "human_confirm",
    )
    assert definition.stage_keys() == (
        "research", "competitor", "strategy", "copywriting",
        "humanize", "guardrail", "review",
    )
    # 人工闸门不是阶段，守门层是阶段但不是 Agent
    assert [node.key for node in definition.agent_nodes()] == [
        "research", "competitor", "strategy", "copywriting", "humanize", "review",
    ]
    assert [node.key for node in definition.step_nodes()] == ["guardrail"]
    assert definition.human_gate == "human_confirm"


def test_guardrail_is_a_deterministic_step_not_an_agent():
    """守门层不调模型：它必须是 step，不能混进 agent 集合。

    混淆的代价很具体——成本统计会多算一份、prompt 契约会被要求写、
    「同输入必同输出」这个可测试性保证也就没了。
    """
    node = load_default_pipeline().node("guardrail")
    assert node.kind == KIND_STEP
    assert node.is_step and not node.is_agent and not node.calls_llm
    assert node.prompt_key == "", "确定性步骤不应声明提示词契约"
    assert node.state_key == "guardrail"
    assert node.is_stage, "守门报告要单独落库/展示，所以是阶段"
    assert node.max_retries == 0


def test_stage_keys_still_match_the_frontend_contract():
    """前端流水线视图依赖这个顺序与集合（Pipeline.tsx 的 STAGES 数组）。

    这是**字面量**断言（不是拿 STAGE_KEYS 去比 STAGE_KEYS）——
    它才是「对外契约此刻长什么样」的证据。
    """
    assert STAGE_KEYS == [
        "research", "competitor", "strategy", "copywriting",
        "humanize", "guardrail", "review",
    ]
    assert STAGE_LABELS == {
        "research": "市场调研",
        "competitor": "竞品分析",
        "strategy": "策略制定",
        "copywriting": "文案创作",
        "humanize": "去 AI 味",
        "guardrail": "质量守门",
        "review": "审校优化",
    }
    assert list(load_default_pipeline().stage_keys()) == STAGE_KEYS
    assert load_default_pipeline().labels()["human_confirm"] == "人工确认"


def test_marketing_entry_and_terminals():
    definition = load_default_pipeline()
    assert definition.entry == "research"
    assert definition.success_target == "END"
    assert definition.failure_target == "END"


def test_marketing_upstream_editable_stages_match_runner_contract():
    """runner 认为「可编辑上游」是这三段，定义里必须声明成同一份。"""
    assert load_default_pipeline().upstream_editable_stages == (
        "research", "competitor", "strategy",
    )


def test_every_agent_node_declares_factory_prompt_and_outputs():
    for node in load_default_pipeline().agent_nodes():
        assert node.factory, f"{node.key} 缺 factory"
        assert node.prompt_key, f"{node.key} 缺 prompt_key"
        assert node.outputs, f"{node.key} 缺 outputs"
        assert node.max_retries == 0, "P2 行为保持：内置流水线不允许节点级重试"
    # 温度与改造前硬编码值一致（humanize 是 P4 新增，0.4 为改写任务的低温度）
    temperatures = {node.key: node.temperature for node in load_default_pipeline().agent_nodes()}
    assert temperatures == {
        "research": 0.5, "competitor": 0.5, "strategy": 0.5,
        "copywriting": 0.8, "humanize": 0.4, "review": 0.3,
    }


def test_review_loop_is_declared_as_a_conditional_edge():
    definition = load_default_pipeline()
    loop_edges = [edge for edge in definition.edges if edge.is_conditional]
    assert len(loop_edges) == 1
    edge = loop_edges[0]
    assert edge.source == "review"
    assert edge.router == "route_after_review"
    assert dict(edge.mapping) == {"copywriting": "copywriting", "human_confirm": "human_confirm"}


def test_review_round_limit_declares_its_source_instead_of_a_magic_number():
    """轮数上限由 Settings 掌管（测试会 monkeypatch 它），YAML 只声明出处。"""
    edge = next(edge for edge in load_default_pipeline().edges if edge.is_conditional)
    assert edge.max_rounds_source == "settings.max_revision_rounds"
    assert not any(ch.isdigit() for ch in edge.max_rounds_source)


def test_definition_round_trips_to_dict():
    payload = load_default_pipeline().to_dict()
    assert payload["pipeline_id"] == "marketing"
    assert payload["version"] == 3  # P3 +guardrail、P4 +humanize，版本随之递增
    assert len(payload["nodes"]) == 8
    assert len(payload["edges"]) == 9
    # 条件边在 dict 里带 router/mapping，无条件边带 target
    conditional = [edge for edge in payload["edges"] if "router" in edge]
    assert len(conditional) == 1
    assert json.dumps(payload, ensure_ascii=False)  # 必须可 JSON 序列化（进 API 用）


# ==========================================================================
# 二、行为保持：省略 pipeline_id 与显式指定结果一致
# ==========================================================================
def test_build_graph_default_matches_explicit_marketing(tmp_path):
    fake_default = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS])
    fake_explicit = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS])

    graph_default = build_graph(fake_default, db_path=tmp_path / "a.db")
    graph_explicit = build_graph(
        fake_explicit, db_path=tmp_path / "b.db", pipeline_id="marketing"
    )

    state = _initial_state()
    result_default = graph_default.invoke(
        state, {"configurable": {"thread_id": "task-1"}}
    )
    result_explicit = graph_explicit.invoke(
        state, {"configurable": {"thread_id": "task-1"}}
    )

    assert result_default["stage_status"] == result_explicit["stage_status"]
    assert result_default["stage_status"] == {
        "research": "completed", "competitor": "completed", "strategy": "completed",
        "copywriting": "completed", "humanize": "skipped", "guardrail": "completed",
        "review": "completed",
    }
    assert result_default["copywriting"] == result_explicit["copywriting"]
    assert "__interrupt__" in result_default and "__interrupt__" in result_explicit


def test_compiled_graph_nodes_match_the_definition(tmp_path):
    fake = FakeLLM([RESEARCH])
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")
    definition = pipeline_definition_of(graph)
    assert definition is not None
    nodes = set(graph.get_graph().nodes)
    assert set(definition.node_keys()) <= nodes


def test_definition_is_attached_to_the_graph_for_the_runner(tmp_path):
    graph = build_graph(FakeLLM([RESEARCH]), db_path=tmp_path / "ckpt.db")
    assert pipeline_definition_of(graph).pipeline_id == "marketing"
    # 没有挂定义的图（老代码/第三方构造）必须安全返回 None
    assert pipeline_definition_of(object()) is None


# ==========================================================================
# 三、配置错误必须失败
# ==========================================================================
@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d.pop("pipeline_id"), "缺少 pipeline_id"),
        (lambda d: d.update(pipeline_id="Bad-Id"), "非法"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.update(nodes=[]), "nodes"),
        (lambda d: d.update(edges=[]), "edges"),
        (lambda d: d.update(edges=None), "edges"),
    ],
)
def test_structural_requirements_are_enforced(mutate, expected):
    data = _minimal()
    mutate(data)
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert expected in str(excinfo.value)


def test_unknown_node_in_edge_fails():
    data = _minimal(edges=[
        {"from": "START", "to": "first"},
        {"from": "first", "to": "nope"},
        {"from": "gate", "to": "END"},
    ])
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "nope" in str(excinfo.value)


def test_unknown_factory_fails_at_resolution():
    definition = parse_definition(_minimal())
    with pytest.raises(PipelineDefinitionError) as excinfo:
        resolve_factories(definition, {"g": object()})
    assert "未知节点工厂" in str(excinfo.value)
    assert "f1" in str(excinfo.value)


def test_unknown_router_fails_at_resolution():
    data = _minimal(edges=[
        {"from": "START", "to": "first"},
        {"from": "first", "to": "gate"},
        {"from": "gate", "router": "no_such_router",
         "branches": {"gate": "gate"}},
    ])
    definition = parse_definition(data)
    with pytest.raises(PipelineDefinitionError) as excinfo:
        resolve_factories(definition, {"f1": object(), "g": object()})
    assert "未知路由" in str(excinfo.value)


def test_missing_factory_fails():
    data = _minimal()
    data["nodes"][0]["contract"] = {"prompt_key": "p1"}
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "factory" in str(excinfo.value)


def test_missing_prompt_key_on_agent_node_fails():
    data = _minimal()
    data["nodes"][0]["contract"] = {"factory": "f1"}
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "prompt_key" in str(excinfo.value)


def test_agent_node_without_outputs_fails():
    data = _minimal()
    data["nodes"][0]["outputs"] = []
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "outputs" in str(excinfo.value)


# ---- kind: step（确定性步骤）的契约 ----
def _with_step(**contract_overrides) -> dict:
    """在最小定义里插一个 step 节点（避免手写重复结构）。"""
    data = _minimal()
    step = {
        "key": "lint", "kind": KIND_STEP, "is_stage": True,
        "contract": {"factory": "s1", "state_key": "lint"},
        "inputs": ["first"], "outputs": ["lint"],
    }
    step["contract"].update(contract_overrides)
    data["nodes"].insert(1, step)
    data["edges"] = [
        {"from": "START", "to": "first"},
        {"from": "first", "to": "lint"},
        {"from": "lint", "to": "gate"},
        {"from": "gate", "to": "END"},
    ]
    return data


def test_step_node_is_accepted_and_needs_no_prompt_contract():
    definition = parse_definition(_with_step())
    node = definition.node("lint")
    assert node.is_step and not node.is_agent
    assert definition.step_nodes() == (node,)


def test_step_node_without_factory_fails():
    data = _with_step()
    data["nodes"][1]["contract"] = {"state_key": "lint"}
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "factory" in str(excinfo.value)


def test_step_node_with_prompt_key_fails():
    """确定性步骤不调模型；写了 prompt_key 说明作者理解错了，必须当场纠正。"""
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(_with_step(prompt_key="lint_prompt"))
    assert "不应声明" in str(excinfo.value)


def test_step_node_without_outputs_fails():
    data = _with_step()
    data["nodes"][1]["outputs"] = []
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "outputs" in str(excinfo.value)


def test_step_stage_cannot_be_listed_as_editable_upstream():
    """工具结论不该开放人工编辑：改了也会被下次执行覆盖。"""
    data = _with_step()
    data["upstream_editable_stages"] = ["lint"]
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "Agent 阶段" in str(excinfo.value)


# --------------------------------------------------------------------------
# editable：判据是「产出即交付物」，不是「是不是 Agent」
# --------------------------------------------------------------------------
def test_agent_stage_is_editable_by_default():
    assert parse_definition(_minimal()).node("first").is_editable is True


def test_editable_false_removes_an_agent_stage_from_the_editing_surface():
    """P4 新增：有些 Agent 阶段的产出**不是**交付物（如 humanize 的处置报告，
    真正生效的是它回写的 copywriting）。开放编辑入口只会得到一个
    「存了但不会回填」的静默失效编辑 —— 所以必须能显式关掉。"""
    data = _minimal()
    data["nodes"][0]["editable"] = False
    node = parse_definition(data).node("first")
    assert node.is_agent and node.is_stage
    assert node.is_editable is False


def test_editable_false_stage_cannot_be_listed_as_editable_upstream():
    data = _minimal(upstream_editable_stages=["first"])
    data["nodes"][0]["editable"] = False
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "upstream_editable_stages" in str(excinfo.value)


def test_editable_true_on_a_step_is_rejected():
    """声明 true 却不满足前提 = 作者理解错了字段含义，加载期就纠正。"""
    data = _with_step()
    data["nodes"][1]["editable"] = True
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "editable" in str(excinfo.value)


def test_editable_true_on_a_non_stage_is_rejected():
    data = _minimal()
    data["nodes"][0]["is_stage"] = False
    data["nodes"][0]["editable"] = True
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "editable" in str(excinfo.value)


def test_editable_must_be_a_boolean():
    data = _minimal()
    data["nodes"][0]["editable"] = "yes"
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "布尔" in str(excinfo.value)


def test_non_stage_step_may_write_a_different_state_key():
    """非阶段节点不参与 stage_status/DB 阶段行，所以 key 与 state_key 可以不同。

    阶段节点的相等约束是为了 DB/前端/stage_status 的一致；非阶段没这个包袱。
    """
    data = _with_step()
    data["nodes"][1]["is_stage"] = False
    data["nodes"][1]["contract"]["state_key"] = "lint_result"
    data["nodes"][1]["outputs"] = ["lint_result"]
    definition = parse_definition(data)
    assert definition.node("lint").state_key == "lint_result"
    assert "lint" not in definition.stage_keys()


def test_duplicate_node_keys_fail():
    data = _minimal()
    data["nodes"].append(copy.deepcopy(data["nodes"][0]))
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "重复" in str(excinfo.value)


def test_unknown_kind_fails():
    data = _minimal()
    data["nodes"][0]["kind"] = "robot"
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "kind" in str(excinfo.value)


def test_missing_start_edge_fails():
    data = _minimal(edges=[
        {"from": "first", "to": "gate"},
        {"from": "gate", "to": "END"},
    ])
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "START" in str(excinfo.value)


def test_two_start_edges_fail():
    data = _minimal(edges=[
        {"from": "START", "to": "first"},
        {"from": "START", "to": "gate"},
        {"from": "gate", "to": "END"},
    ])
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "只能有一条" in str(excinfo.value)


def test_unreachable_node_fails():
    """从 START 走不到的节点是死配置，必须报错而不是安静地不执行。"""
    data = _minimal()
    data["nodes"].append({
        "key": "orphan", "kind": KIND_AGENT,
        "contract": {"factory": "f2", "prompt_key": "p2", "state_key": "orphan"},
        "inputs": ["input"], "outputs": ["orphan"],
    })
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "不可达" in str(excinfo.value)


def test_input_without_any_producer_fails():
    """契约不闭合：读了没人产出的状态键。"""
    data = _minimal()
    data["nodes"][1]["inputs"] = ["first", "ghost"]
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "ghost" in str(excinfo.value)


def test_loop_back_input_is_allowed_when_someone_produces_it():
    """审校回路：某节点读一个由**下游**节点产出的键，是合法的回边输入。

    「输入必须有人产出」的校验只要求**存在**产出者，不要求先于本节点 ——
    否则 marketing 的 review→copywriting 回路就会被误判为契约错误。
    """
    data = _minimal()
    data["nodes"] = [
        {"key": "first", "kind": KIND_AGENT,
         "contract": {"factory": "f1", "prompt_key": "p1", "state_key": "first"},
         "inputs": ["input"], "outputs": ["first"]},
        # draft 读 feedback —— feedback 由后面的 feedback 节点产出（回边）
        {"key": "draft", "kind": KIND_AGENT,
         "contract": {"factory": "f2", "prompt_key": "p2", "state_key": "draft"},
         "inputs": ["first", "feedback"], "outputs": ["draft"]},
        {"key": "feedback", "kind": KIND_AGENT,
         "contract": {"factory": "f3", "prompt_key": "p3", "state_key": "feedback"},
         "inputs": ["draft"], "outputs": ["feedback"]},
        {"key": "gate", "kind": KIND_HUMAN_GATE,
         "contract": {"factory": "g", "state_key": "confirmed"},
         "inputs": ["draft"], "outputs": ["confirmed"]},
    ]
    data["edges"] = [
        {"from": "START", "to": "first"},
        {"from": "first", "to": "draft"},
        {"from": "draft", "to": "feedback"},
        {"from": "feedback", "router": "r",
         "branches": {"again": "draft", "done": "gate"}},
        {"from": "gate", "to": "END"},
    ]
    definition = parse_definition(data)
    assert definition.node("draft").inputs == ("first", "feedback")
    assert definition.node("feedback").key == definition.node("feedback").state_key


def test_loop_back_to_an_unproduced_key_still_fails():
    """回边不是免检通道：没人产出的键依旧要报错。"""
    data = _minimal()
    data["nodes"][1]["inputs"] = ["first", "ghost"]
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "ghost" in str(excinfo.value)


# ---- state_key 绑定（把隐式耦合变成显式契约）----
def test_agent_node_without_state_key_fails():
    data = _minimal()
    del data["nodes"][0]["contract"]["state_key"]
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "state_key" in str(excinfo.value)


def test_state_key_must_appear_in_outputs():
    data = _minimal()
    data["nodes"][0]["contract"]["state_key"] = "something_else"
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "不在 outputs" in str(excinfo.value)


def test_stage_node_key_must_equal_state_key():
    """已知约束：阶段节点不能改名（节点实现把状态键硬编码在代码里）。

    这条校验把「改个名字就让 DB 阶段行和前端流水线错位」这类
    静默故障，变成加载期的一句明确报错。
    """
    data = _minimal()
    assert parse_definition(data).stage_keys() == ("first",)  # 合法：key == state_key

    data["nodes"][0]["key"] = "renamed"        # 只改 key，不动 state_key
    data["edges"] = [
        {"from": "START", "to": "renamed"},
        {"from": "renamed", "to": "gate"},
        {"from": "gate", "to": "END"},
    ]
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "必须等于 state_key" in str(excinfo.value)


def test_human_gate_may_use_a_different_state_key():
    """闸门不是阶段，所以 key（human_confirm）与 state_key（confirmed）可以不同。"""
    node = load_default_pipeline().node("human_confirm")
    assert node.key == "human_confirm"
    assert node.state_key == "confirmed"


def test_every_stage_node_in_bundled_definitions_has_key_equal_to_state_key():
    for definition in (load_default_pipeline(),
                       load_pipeline("seo_rewrite", directory=FIXTURE_DIR)):
        for node in definition.stage_nodes():
            assert node.key == node.state_key, f"{definition.pipeline_id}/{node.key}"


def test_missing_human_gate_fails():
    data = _minimal()
    data.pop("human_gate")
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "human_gate" in str(excinfo.value)


def test_human_gate_pointing_at_unknown_node_fails():
    data = _minimal(human_gate="ghost")
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "human_gate" in str(excinfo.value)


def test_human_gate_with_wrong_kind_fails():
    """闸门节点必须声明 kind: human_gate，否则 interrupt 语义无处安放。"""
    data = _minimal(human_gate="first")
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert KIND_HUMAN_GATE in str(excinfo.value)


def test_conditional_edge_without_branches_fails():
    data = _minimal(edges=[
        {"from": "START", "to": "first"},
        {"from": "first", "router": "r"},
        {"from": "gate", "to": "END"},
    ])
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "branches" in str(excinfo.value)


def test_plain_edge_needs_a_target():
    data = _minimal(edges=[
        {"from": "START", "to": "first"},
        {"from": "first"},
        {"from": "gate", "to": "END"},
    ])
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "to" in str(excinfo.value)


def test_upstream_editable_stage_must_be_a_stage():
    data = _minimal(upstream_editable_stages=["gate"])
    with pytest.raises(PipelineDefinitionError) as excinfo:
        parse_definition(data)
    assert "upstream_editable_stages" in str(excinfo.value)


def test_non_mapping_top_level_fails():
    with pytest.raises(PipelineDefinitionError):
        parse_definition(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_unknown_pipeline_id_fails():
    with pytest.raises(PipelineDefinitionError) as excinfo:
        load_pipeline("no_such_pipeline")
    assert "未找到流水线定义" in str(excinfo.value)


# ==========================================================================
# 四、新增一条流水线不需要改任何 Python
# ==========================================================================
def test_extra_definition_is_discovered_in_a_custom_directory():
    assert "seo_rewrite" in list_pipelines(directory=FIXTURE_DIR)


def test_seo_rewrite_definition_loads_and_differs_from_marketing():
    seo = load_pipeline("seo_rewrite", directory=FIXTURE_DIR)
    marketing = load_default_pipeline()
    assert seo.pipeline_id == "seo_rewrite"
    assert seo.stage_keys() == ("research", "copywriting")
    assert seo.stage_keys() != marketing.stage_keys()
    # 阶段少了三个，标签也不同
    assert seo.labels()["research"] == "关键词调研"
    assert seo.human_gate == "seo_gate"
    assert seo.upstream_editable_stages == ("research",)
    # 复用现有节点实现 —— 只迁移元数据，不新增节点函数
    assert {node.factory for node in seo.agent_nodes()} <= set(NODE_BUILDERS)
    assert seo.entry == "research"


def test_guardrail_is_opt_in_per_definition():
    """守门层是按定义启用的**可选**节点，不是无条件挂在图上。

    seo_rewrite 没声明它，因此那条流水线不跑守门——这既是"新定义只加 YAML"
    的又一处证明，也把「未启用守门层时链路与改造前一致」变成可断言的事实。
    """
    seo = load_pipeline("seo_rewrite", directory=FIXTURE_DIR)
    assert "guardrail" not in seo.node_keys()
    assert seo.step_nodes() == ()
    assert "guardrail" not in seo.stage_keys()


def test_seo_rewrite_builds_its_own_graph_without_touching_runner(tmp_path):
    fake = FakeLLM([RESEARCH, COPY])
    graph = build_graph(fake, db_path=tmp_path / "seo.db", pipelines_dir=FIXTURE_DIR,
                        pipeline_id="seo_rewrite")
    definition = pipeline_definition_of(graph)
    assert definition.pipeline_id == "seo_rewrite"
    assert set(definition.node_keys()) <= set(graph.get_graph().nodes)

    result = graph.invoke(_initial_state(), {"configurable": {"thread_id": "task-9"}})
    assert "__interrupt__" in result
    assert result["stage_status"] == {
        "research": "completed", "copywriting": "completed",
    }
    assert fake.calls == 2  # 定义里只有两个 agent 节点


def test_seo_rewrite_stage_nodes_are_a_strict_subset_of_marketing():
    """两套定义的差别只在**阶段集合**，证明阶段确实由配置决定。"""
    marketing = set(load_default_pipeline().stage_keys())
    seo = set(load_pipeline("seo_rewrite", directory=FIXTURE_DIR).stage_keys())
    assert seo == {"research", "copywriting"}
    assert seo < marketing
    # marketing 多出的阶段：两个分析 + 去 AI 味 + 守门 + 审校
    assert marketing - seo == {
        "competitor", "strategy", "humanize", "guardrail", "review",
    }


def test_runner_stage_keys_follow_the_graph_definition():
    """runner 的阶段集合来自图所挂的定义，不是模块常量。

    这是「新增定义无需修改 runner.py」的核心证据。
    """
    from app.agents.graph import definition_editable_stages, definition_stage_keys

    class _Graph:
        def __init__(self, definition):
            self._pipeline_definition = definition

    marketing = _Graph(load_default_pipeline())
    seo = _Graph(load_pipeline("seo_rewrite", directory=FIXTURE_DIR))
    assert definition_stage_keys(marketing) == [
        "research", "competitor", "strategy", "copywriting",
        "humanize", "guardrail", "review",
    ]
    assert definition_stage_keys(seo) == ["research", "copywriting"]
    assert definition_editable_stages(seo) == ["research"]
    assert definition_editable_stages(marketing) == ["research", "competitor", "strategy"]
    # 没有挂定义的图回落模块常量，老行为不变
    assert definition_stage_keys(object()) == STAGE_KEYS


def test_editable_stages_exclude_deterministic_steps():
    """可编辑阶段 = 「产出即交付物」的阶段。

    守门层（确定性步骤）与 humanize（产出是处置报告、真正改的是 copywriting）
    都不开放手改 —— 前者的结论手改无意义且会被覆盖，后者的编辑根本不会回填。
    """
    from app.agents.graph import definition_editable_stage_set

    class _Graph:
        def __init__(self, definition):
            self._pipeline_definition = definition

    editable = definition_editable_stage_set(_Graph(load_default_pipeline()))
    assert editable == {"research", "competitor", "strategy", "copywriting", "review"}
    assert "guardrail" not in editable
    assert "humanize" not in editable
    # 拿不到定义时 fail closed：无法判断哪些是 Agent 阶段，就都不许改
    assert definition_editable_stage_set(object()) == set()


def test_registry_covers_every_factory_declared_by_bundled_definitions(tmp_path):
    """内置定义的 factory / router 必须在注册表里，否则构建期就会失败。"""
    registry = {**NODE_BUILDERS, **ROUTERS}
    for definition in (load_default_pipeline(),
                       load_pipeline("seo_rewrite", directory=FIXTURE_DIR)):
        resolve_factories(definition, registry)


def test_node_level_retry_wrapper_is_a_passthrough_when_zero():
    from app.agents.graph import _with_retries

    calls = {"n": 0}

    def node(state):
        calls["n"] += 1
        return {"ok": True}

    assert _with_retries(node, 0) is node  # 零重试 = 原样返回，零行为差异
    assert _with_retries(node, 0)({}) == {"ok": True}


def test_node_level_retry_recovers_from_a_transient_failure():
    from app.agents.graph import _with_retries

    calls = {"n": 0}

    def flaky(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("瞬时故障")
        return {"ok": True}

    assert _with_retries(flaky, 1)({}) == {"ok": True}
    assert calls["n"] == 2


def test_node_level_retry_reraises_after_exhausting_attempts():
    from app.agents.graph import _with_retries

    def always_fails(state):
        raise RuntimeError("稳定故障")

    with pytest.raises(RuntimeError, match="稳定故障"):
        _with_retries(always_fails, 2)({})
