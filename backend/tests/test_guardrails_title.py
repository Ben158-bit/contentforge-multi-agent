"""标题六维体检测试。

体检是**纯算术 + 词表近似**，所以结论必须可复现、可单测——
这正是它不该交给模型算的理由。
"""
from __future__ import annotations

from app.guardrails.title_check import (
    GRADE_NA,
    check_title,
    grade_from_score,
    load_title_rules,
    weighted_grade,
)


# --------------------------------------------------------------------------
# 规则资产
# --------------------------------------------------------------------------
def test_dimension_weights_sum_to_one():
    rules = load_title_rules()
    assert sorted(rules.weights) == [
        "benefit",
        "compliance",
        "emotion",
        "keyword_match",
        "scarcity",
        "structure",
    ]
    assert abs(sum(rules.weights.values()) - 1.0) < 1e-9
    assert rules.labels["benefit"] == "利益"


def test_grade_thresholds_are_descending():
    mins = [threshold for threshold, _, _ in load_title_rules().grades]
    assert mins == sorted(mins, reverse=True)


# --------------------------------------------------------------------------
# 定档
# --------------------------------------------------------------------------
def test_grade_boundaries():
    assert grade_from_score(10.0)[0] == "S"
    assert grade_from_score(9.0)[0] == "S"
    assert grade_from_score(8.99)[0] == "A"
    assert grade_from_score(7.0)[0] == "A"
    assert grade_from_score(6.99)[0] == "B"
    assert grade_from_score(5.0)[0] == "B"
    assert grade_from_score(4.99)[0] == "C"
    assert grade_from_score(0.0)[0] == "C"


def test_weighted_grade_all_tens_is_perfect():
    scores = {dim: 10 for dim in load_title_rules().weights}
    score, grade, _ = weighted_grade(scores)
    assert score == 10.0
    assert grade == "S"


def test_weighted_grade_with_missing_dimension_is_na_by_default():
    scores = {dim: 10 for dim in load_title_rules().weights}
    scores["keyword_match"] = None
    score, grade, note = weighted_grade(scores)
    assert score is None
    assert grade == GRADE_NA
    assert "主题匹配" in note


def test_weighted_grade_renormalises_when_partial_allowed():
    scores = {dim: 10 for dim in load_title_rules().weights}
    scores["keyword_match"] = None
    score, grade, _ = weighted_grade(scores, allow_partial=True)
    assert score == 10.0
    assert grade == "S"


def test_weighted_grade_without_any_known_dimension():
    assert weighted_grade({})[1] == GRADE_NA
    assert weighted_grade({"不存在的维度": 10})[1] == GRADE_NA


# --------------------------------------------------------------------------
# 体检主流程
# --------------------------------------------------------------------------
def test_empty_title_is_na_and_flagged():
    result = check_title("")
    assert result.length == 0
    assert result.length_ok is False
    assert result.grade == GRADE_NA
    assert result.grade_note == "标题为空"
    assert result.notes == ["标题为空，无法体检"]
    assert all(value is None for value in result.dimensions.values())


def test_length_within_range_scores_full_and_keeps_flag_true():
    result = check_title("春季新款连衣裙，显瘦百搭", min_chars=5, max_chars=20)
    assert result.length_ok is True
    assert result.dimensions["structure"] == 10.0


def test_length_below_min_is_flagged_and_downgraded():
    result = check_title("短标题", min_chars=5, max_chars=20)
    assert result.length_ok is False
    assert result.dimensions["structure"] == 6.0
    assert any("短于下限" in note for note in result.notes)


def test_length_slightly_over_limit_is_tolerated():
    result = check_title("字" * 22, max_chars=20)
    assert result.length_ok is False
    assert result.dimensions["structure"] == 6.0
    assert any("略超上限" in note for note in result.notes)


def test_length_far_over_limit_scores_worst():
    result = check_title("字" * 30, max_chars=20)
    assert result.length_ok is False
    assert result.dimensions["structure"] == 3.0
    assert any("远超上限" in note for note in result.notes)


def test_missing_length_config_skips_the_check_with_a_note():
    result = check_title("任意标题")
    assert result.length_ok is True
    assert result.dimensions["structure"] == 10.0
    assert any("跳过长度合规判定" in note for note in result.notes)


def test_keywords_dimension_scores_by_hit_ratio():
    result = check_title("春季新款连衣裙", keywords=["春季", "连衣裙"])
    assert result.dimensions["keyword_match"] == 10.0


def test_keywords_dimension_warns_when_nothing_matched():
    result = check_title("完全无关的标题", keywords=["春季", "连衣裙"])
    assert result.dimensions["keyword_match"] == 0.0
    assert any("未包含任何调研关键词" in note for note in result.notes)


def test_no_keywords_provided_marks_dimension_na():
    result = check_title("随便一个标题", keywords=[])
    assert result.dimensions["keyword_match"] is None
    assert any("记 N/A" in note for note in result.notes)


def test_benefit_dimension_rewards_two_hits():
    result = check_title("省 200 元，亲测有效的 3 个方法")
    assert result.dimensions["benefit"] == 10.0


def test_benefit_dimension_gives_partial_credit_for_one_hit():
    result = check_title("这篇只提到一个「方法」")
    assert result.dimensions["benefit"] == 7.0


def test_dimensions_without_any_keyword_hit_get_the_floor_score():
    result = check_title("一款普通的产品")
    assert result.dimensions["benefit"] == 3.0
    assert result.dimensions["emotion"] == 3.0
    assert result.dimensions["scarcity"] == 3.0


def test_clean_title_scores_full_compliance():
    result = check_title("春季新款连衣裙")
    assert result.forbidden_hits == []
    assert result.dimensions["compliance"] == 10.0


def test_forbidden_word_in_title_drops_compliance_score():
    result = check_title("全网最低价的宝藏好物")
    assert len(result.forbidden_hits) == 1
    assert result.dimensions["compliance"] == 5.0
    assert result.forbidden_hits[0].word == "全网最低"


def test_title_with_ai_tell_is_surfaced_for_human():
    """标题里的「不是 X 而是 Y」是 fatal 级 AI 味，必须浮出来。"""
    result = check_title("不是便宜，而是省钱")
    assert result.ai_tell_hits
    assert any(hit.severity == "fatal" for hit in result.ai_tell_hits)


def test_separator_is_only_a_note_not_a_score_bonus():
    with_sep = check_title("春季新款｜显瘦百搭")
    without_sep = check_title("春季新款显瘦百搭")
    assert any("分隔符" in note for note in with_sep.notes)
    assert not any("分隔符" in note for note in without_sep.notes)
    # 分隔符不参与打分：其余维度完全相同
    assert with_sep.dimensions["benefit"] == without_sep.dimensions["benefit"]
    assert with_sep.dimensions["structure"] == without_sep.dimensions["structure"]


def test_full_report_is_json_serialisable():
    result = check_title("全网最低价的 3 个方法", min_chars=5, max_chars=20, keywords=["方法"])
    payload = result.to_dict()
    assert set(payload) == {
        "title",
        "length",
        "length_ok",
        "min_chars",
        "max_chars",
        "dimensions",
        "weighted_score",
        "grade",
        "grade_note",
        "forbidden_hits",
        "ai_tell_hits",
        "notes",
    }
    assert payload["forbidden_hits"][0]["word"] == "全网最低"
    assert payload["grade"] in {"S", "A", "B", "C", GRADE_NA}


def test_title_check_echoes_the_channel_range():
    """标题达标区间必须回写进体检结果——前端编辑器据此做实时字数提示，
    不能前端自己硬编码渠道区间表（那是「单一真源没传到位」的断点）。"""
    result = check_title("春季新款连衣裙，显瘦百搭", min_chars=18, max_chars=20)
    assert result.min_chars == 18
    assert result.max_chars == 20
    assert result.to_dict()["min_chars"] == 18
    assert result.to_dict()["max_chars"] == 20


def test_title_check_echoes_zero_range_when_unconfigured():
    """渠道未配置标题区间（min==max==0）时也要回写 0/0，前端据此不显示区间提示。"""
    result = check_title("无区间渠道标题")
    assert result.min_chars == 0
    assert result.max_chars == 0
    assert result.length_ok is True  # 未配置区间时跳过长度判定
