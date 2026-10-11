"""违禁词 / 广告法词表检出测试。"""
from __future__ import annotations

from app.guardrails.compliance import (
    Wordlist,
    load_wordlist,
    scan_text,
    scan_texts,
    split_user_words,
    summarize_hits,
)


def test_wordlist_loads_and_keeps_weibo_core_entries():
    wordlist = load_wordlist()
    # 内化自微博确定性引擎的 51 条是资产下限
    assert len(wordlist.entries) >= 51
    assert "全网最低" in {entry.word for entry in wordlist.entries}
    assert "100%" in {entry.word for entry in wordlist.entries}


def test_every_entry_carries_an_actionable_suggestion():
    """词表质量闸门：不许出现「禁用」了事。"""
    for entry in load_wordlist().entries:
        assert entry.suggestion.strip(), f"{entry.word} 缺少替换建议"
        assert len(entry.suggestion) >= 6, f"{entry.word} 的替换建议过于笼统"


def test_every_entry_has_known_category_and_basis():
    wordlist = load_wordlist()
    known = set(wordlist.category_labels)
    for entry in wordlist.entries:
        assert entry.category in known, f"{entry.word} 的类别未知: {entry.category}"
        assert entry.basis.strip(), f"{entry.word} 缺少依据"


def test_hits_absolute_advertising_word_with_context_and_suggestion():
    hits = scan_text("这款产品全网最低，错过就没了。")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.word == "全网最低"
    assert hit.category == "absolute_advertising"
    assert hit.scope == "draft"
    assert "全网最低" in hit.context
    assert "本次活动价" in hit.suggestion


def test_ordinal_exemption_does_not_flag_plain_ordinal():
    assert scan_text("他第一次来北京。") == []


def test_ordinal_exemption_still_flags_real_claim():
    hits = scan_text("行业第一的品质")
    assert [hit.word for hit in hits] == ["第一"]


def test_english_substring_is_not_matched():
    """Glasswing 里的 ass 类误匹配必须被英文边界挡住。"""
    custom = Wordlist(
        entries=(),
        category_labels={"user_banned": "用户禁用词"},
    )
    assert scan_text("Glasswing 是英文名", wordlist=custom, user_banned=["ass"]) == []
    assert scan_text("ass 出现在句首", wordlist=custom, user_banned=["ass"])


def test_longer_word_wins_and_mask_prevents_double_count():
    """「最高级」应只命中一次，不额外命中其中的「最高」。"""
    hits = scan_text("这是最高级的配置")
    assert [hit.word for hit in hits] == ["最高级"]


def test_draft_and_source_scopes_are_separate():
    draft = scan_text("全网最低价", scope="draft")
    source = scan_text("全网最低价", scope="source")
    assert [hit.scope for hit in draft] == ["draft"]
    assert [hit.scope for hit in source] == ["source"]


def test_scan_texts_scans_each_variant_and_merges():
    hits = scan_texts(["全网最低", "行业第一", "干净文案"])
    assert {hit.word for hit in hits} == {"全网最低", "第一"}
    assert len(hits) == 2, "干净文案不应产生命中"


def test_user_banned_words_are_scanned_with_their_own_category():
    hits = scan_text("本品含有某某成分", user_banned=["某某成分"])
    assert len(hits) == 1
    assert hits[0].category == "user_banned"
    assert hits[0].basis == "用户禁用词"


def test_user_banned_word_already_in_wordlist_is_not_duplicated():
    hits = scan_text("全网最低", user_banned=["全网最低"])
    assert len(hits) == 1
    assert hits[0].category == "absolute_advertising"


def test_empty_text_returns_no_hits():
    assert scan_text("") == []
    assert scan_text(None) == []  # type: ignore[arg-type]


def test_medical_and_finance_categories_detected():
    hits = scan_text("这款药可以根治近视，投资稳赚不赔")
    assert {hit.category for hit in hits} == {"medical", "finance"}


def test_summarize_hits_groups_by_category():
    hits = scan_text("全网最低，还能根治，而且稳赚")
    summary = summarize_hits(hits)
    assert summary["absolute_advertising"] == 1
    assert summary["medical"] == 1
    assert summary["finance"] == 1


# --------------------------------------------------------------------------
# 人工填写的词表解析（品牌 taboos 是自由文本，不是干净的词表）
# --------------------------------------------------------------------------
def test_split_user_words_handles_comma_separated_words():
    # 单字 "最" 被丢弃：它会命中 "最后" 这类正常表述，误报代价高于漏检
    assert split_user_words("最,第一,根治") == ["第一", "根治"]


def test_split_user_words_handles_chinese_separators():
    assert split_user_words("最、第一；根治\n稳赚") == ["第一", "根治", "稳赚"]


def test_split_user_words_drops_single_characters():
    """单字误报率太高（"最" 会命中 "最后"），宁可不查。"""
    assert split_user_words("最 第一") == ["第一"]


def test_single_character_absolutes_are_still_covered_by_the_builtin_wordlist():
    """单字从**用户词表**里丢掉，不代表这条风险没人管。

    内置词表本来就覆盖 "最好"/"最强" 这类绝对化表述的完整写法 ——
    所以「用户词表保守」与「合规有覆盖」并不冲突。
    """
    hits = scan_text("这是最好的保温杯")
    assert hits, "内置词表应能识别绝对化表述"
    assert any("最" in hit.word for hit in hits)


def test_split_user_words_drops_sentence_like_fragments():
    """没有分隔符的长串是规则句子，不是词 —— 整条丢弃，不做二次切分。"""
    assert split_user_words("不夸大宣传不承诺具体疗效且不虚构价格") == []


def test_split_user_words_trims_and_dedupes():
    assert split_user_words(" 根治 , 根治 ,稳赚 ") == ["根治", "稳赚"]


def test_split_user_words_returns_empty_for_junk_input():
    for value in (None, "", "   ", 123, [], {"a": 1}):
        assert split_user_words(value) == []


def test_split_user_words_output_is_directly_usable_by_scan_text():
    """词表解析的产出必须能直接喂给扫描器（接口闭合）。"""
    words = split_user_words("根治、稳赚")
    hits = scan_text("这个方案根治一切，投资稳赚", user_banned=words)
    assert {hit.word for hit in hits} >= {"根治", "稳赚"}
