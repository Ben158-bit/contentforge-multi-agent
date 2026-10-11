"""字数硬校验与五级降档测试。

核心断言：**只要存在可行解，降档结果就一定不超限**。
"""
from __future__ import annotations

from app.guardrails.length_policy import (
    DETAIL_FULL,
    DETAIL_SHORT,
    DETAIL_THESIS,
    build_segments,
    clip,
    downgrade_body,
    split_sentences,
)


def test_split_sentences_keeps_punctuation_and_skips_newlines():
    text = "数十亿美元。\n\n这是Meta刚刚收购Manus的价格。"
    assert split_sentences(text) == ["数十亿美元。", "这是Meta刚刚收购Manus的价格。"]


def test_split_sentences_does_not_break_on_consecutive_terminators():
    assert split_sentences("真的吗？！我不信。") == ["真的吗？！", "我不信。"]


def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_clip_returns_text_when_within_limit():
    assert clip("很短", 10) == "很短"


def test_clip_breaks_at_punctuation_and_keeps_the_pause_mark():
    text = "第一句话比较长，第二句话也不短，第三句话更长一些"
    out = clip(text, 12)
    assert out == "第一句话比较长，"
    assert len(out) <= 12


def test_clip_never_exceeds_limit_across_widths():
    """回归：断句窗口曾写成 text[:limit+4]，导致 cut 落在限额之外。"""
    text = "第一句话比较长，第二句话也不短，第三句话更长一些，第四句收尾。"
    for limit in range(1, len(text) + 1):
        out = clip(text, limit)
        assert len(out) <= limit, f"limit={limit} 时裁出了 {len(out)} 字"
        assert text.startswith(out), f"limit={limit} 时应是原文前缀"


def test_clip_hard_truncates_when_no_pause_mark_is_available():
    out = clip("一二三四五六七八九十甲乙丙丁", 6)
    assert len(out) <= 6


def test_clip_falls_back_to_hard_cut_when_tail_particles_trim_to_empty():
    assert clip("的了在是的了", 3) != ""


def test_build_segments_marks_first_as_hook_and_last_as_closing():
    body = "钩子句。中间要点一。中间要点二。结尾号召。"
    hook, points, closing = build_segments(body)
    assert hook is not None and hook.kind == "hook" and hook.protect
    assert [p.text for p in points] == ["中间要点一。", "中间要点二。"]
    assert closing is not None and closing.kind == "closing"


def test_build_segments_two_sentences_have_no_closing():
    hook, points, closing = build_segments("第一句。第二句。")
    assert closing is None
    assert [p.text for p in points] == ["第二句。"]


def test_build_segments_empty_body():
    hook, points, closing = build_segments("")
    assert hook is None and points == [] and closing is None


def test_short_body_is_returned_untouched_at_level_zero():
    body = "短的正文。"
    result = downgrade_body(body, 280)
    assert result.text == body
    assert result.level == 0
    assert result.fitted is True
    assert result.dropped == []


def test_body_exactly_at_limit_is_not_downgraded():
    body = "一二三四五六七八九十"
    result = downgrade_body(body, 10)
    assert len(result.text) == 10
    assert result.level == 0


def test_body_one_over_limit_without_punctuation_is_hard_cut_and_flagged():
    """没有标点可断的长句只能硬裁——钩子本身被切过，必须留痕交人工。"""
    result = downgrade_body("一二三四五六七八九十甲", 10)
    assert len(result.text) <= 10
    assert result.fitted is False
    assert "人工" in result.notes


def test_body_one_over_limit_with_points_is_downgraded_without_hard_cut():
    body = "钩子句。要点一：细节甲。要点二：细节乙。"
    result = downgrade_body(body, len(body) - 1)
    assert len(result.text) <= len(body) - 1
    assert result.fitted is True
    assert result.level > 0
    assert result.text.startswith("钩子句。")


def test_downgrade_never_exceeds_limit_for_long_text():
    body = "。".join(f"这是第{i}个比较长的句子需要被裁剪掉一些内容" for i in range(1, 20)) + "。"
    result = downgrade_body(body, 60)
    assert len(result.text) <= 60
    assert result.fitted is True


def test_downgrade_records_dropped_points_and_keeps_hook():
    body = "钩子句。" + "".join(f"要点{i}很长很长很长。".replace("i", str(i)) for i in range(1, 8))
    result = downgrade_body(body, 30)
    assert len(result.text) <= 30
    assert result.text.startswith("钩子句。")
    assert result.dropped, "应记录被移出正文的要点"


def test_downgrade_reports_level_and_detail_mode():
    body = "钩子。要点一：这里有很长的细节说明文字需要被压缩。要点二：这里也有很长的细节说明。结语。"
    result = downgrade_body(body, 40)
    assert len(result.text) <= 40
    assert 0 <= result.level <= 4
    assert result.detail_mode in (DETAIL_FULL, DETAIL_SHORT, DETAIL_THESIS)


def test_downgrade_strips_emoji_when_that_is_what_fits():
    body = "钩子✨。" + "补充说明文字很多" * 3
    result = downgrade_body(body, 14)
    assert len(result.text) <= 14


def test_downgrade_empty_body():
    result = downgrade_body("", 100)
    assert result.text == ""
    assert result.fitted is True


def test_downgrade_with_zero_limit_returns_empty():
    result = downgrade_body("任意内容", 0)
    assert result.text == ""
    assert result.fitted is True


def test_full_text_is_preserved_for_long_form_reference():
    body = "钩子。要点一：细节甲。要点二：细节乙。结语。"
    result = downgrade_body(body, 20)
    assert result.full_text == "钩子。\n要点一：细节甲。\n要点二：细节乙。\n结语。"
    assert len(result.text) <= 20
