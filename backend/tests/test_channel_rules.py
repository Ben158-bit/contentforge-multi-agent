"""渠道规则资产测试（P5）。

`channels.yaml` 从「自由文本 + 缺省推断」升级为「显式结构化字段」后，
需要有人在每次改动时守住两件事：

1. **资产完整性** —— 四个渠道都得把 7 个守门参数写全，且彼此不矛盾；
2. **真的被读到** —— 显式值必须驱动守门层行为，不能躺在 YAML 里当注释。

第 2 点才是这个文件存在的核心理由：**配置写了却没人读，是最难发现的一类失效**。
守门层的 `policy_from_channel` / `rules_from_channel` / `mode_from_channel`
都走「显式优先、缺省推断」，所以「显式值写错但恰好和推断值一样」这种错
只能靠对**行为**的断言抓出来，靠读配置是抓不出来的。
"""
from __future__ import annotations

import pytest

from app.guardrails import (
    mode_from_channel,
    policy_from_channel,
    rules_from_channel,
    run_guardrail,
)
from app.templates import get_channel

ALL_CHANNELS = ("xiaohongshu", "wechat", "weibo", "product_page")

# 守门层会读的结构化字段；缺任何一个都会静默退回推断值
GUARDRAIL_FIELDS = (
    "emoji_policy",
    "hashtag_style",
    "hashtag_min",
    "hashtag_max",
    "title_min_chars",
    "title_max_chars",
    "char_count_mode",
)

EMOJI_POLICIES = ("remove", "cap3", "keep")
HASHTAG_STYLES = ("paired", "hash", "none")
COUNT_MODES = ("raw", "no_whitespace")

# 恰好 19 字，落在小红书 18-20 区间内
TITLE_XHS_OK = "冬季通勤保温杯实测：30天后的真实感受"


def _variant(body: str = "这是一段干净的正文。", **overrides) -> dict:
    variant = {"title": "标题", "body": body, "hashtags": [], "notes": "n"}
    variant.update(overrides)
    return variant


def _run(channel_id: str, *variants: dict, topic: str | None = None):
    """用真实渠道配置跑一遍守门层，返回 (归一化后的变体, 报告)。"""
    channel = get_channel(channel_id)
    items = list(variants) or [_variant()]
    return run_guardrail(
        items, channel, channel_id=channel_id, fallback_topic=topic
    )


# ==========================================================================
# 一、资产完整性：四个渠道都得把参数写全
# ==========================================================================
@pytest.mark.parametrize("channel_id", ALL_CHANNELS)
def test_every_channel_declares_all_guardrail_fields(channel_id: str):
    """少写一个字段 = 静默退回推断值 —— 这种「配置缺项」必须在加载期之外被抓到。"""
    channel = get_channel(channel_id)
    missing = [field for field in GUARDRAIL_FIELDS if field not in channel]
    assert not missing, f"{channel_id} 缺少结构化守门字段: {missing}"


def test_guardrail_field_values_are_legal():
    for channel_id in ALL_CHANNELS:
        channel = get_channel(channel_id)
        assert channel["emoji_policy"] in EMOJI_POLICIES, channel_id
        assert channel["hashtag_style"] in HASHTAG_STYLES, channel_id
        assert channel["char_count_mode"] in COUNT_MODES, channel_id
        for field in ("hashtag_min", "hashtag_max", "title_min_chars", "title_max_chars"):
            value = channel[field]
            assert isinstance(value, int) and not isinstance(value, bool), (
                f"{channel_id}.{field} 应为整数，实际 {value!r}"
            )
            assert value >= 0, f"{channel_id}.{field} 不应为负: {value}"


def test_require_flags_agree_with_explicit_policies():
    """`require_*` 是给模型的风格提示，结构化字段是给工具的确定性参数。

    两者表达的是同一件事，允许的差异只有「程度」不允许「方向」：
    声明了「不需要标签」却又让工具去成对规范化，就是自相矛盾的资产。
    """
    for channel_id in ALL_CHANNELS:
        channel = get_channel(channel_id)
        assert bool(channel["require_hashtags"]) is (
            channel["hashtag_style"] != "none"
        ), f"{channel_id}: require_hashtags 与 hashtag_style 方向不一致"
        assert bool(channel["require_emoji"]) is (
            channel["emoji_policy"] != "remove"
        ), f"{channel_id}: require_emoji 与 emoji_policy 方向不一致"


def test_channels_that_do_not_use_hashtags_declare_no_range():
    for channel_id in ALL_CHANNELS:
        channel = get_channel(channel_id)
        if channel["hashtag_style"] == "none":
            assert channel["hashtag_min"] == 0, channel_id
            assert channel["hashtag_max"] == 0, channel_id


def test_channels_that_use_hashtags_have_a_ceiling():
    """用标签的渠道必须给上限，否则长期不管就会越堆越多。"""
    for channel_id in ALL_CHANNELS:
        channel = get_channel(channel_id)
        if channel["hashtag_style"] != "none":
            assert channel["hashtag_max"] > 0, f"{channel_id} 未配置标签上限"
            assert channel["hashtag_min"] <= channel["hashtag_max"], channel_id


def test_title_range_has_a_ceiling_and_is_ordered():
    """标题区间必须**有上限**，且下限不高于上限；两端都为 0 表示显式放弃判定。

    为什么必须要求上限：上限来自**平台硬约束**（微信 64 字、小红书 20 字），
    是「超了就发不出去/被截断」的事实。下限只是**风格建议**（小红书 18 字起
    才好看），所以「只配上限」合理、「只配下限」不合理——后者等于凭空给标题
    设了个必须够长的硬门槛，还会让标题被硬凑字数。
    """
    for channel_id in ALL_CHANNELS:
        channel = get_channel(channel_id)
        lower, upper = channel["title_min_chars"], channel["title_max_chars"]
        if lower == 0 and upper == 0:
            continue
        assert upper > 0, f"{channel_id}: 配了标题下限 {lower} 却没配上限"
        assert 0 <= lower <= upper, f"{channel_id}: 标题区间 {lower}-{upper} 不合法"


def test_every_channel_has_a_positive_body_limit():
    for channel_id in ALL_CHANNELS:
        assert get_channel(channel_id)["max_chars"] > 0, channel_id


# ==========================================================================
# 二、显式值真的驱动守门层（而不是退回了内置默认）
# ==========================================================================
@pytest.mark.parametrize("channel_id", ALL_CHANNELS)
def test_explicit_values_flow_through_the_channel_helpers(channel_id: str):
    channel = get_channel(channel_id)

    rules = rules_from_channel(channel)
    assert rules["style"] == channel["hashtag_style"]
    assert rules["min_count"] == channel["hashtag_min"]
    assert rules["max_count"] == channel["hashtag_max"]
    assert policy_from_channel(channel) == channel["emoji_policy"]
    assert mode_from_channel(channel) == channel["char_count_mode"]


def test_xiaohongshu_hashtags_are_single_hash_not_paired():
    """回归：小红书按 `require_hashtags` 推断会得到微博的 `paired` 写法。

    平台实际写法是 `#话题`（单井号）；`hashtags.py` 的模块文档也一直这么写。
    推断口径与平台规范不符，P5 用显式 `hashtag_style: hash` 修正。
    """
    fixed, _ = _run("xiaohongshu", _variant(hashtags=["保温杯", "#通勤"]))

    assert fixed[0]["hashtags"] == ["#保温杯", "#通勤"]
    assert all(not tag.endswith("#") for tag in fixed[0]["hashtags"])


def test_weibo_hashtags_stay_paired():
    """微博反过来：话题必须 `#话题#` 成对，否则不生效。"""
    fixed, _ = _run("weibo", _variant(hashtags=["保温杯", "通勤"]))

    assert fixed[0]["hashtags"] == ["#保温杯#", "#通勤#"]


@pytest.mark.parametrize("channel_id", ["wechat", "product_page"])
def test_channels_without_hashtags_clear_them(channel_id: str):
    fixed, report = _run(channel_id, _variant(hashtags=["保温杯", "通勤"]))

    assert fixed[0]["hashtags"] == []
    assert any("不使用话题标签" in note for note in report.variant_reports[0].hashtag_notes)


@pytest.mark.parametrize(
    ("channel_id", "expected"),
    [("xiaohongshu", 8), ("weibo", 4)],
)
def test_hashtag_max_truncates_per_channel(channel_id: str, expected: int):
    tags = [f"标签{index}" for index in range(10)]
    fixed, report = _run(channel_id, _variant(hashtags=tags))

    assert len(fixed[0]["hashtags"]) == expected
    assert any("超量" in note for note in report.variant_reports[0].hashtag_notes)


@pytest.mark.parametrize(
    ("channel_id", "expected"),
    [("xiaohongshu", ["#智能保温杯"]), ("weibo", ["#智能保温杯#"])],
)
def test_hashtag_min_backfills_one_tag_from_the_topic(channel_id: str, expected: list[str]):
    """`hashtag_min` 的语义是「必须有标签」，实现只兜底补 1 个。

    兜底会出现在报告的 `auto_fixed` 里（不是静默注入），并提示换成真实在跑的话题。
    """
    fixed, report = _run(channel_id, _variant(), topic="智能保温杯")

    assert fixed[0]["hashtags"] == expected
    assert any("标签不足" in note for note in report.variant_reports[0].hashtag_notes)
    assert any("标签不足" in note for note in report.auto_fixed)


@pytest.mark.parametrize("channel_id", ["wechat", "product_page"])
def test_channels_without_hashtags_never_backfill(channel_id: str):
    fixed, report = _run(channel_id, _variant(), topic="智能保温杯")

    assert fixed[0]["hashtags"] == []
    assert not any("标签不足" in note for note in report.variant_reports[0].hashtag_notes)


def test_enough_hashtags_does_not_trigger_backfill():
    fixed, report = _run("xiaohongshu", _variant(hashtags=["保温杯"]), topic="智能保温杯")

    assert fixed[0]["hashtags"] == ["#保温杯"]
    assert report.variant_reports[0].hashtag_notes == []


# ==========================================================================
# 三、标题长度判定：配了就得生效，没配就得说清楚
# ==========================================================================
def test_xiaohongshu_title_must_sit_in_the_declared_range():
    _, ok_report = _run("xiaohongshu", _variant(title=TITLE_XHS_OK))
    assert ok_report.variant_reports[0].title.length_ok is True

    _, short_report = _run("xiaohongshu", _variant(title="保温杯实测"))
    assert short_report.variant_reports[0].title.length_ok is False
    assert any(
        "标题长度不合规" in note for note in short_report.needs_human
    ), "标题越界必须进 needs_human，否则等于没判定"


def test_xiaohongshu_title_over_platform_ceiling_is_flagged():
    long_title = "通勤保温杯自用一个月后我想说的三个真实细节和踩过的坑"
    assert len(long_title) > 20

    _, report = _run("xiaohongshu", _variant(title=long_title))

    assert report.variant_reports[0].title.length_ok is False


def test_wechat_title_ceiling_is_enforced():
    _, ok_report = _run("wechat", _variant(title="字" * 40))
    assert ok_report.variant_reports[0].title.length_ok is True

    _, bad_report = _run("wechat", _variant(title="字" * 70))
    assert bad_report.variant_reports[0].title.length_ok is False


@pytest.mark.parametrize("channel_id", ["weibo", "product_page"])
def test_channels_without_a_title_range_skip_the_check(channel_id: str):
    """没配区间 ≠ 标题合格，而是「这个渠道不判标题长度」——报告里要说清楚。"""
    _, report = _run(channel_id, _variant(title="标题"))
    title = report.variant_reports[0].title

    assert title.length_ok is True
    assert any("跳过长度合规判定" in note for note in title.notes)


# ==========================================================================
# 四、渠道之间不得串味（升级为显式声明后最容易犯的错）
# ==========================================================================
def test_xiaohongshu_is_not_configured_like_weibo():
    """两个都用标签、都要 emoji，但写法必须不同 —— 显式化之后容易复制粘贴串味。"""
    xhs = get_channel("xiaohongshu")
    weibo = get_channel("weibo")

    assert xhs["require_hashtags"] is weibo["require_hashtags"] is True
    assert xhs["hashtag_style"] != weibo["hashtag_style"]
    assert xhs["hashtag_max"] != weibo["hashtag_max"]
    assert xhs["max_chars"] != weibo["max_chars"]
