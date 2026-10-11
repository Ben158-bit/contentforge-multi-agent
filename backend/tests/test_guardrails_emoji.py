"""emoji 检测与渠道策略测试。"""
from __future__ import annotations

from app.guardrails.emoji_policy import (
    DEFAULT_MAX_KEEP,
    POLICY_CAP,
    POLICY_KEEP,
    POLICY_REMOVE,
    apply_emoji_policy,
    count_emoji,
    emoji_positions,
    is_emoji,
    policy_from_channel,
    strip_emoji,
)


def test_is_emoji_recognises_symbol_and_pictograph_ranges():
    assert is_emoji("✨") is True  # 0x2728，落在 0x2600-0x27BF
    assert is_emoji("🚀") is True  # 0x1F680，落在 0x1F300-0x1FAFF
    assert is_emoji("⭐") is True  # 0x2B50，单码点白名单
    assert is_emoji("❤") is True  # 0x2764，单码点白名单
    assert is_emoji("\ufe0f") is True  # 变体选择符，跟随前一个 emoji


def test_is_emoji_rejects_plain_text_and_empty():
    assert is_emoji("") is False
    assert is_emoji("a") is False
    assert is_emoji("中") is False
    assert is_emoji("，") is False
    assert is_emoji("\n") is False


def test_count_and_positions_agree():
    text = "你好🚀世界✨"
    assert count_emoji(text) == 2
    assert emoji_positions(text) == [2, 5]
    assert len(emoji_positions(text)) == count_emoji(text)


def test_strip_emoji_removes_zwj_residue_of_combined_emoji():
    """组合 emoji 的零宽连接符也必须移除，否则正文会留下不可见残渣。"""
    family = "👨\u200d👩\u200d👧"
    assert count_emoji(family) == 5  # 3 个 emoji + 2 个 ZWJ
    assert strip_emoji(family) == ""
    assert strip_emoji(f"一家{family}人") == "一家人"


def test_policy_keep_never_touches_text():
    text = "标题✨✨重要🚀"
    out, notes = apply_emoji_policy(text, POLICY_KEEP)
    assert out == text
    assert notes == []


def test_policy_remove_strips_all_and_reports_count():
    out, notes = apply_emoji_policy("钩子✨正文🚀收尾⭐", POLICY_REMOVE)
    assert out == "钩子正文收尾"
    assert len(notes) == 1
    assert "3" in notes[0]


def test_policy_remove_on_emoji_free_text_is_a_noop():
    out, notes = apply_emoji_policy("纯文字正文", POLICY_REMOVE)
    assert out == "纯文字正文"
    assert notes == []


def test_policy_cap_keeps_first_n_in_order_and_drops_the_rest():
    out, notes = apply_emoji_policy("一✨二🚀三⭐四🔥", POLICY_CAP, max_keep=2)
    assert out == "一✨二🚀三四"
    assert notes and "上限 2" in notes[0]


def test_policy_cap_within_allowance_is_a_noop():
    text = "一✨二🚀"
    out, notes = apply_emoji_policy(text, POLICY_CAP, max_keep=DEFAULT_MAX_KEEP)
    assert out == text
    assert notes == []


def test_policy_cap_default_allowance_is_three():
    out, _ = apply_emoji_policy("✨🚀⭐🔥", POLICY_CAP)
    assert count_emoji(out) == DEFAULT_MAX_KEEP


def test_unknown_policy_leaves_text_alone():
    """未知策略必须按「不动」处理——宁可不改，也不能误删。"""
    text = "正文✨"
    out, notes = apply_emoji_policy(text, "bogus")
    assert out == text
    assert notes == []


def test_policy_from_channel_prefers_explicit_config():
    assert policy_from_channel({"emoji_policy": "KEEP", "require_emoji": False}) == POLICY_KEEP
    assert policy_from_channel({"emoji_policy": " remove "}) == POLICY_REMOVE


def test_policy_from_channel_infers_from_require_emoji():
    assert policy_from_channel({"require_emoji": True}) == POLICY_CAP
    assert policy_from_channel({"require_emoji": False}) == POLICY_REMOVE
    # 渠道没配这一项时按「不需要 emoji」处理，与旧行为一致
    assert policy_from_channel({}) == POLICY_REMOVE
