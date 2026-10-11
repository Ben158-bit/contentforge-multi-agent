"""话题标签规范化测试。"""
from __future__ import annotations

from app.guardrails.hashtags import (
    STYLE_HASH,
    STYLE_NONE,
    STYLE_PAIRED,
    normalize_tag,
    normalize_tags,
    rules_from_channel,
)


def test_paired_style_wraps_both_sides():
    assert normalize_tag("话题", STYLE_PAIRED) == "#话题#"


def test_hash_style_wraps_one_side():
    assert normalize_tag("话题", STYLE_HASH) == "#话题"


def test_strips_existing_hashes_including_fullwidth():
    assert normalize_tag("#话题#", STYLE_PAIRED) == "#话题#"
    assert normalize_tag("＃话题＃", STYLE_PAIRED) == "#话题#"
    assert normalize_tag("##话题", STYLE_HASH) == "#话题"


def test_blank_tag_becomes_empty():
    assert normalize_tag("", STYLE_PAIRED) == ""
    assert normalize_tag("###", STYLE_PAIRED) == ""
    assert normalize_tag(None, STYLE_PAIRED) == ""  # type: ignore[arg-type]


def test_none_style_always_empty():
    assert normalize_tag("话题", STYLE_NONE) == ""


def test_normalize_tags_dedupes_case_insensitively():
    tags, notes = normalize_tags(
        ["#AI#", "#ai#", "#AI#"], style=STYLE_PAIRED, min_count=0, max_count=0
    )
    assert tags == ["#AI#"]
    assert any("重复" in note for note in notes)


def test_normalize_tags_truncates_to_max():
    tags, notes = normalize_tags(
        ["#a#", "#b#", "#c#", "#d#"], style=STYLE_PAIRED, max_count=2
    )
    assert tags == ["#a#", "#b#"]
    assert any("超量" in note for note in notes)


def test_normalize_tags_pads_with_topic_when_below_min():
    tags, notes = normalize_tags(
        [], style=STYLE_PAIRED, min_count=2, max_count=4, fallback_topic="通勤穿搭推荐"
    )
    assert len(tags) == 1
    assert tags[0].startswith("#") and tags[0].endswith("#")
    assert any("不足" in note for note in notes)


def test_normalize_tags_does_not_pad_when_topic_would_duplicate():
    existing = normalize_tag("通勤穿搭推荐", STYLE_PAIRED)
    tags, _ = normalize_tags(
        [existing], style=STYLE_PAIRED, min_count=2, fallback_topic="通勤穿搭推荐"
    )
    assert tags == [existing]


def test_none_style_clears_and_explains():
    tags, notes = normalize_tags(["#a#", "#b#"], style=STYLE_NONE)
    assert tags == []
    assert any("不使用话题标签" in note for note in notes)


def test_none_style_with_no_tags_is_silent():
    tags, notes = normalize_tags([], style=STYLE_NONE)
    assert tags == []
    assert notes == []


def test_rules_from_channel_reads_explicit_config():
    rules = rules_from_channel(
        {"hashtag_style": "hash", "hashtag_min": 2, "hashtag_max": 8}
    )
    assert rules == {"style": STYLE_HASH, "min_count": 2, "max_count": 8}


def test_rules_from_channel_infers_from_require_hashtags():
    assert rules_from_channel({"require_hashtags": True})["style"] == STYLE_PAIRED
    assert rules_from_channel({"require_hashtags": False})["style"] == STYLE_NONE
    assert rules_from_channel({})["style"] == STYLE_NONE


def test_rules_from_channel_tolerates_bad_numbers():
    rules = rules_from_channel({"hashtag_min": "不是数字", "hashtag_max": None})
    assert rules["min_count"] == 0
    assert rules["max_count"] == 0
