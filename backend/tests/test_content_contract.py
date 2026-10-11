"""内容契约核验测试（P6）。

这一层存在的理由：``channels.yaml`` 的 ``copywriting_guide`` 把渠道方法论写得很细，
但它**只是提示词里的一段话** —— 没有任何机制检查模型有没有照做。
「写了规则」和「执行了规则」之间那条缝，就是内容质量的漏损点。

本文件守三件事：

1. **核验逻辑正确**：五种 kind 各自该报就报、该放过就放过；
2. **边界不被越过**：契约缺口只**标记**，不改变 ``passed``（硬合规才决定它），
   也不静默补全内容；
3. **契约与提示词不漂移**：提示词里要求的字段，必须在契约里有对应核验项。
   这条才是最有价值的一条 —— 它把「改了 prompts 忘了改契约」变成红灯。
"""
from __future__ import annotations

import json

import pytest

from app.agents.prompts import build_copywriting_messages
from app.guardrails import run_guardrail
from app.guardrails.content_contract import (
    FEED_TARGETS,
    KNOWN_KINDS,
    check_content_contract,
    contract_from_channel,
    flatten_tiered_tags,
    gaps_of,
    tag_source_field,
)
from app.templates import get_channel, list_channels

ALL_CHANNELS = ("xiaohongshu", "wechat", "weibo", "product_page")

# 恰好 19 字，落在小红书 18-20 区间内（否则标题判定会额外产生 needs_human）
TITLE_XHS_OK = "冬季通勤保温杯实测：30天后的真实感受"


def _channel(**overrides) -> dict:
    """最小可用渠道配置。"""
    base = {"name": "测试渠道", "max_chars": 1000}
    base.update(overrides)
    return base


def _codes(checks) -> dict[str, bool]:
    return {check.field: check.ok for check in checks}


# ==========================================================================
# 一、不核验的边界：没有要求就没有缺口
# ==========================================================================
def test_channel_without_contract_is_not_checked():
    """未声明 content_contract ≠ 全部通过，而是**不核验**。

    与守门参数的「缺省推断」刻意不同：契约是「要求」，凭空推断出要求
    只会让没写配置的渠道莫名其妙地报缺口。
    """
    assert contract_from_channel(_channel()) == []
    assert check_content_contract({"title": "t", "body": "b"}, _channel()) == []
    assert tag_source_field(_channel()) is None


def test_declared_contract_is_returned_as_copies():
    """返回副本而不是原对象，避免调用方改到配置缓存（``load_channel_templates`` 有 lru_cache）。"""
    channel = get_channel("xiaohongshu")
    entries = contract_from_channel(channel)
    entries[0]["label"] = "被改坏了"
    assert contract_from_channel(channel)[0]["label"] != "被改坏了"


# ==========================================================================
# 二、五种 kind 的核验行为
# ==========================================================================
def test_text_kind_requires_non_empty_string():
    channel = _channel(content_contract=[{"field": "cta", "kind": "text", "label": "行动号召"}])

    assert check_content_contract({"cta": "先收藏"}, channel)[0].ok is True
    for bad in (None, "", "   ", 123, []):
        check = check_content_contract({"cta": bad}, channel)[0]
        assert check.ok is False, f"{bad!r} 应被判为缺口"
        assert "缺失" in check.detail


def test_enum_kind_rejects_values_outside_the_list():
    channel = _channel(content_contract=[{
        "field": "hook_type", "kind": "enum", "label": "钩子", "values": ["场景痛点", "反常识"],
    }])

    assert check_content_contract({"hook_type": "场景痛点"}, channel)[0].ok is True
    outside = check_content_contract({"hook_type": "随便写的"}, channel)[0]
    assert outside.ok is False
    assert "不在允许集合" in outside.detail
    # 缺失也要报，且告诉模型可选项 —— 否则模型不知道要补什么
    missing = check_content_contract({}, channel)[0]
    assert missing.ok is False and "场景痛点" in missing.detail


def test_list_kind_checks_count_bounds():
    channel = _channel(content_contract=[{
        "field": "selling_points", "kind": "list", "label": "卖点", "min": 3, "max": 5,
    }])

    assert check_content_contract({"selling_points": ["a", "b", "c"]}, channel)[0].ok is True
    assert check_content_contract({"selling_points": ["a", "b"]}, channel)[0].ok is False
    assert check_content_contract({"selling_points": ["a"] * 6}, channel)[0].ok is False
    assert check_content_contract({"selling_points": "不是数组"}, channel)[0].ok is False


def test_list_kind_checks_item_fields():
    """产品页卖点是 FAB 四段式：少一段就是「没写全」，必须报出来。"""
    channel = _channel(content_contract=[{
        "field": "selling_points", "kind": "list", "label": "卖点", "min": 1,
        "item_fields": ["label", "benefit", "proof"],
    }])
    good = [{"label": "静音", "benefit": "不打扰同事", "proof": "来自任务输入"}]
    bad = [{"label": "静音", "benefit": "不打扰同事"}]  # 缺 proof

    assert check_content_contract({"selling_points": good}, channel)[0].ok is True
    check = check_content_contract({"selling_points": bad}, channel)[0]
    assert check.ok is False
    assert "第 1 项" in check.detail and "proof" in check.detail


def test_empty_item_field_value_counts_as_missing():
    channel = _channel(content_contract=[{
        "field": "selling_points", "kind": "list", "label": "卖点", "min": 1,
        "item_fields": ["proof"],
    }])

    check = check_content_contract({"selling_points": [{"proof": "  "}]}, channel)[0]
    assert check.ok is False


def test_tiered_kind_reports_each_tier_separately():
    channel = _channel(content_contract=[{
        "field": "tags_by_tier", "kind": "tiered", "label": "分层标签",
        "tiers": [
            {"key": "hot", "label": "热门大标签", "min": 2, "max": 3},
            {"key": "longtail", "label": "精准长尾标签", "min": 3, "max": 5},
        ],
    }])

    ok = {"tags_by_tier": {"hot": ["a", "b"], "longtail": ["c", "d", "e"]}}
    assert check_content_contract(ok, channel)[0].ok is True

    thin = {"tags_by_tier": {"hot": ["a"], "longtail": ["c", "d", "e"]}}
    check = check_content_contract(thin, channel)[0]
    assert check.ok is False
    assert "热门大标签" in check.detail and "精准长尾标签" not in check.detail

    # 整块缺失
    assert check_content_contract({}, channel)[0].ok is False


def test_headings_kind_requires_headings_to_appear_in_body():
    """「大纲写了、正文没写」正是这条契约要抓的东西。"""
    channel = _channel(content_contract=[{
        "field": "section_headings", "kind": "headings", "label": "小节标题", "min": 2,
    }])
    body = "一、先明确场景\n内容 A\n\n二、再核对信息\n内容 B"

    assert check_content_contract(
        {"section_headings": ["一、先明确场景", "二、再核对信息"], "body": body}, channel
    )[0].ok is True

    check = check_content_contract(
        {"section_headings": ["一、先明确场景", "三、根本没写"], "body": body}, channel
    )[0]
    assert check.ok is False
    assert "三、根本没写" in check.detail

    # 数量不足也要报
    assert check_content_contract(
        {"section_headings": ["一、先明确场景"], "body": body}, channel
    )[0].ok is False


def test_headings_match_survives_body_reflow():
    """守门层归一化会重排正文换行，标题匹配不能因此失效。

    ``downgrade_body`` 逐句以 ``\\n`` 连接，所以「小标题是否出现在正文里」
    必须去空白后再比 —— 否则降档过的正文会被误报成「标题不在正文中」。
    """
    channel = _channel(content_contract=[{
        "field": "section_headings", "kind": "headings", "label": "小节标题", "min": 1,
    }])

    check = check_content_contract(
        {"section_headings": ["一、 先明确场景"], "body": "开头。\n一、先明确\n场景\n后面还有。"},
        channel,
    )[0]
    assert check.ok is True


def test_unknown_kind_fails_loudly():
    """未知 kind 必须报错而不是静默跳过 —— 配置写错也该是红灯。"""
    channel = _channel(content_contract=[{"field": "x", "kind": "typo", "label": "写错了"}])

    check = check_content_contract({"x": "任意值"}, channel)[0]
    assert check.ok is False
    assert "未知的契约 kind" in check.detail


# ==========================================================================
# 三、边界：只标记，不改判、不补内容
# ==========================================================================
def _xhs_full_variant(**overrides) -> dict:
    variant = {
        "title": TITLE_XHS_OK,
        "body": "冬天到了，谁不想喝口热的。",
        "hashtags": [],
        "hook_type": "场景痛点",
        "scenario": "地铁上水早就凉了",
        "experience_detail": "早上装的水到晚上还是烫的",
        "selling_points": ["长效保温", "单手开盖", "密封防漏"],
        "tags_by_tier": {
            "hot": ["保温杯推荐", "通勤好物"],
            "longtail": ["秋冬必备", "真实体验", "好物分享"],
        },
        "cta": "先收藏，需要的时候再翻出来对照",
    }
    variant.update(overrides)
    return variant


def test_full_contract_produces_no_gaps():
    _, report = run_guardrail(
        [_xhs_full_variant()], get_channel("xiaohongshu"), channel_id="xiaohongshu"
    )

    assert report.stats["contract_gaps"] == 0
    assert report.contract_gaps == []
    assert not any("内容契约" in note for note in report.needs_human)


def test_contract_gaps_are_reported_but_do_not_flip_passed():
    """**这是本层最重要的边界**：缺口进 needs_human，但 passed 不受影响。

    ``passed`` 表达的是硬合规（字数 / 违禁词 / 致命 AI 味）。
    把「内容不够好」也算进去，等于让工具替审校下判断，
    还会让所有渠道的 passed 长期为 False 而失去信号价值。
    """
    _, report = run_guardrail(
        [{"title": TITLE_XHS_OK, "body": "一段干净的正文。", "hashtags": []}],
        get_channel("xiaohongshu"),
        channel_id="xiaohongshu",
    )

    assert report.stats["contract_gaps"] > 0
    assert report.contract_gaps
    assert all("内容契约" in note for note in report.contract_gaps)
    assert report.passed is True, "契约缺口不得翻转 passed"


def test_validator_never_fills_in_missing_content():
    """核验者不补内容：缺 cta 不会替你写一个 —— 那正是「不静默改写」的约定。"""
    fixed, _ = run_guardrail(
        [{"title": TITLE_XHS_OK, "body": "一段干净的正文。", "hashtags": []}],
        get_channel("xiaohongshu"),
        channel_id="xiaohongshu",
    )

    assert "cta" not in fixed[0]
    assert "hook_type" not in fixed[0]


def test_gaps_of_filters_failures_only():
    checks = check_content_contract({"title": "t", "body": "b"}, get_channel("wechat"))
    assert gaps_of(checks) == list(checks)
    assert all(not check.ok for check in gaps_of(checks))


def test_report_is_json_serializable_after_adding_contract_checks():
    _, report = run_guardrail(
        [_xhs_full_variant()], get_channel("xiaohongshu"), channel_id="xiaohongshu"
    )

    payload = json.dumps(report.model_dump(), ensure_ascii=False)
    assert "contract" in payload and "内容契约" not in payload  # 全通过时没有缺口文案


# ==========================================================================
# 四、分层标签：标签的单一来源
# ==========================================================================
def test_tiered_tags_become_the_hashtag_source():
    """小红书标签是**分层结构**，``hashtags`` 由守门层扁平化得到。

    否则「hashtags」与「分层结果」会变成两处真源 —— 而「按层核验标签配比」
    恰恰依赖后者的可信度。
    """
    assert tag_source_field(get_channel("xiaohongshu")) == "tags_by_tier"
    assert tag_source_field(get_channel("wechat")) is None

    fixed, report = run_guardrail(
        [_xhs_full_variant()], get_channel("xiaohongshu"), channel_id="xiaohongshu"
    )

    # 顺序：hot 在前、longtail 在后，与提示词里给模型的顺序一致
    assert fixed[0]["hashtags"] == [
        "#保温杯推荐", "#通勤好物", "#秋冬必备", "#真实体验", "#好物分享",
    ]
    assert any("扁平化" in note for note in report.auto_fixed)


def test_flatten_follows_declared_tier_order():
    entry = {
        "field": "tags_by_tier",
        "tiers": [{"key": "hot"}, {"key": "longtail"}],
    }
    assert flatten_tiered_tags({"longtail": ["b"], "hot": ["a"]}, entry) == ["a", "b"]
    assert flatten_tiered_tags({"longtail": "b"}, entry) == ["b"]  # 容忍标量
    assert flatten_tiered_tags({}, entry) == []
    assert flatten_tiered_tags("不是对象", entry) == []


def test_copywriting_node_derives_hashtags_before_persisting():
    """标签推导必须在**创作节点出口**完成，不能等守门层。

    这是真实端到端跑出来的缺口：``runner._write_chunk`` 按**节点名**写阶段产出，
    守门节点回写 ``copywriting`` 状态通道**不会**更新 ``copywriting`` 那行阶段记录。
    只在守门层推导时，守门报告一切正常（契约全通过），但持久化的
    「创作阶段产出」里 ``hashtags`` 是空的 —— 而前端变体编辑器读的正是它。
    表现就是「核验通过、界面标签空白」这种最难归因的断裂。
    """
    from app.agents.nodes import make_copywriting_node
    from app.llm import LLMResult

    class StubLLM:
        def chat(self, messages, **kwargs):
            return LLMResult(
                content=json.dumps({"variants": [{
                    "title": TITLE_XHS_OK, "body": "正文", "notes": "n",
                    "hook_type": "场景痛点", "scenario": "s", "experience_detail": "e",
                    "selling_points": ["a", "b", "c"],
                    "tags_by_tier": {"hot": ["大标签", "大标签2"], "longtail": ["长1", "长2", "长3"]},
                    "cta": "c",
                }]}, ensure_ascii=False),
                model="stub", prompt_tokens=1, completion_tokens=1, latency_seconds=0.0,
            )

    node = make_copywriting_node(StubLLM())
    out = node({"task_id": 1, "input": {
        "topic": "保温杯", "brand_name": "示例", "channel_id": "xiaohongshu",
    }})

    # 落库的是**可发布形态**（已按小红书单井号写法补齐），
    # 因为前端编辑器 / 导出读的正是这份记录 —— 只推导「裸词」等于把补井号的活
    # 留给了没人看的下游。
    assert out["copywriting"][0]["hashtags"] == [
        "#大标签", "#大标签2", "#长1", "#长2", "#长3",
    ]


def test_copywriting_node_lets_config_win_over_model_supplied_hashtags():
    """模型若既给了 tags_by_tier 又自作主张给了 hashtags，以分层结构为准。"""
    from app.agents.nodes import make_copywriting_node
    from app.llm import LLMResult

    class StubLLM:
        def chat(self, messages, **kwargs):
            return LLMResult(
                content=json.dumps({"variants": [{
                    "title": TITLE_XHS_OK, "body": "正文",
                    "hashtags": ["#模型自己编的"],
                    "hook_type": "场景痛点", "scenario": "s", "experience_detail": "e",
                    "selling_points": ["a", "b", "c"],
                    "tags_by_tier": {"hot": ["大标签", "大标签2"], "longtail": ["长1", "长2", "长3"]},
                    "cta": "c",
                }]}, ensure_ascii=False),
                model="stub", prompt_tokens=1, completion_tokens=1, latency_seconds=0.0,
            )

    node = make_copywriting_node(StubLLM())
    out = node({"task_id": 1, "input": {
        "topic": "保温杯", "brand_name": "示例", "channel_id": "xiaohongshu",
    }})

    # 落库的是**可发布形态**（已按小红书单井号写法补齐），
    # 因为前端编辑器 / 导出读的正是这份记录 —— 只推导「裸词」等于把补井号的活
    # 留给了没人看的下游。
    assert out["copywriting"][0]["hashtags"] == [
        "#大标签", "#大标签2", "#长1", "#长2", "#长3",
    ]


def test_channels_without_a_tiered_source_keep_the_model_tags():
    """微博/公众号/产品页没有 tiered 标签来源 —— 标签必须原样保留，不能被推导逻辑清掉。"""
    from app.agents.nodes import make_copywriting_node
    from app.llm import LLMResult

    class StubLLM:
        def chat(self, messages, **kwargs):
            return LLMResult(
                content=json.dumps({"variants": [{
                    "title": "t", "body": "b", "hashtags": ["#话题"], "notes": "n",
                }]}, ensure_ascii=False),
                model="stub", prompt_tokens=1, completion_tokens=1, latency_seconds=0.0,
            )

    node = make_copywriting_node(StubLLM())
    out = node({"task_id": 1, "input": {
        "topic": "保温杯", "brand_name": "示例", "channel_id": "weibo",
    }})

    assert out["copywriting"][0]["hashtags"] == ["#话题"]


def test_missing_tiered_tags_falls_back_instead_of_wiping_tags():
    """分层结构没填出来时，**不能把标签清空** —— 退回模型给的 hashtags，
    缺口交给契约核验去报（清空会让「标签缺失」与「模型没给标签」混为一谈）。"""
    variant = _xhs_full_variant(hashtags=["#手工标签"], tags_by_tier={"hot": [], "longtail": []})
    fixed, report = run_guardrail([variant], get_channel("xiaohongshu"), channel_id="xiaohongshu")

    assert fixed[0]["hashtags"] == ["#手工标签"]
    assert any("热门大标签" in note for note in report.contract_gaps)


# ==========================================================================
# 五、资产完整性：契约自己也要被守住
# ==========================================================================
@pytest.mark.parametrize("channel_id", ALL_CHANNELS)
def test_every_channel_declares_a_content_contract(channel_id: str):
    """四个内置渠道都必须有契约 —— 「内容质量按渠道细化」首先要落到配置上。"""
    entries = contract_from_channel(get_channel(channel_id))
    assert entries, f"{channel_id} 未声明内容契约"


@pytest.mark.parametrize("channel_id", ALL_CHANNELS)
def test_contract_entries_are_well_formed(channel_id: str):
    for entry in contract_from_channel(get_channel(channel_id)):
        where = f"{channel_id}.{entry.get('field')}"
        assert entry.get("kind") in KNOWN_KINDS, f"{where} kind 非法: {entry.get('kind')}"
        assert entry.get("label"), f"{where} 缺 label（needs_human 与前端都靠它显示）"
        if entry.get("kind") == "enum":
            assert entry.get("values"), f"{where} 是 enum 却没有 values"
        if entry.get("kind") == "tiered":
            tiers = entry.get("tiers") or []
            assert tiers, f"{where} 是 tiered 却没有 tiers"
            for tier in tiers:
                assert tier.get("key") and tier.get("label"), f"{where} 的 tier 缺 key/label"
                assert tier.get("min", 0) <= tier.get("max", 0) or not tier.get("max"), where
        if "min" in entry and "max" in entry:
            assert entry["min"] <= entry["max"], f"{where} 的 min > max"
        if entry.get("feeds"):
            assert entry["feeds"] in FEED_TARGETS, f"{where} 的 feeds 目标不在白名单"


def test_only_tiered_entries_may_feed_standard_fields():
    for channel_id in ALL_CHANNELS:
        for entry in contract_from_channel(get_channel(channel_id)):
            if entry.get("feeds"):
                assert entry["kind"] == "tiered", f"{channel_id}.{entry['field']} 非 tiered 却声明 feeds"


def test_xiaohongshu_tier_ceiling_fits_hashtag_max():
    """分层标签的上限总和不得超过守门层的标签上限。

    两个数字住在不同的段（``tiers`` 与 ``hashtag_max``），天然容易漂移：
    契约允许 6 个、守门层却只留 4 个，模型就会永远"超量被截断"。
    """
    channel = get_channel("xiaohongshu")
    entry = next(e for e in contract_from_channel(channel) if e["field"] == "tags_by_tier")
    ceiling = sum(int(tier.get("max") or 0) for tier in entry["tiers"])

    assert ceiling <= channel["hashtag_max"], (
        f"分层标签上限合计 {ceiling} 超过 hashtag_max {channel['hashtag_max']}"
    )


# ==========================================================================
# 六、契约与提示词不漂移（最有价值的一条）
# ==========================================================================
@pytest.mark.parametrize("channel_id", ALL_CHANNELS)
def test_prompt_asks_for_every_contract_field(channel_id: str):
    """契约里核验的字段，必须真的出现在创作提示词里。

    这条守的是**双向漂移**：改了 prompts 忘了改契约（字段没人核验），
    或改了契约忘了改 prompts（模型根本不知道要填，缺口永远修不掉）。
    """
    messages = build_copywriting_messages(
        {"input": {
            "topic": "智能保温杯", "brand_name": "暖芯", "target_audience": "都市白领",
            "channel_id": channel_id, "extra_requirements": "",
        }},
        get_channel(channel_id),
    )
    prompt = messages[-1]["content"]

    for entry in contract_from_channel(get_channel(channel_id)):
        assert entry["field"] in prompt, (
            f"{channel_id}: 契约字段「{entry['field']}」没有出现在创作提示词里"
        )


@pytest.mark.parametrize("channel_id", ALL_CHANNELS)
def test_prompt_declares_enum_values_from_the_contract(channel_id: str):
    """enum 的允许取值也要同步 —— 提示词给了别的选项，模型就会踩缺口。"""
    messages = build_copywriting_messages(
        {"input": {"topic": "t", "brand_name": "b", "channel_id": channel_id}},
        get_channel(channel_id),
    )
    prompt = messages[-1]["content"]

    for entry in contract_from_channel(get_channel(channel_id)):
        if entry.get("kind") != "enum":
            continue
        for value in entry["values"]:
            assert value in prompt, f"{channel_id}: 提示词未声明枚举值「{value}」"


def test_channel_display_name_comes_from_the_config():
    """提示词里的渠道名必须取自 channels.yaml，而不是本地抄一份映射表。

    回归：此前映射表只覆盖 weibo / product_page，小红书与公众号
    在提示词里会以 **英文 id** 出现。
    """
    from app.agents.prompts import _marketing_controls

    for channel_id in ALL_CHANNELS:
        expected = get_channel(channel_id)["name"]
        assert expected in _marketing_controls(channel_id)
    assert "未指定渠道" in _marketing_controls("")
    assert "未来渠道" in _marketing_controls("未来渠道")  # 未知渠道原样回显，不报错


def test_all_channels_agree_with_their_declared_contract_count():
    """每个渠道的契约项数量固定下来，便于 review 时一眼看出有没有人偷偷删项。"""
    assert len(contract_from_channel(get_channel("xiaohongshu"))) == 6
    assert len(contract_from_channel(get_channel("wechat"))) == 5
    assert len(contract_from_channel(get_channel("weibo"))) == 3
    assert len(contract_from_channel(get_channel("product_page"))) == 4
    assert len(list_channels()) == 4
