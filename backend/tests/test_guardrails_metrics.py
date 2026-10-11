"""字数口径纯函数测试。"""
from __future__ import annotations

import pytest

from app.guardrails.text_metrics import (
    count_chars,
    count_variant,
    hashtags_to_text,
    mode_from_channel,
    visual_width,
)


def test_raw_mode_counts_everything_including_punctuation_tags_newlines():
    text = "你好，世界。#话题#\n@某人"
    assert count_chars(text) == len(text)


def test_empty_and_whitespace_only():
    assert count_chars("") == 0
    assert count_chars(None) == 0
    # 3 空格 + \n + \t + 1 空格 = 6 个字符，raw 模式下一个都不能少
    assert count_chars("   \n\t ") == 6
    assert count_chars("   \n\t ", "no_whitespace") == 0


def test_no_whitespace_mode_strips_all_whitespace():
    assert count_chars("a b\n\tc", "no_whitespace") == 3


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        count_chars("x", "bogus")  # type: ignore[arg-type]


def test_english_counted_by_character_not_by_word():
    assert count_chars("Meta acquired Manus") == 19


def test_visual_width_halfwidth_counts_half():
    assert visual_width("ab") == 1.0
    assert visual_width("中文") == 2.0
    assert visual_width("中a") == 1.5


def test_hashtags_to_text_accepts_list_and_scalar():
    assert hashtags_to_text(["#a#", "#b#"]) == "#a# #b#"
    assert hashtags_to_text("#only#") == "#only#"
    assert hashtags_to_text(None) == ""
    assert hashtags_to_text(["#a#", "", "  "]) == "#a#"


def test_count_variant_separates_body_from_title_and_tags():
    variant = {
        "title": "标题四个字",
        "body": "正文五个字",
        "hashtags": ["#话题#"],
        "notes": "说明",
    }
    result = count_variant(variant)
    assert result["body"] == len("正文五个字")
    assert result["title"] == len("标题四个字")
    assert result["hashtags"] == len("#话题#")
    assert result["notes"] == len("说明")
    assert result["published"] == result["body"] + result["title"] + result["hashtags"]
    assert result["total"] == result["published"] + result["notes"]


def test_count_variant_tolerates_missing_fields():
    result = count_variant({})
    assert result == {
        "body": 0,
        "title": 0,
        "hashtags": 0,
        "notes": 0,
        "published": 0,
        "total": 0,
    }


def test_count_variant_handles_non_list_hashtags():
    result = count_variant({"body": "x", "hashtags": "#单个#"})
    assert result["hashtags"] == len("#单个#")


def test_mode_from_channel_defaults_to_raw():
    assert mode_from_channel({}) == "raw"
    assert mode_from_channel({"char_count_mode": "no_whitespace"}) == "no_whitespace"
    assert mode_from_channel({"char_count_mode": "bogus"}) == "raw"
