"""emoji 检测与渠道策略。

内化自微博确定性引擎（build_weibo_post.py）的 is_emoji / strip_emoji，
并扩展为「按渠道策略执行」——渠道配置里的 require_emoji 只说明要不要，
这里负责具体怎么修。
"""
from __future__ import annotations

# emoji 码点区间（内化自微博引擎，保持与其一致以避免两套口径）
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
)
_EMOJI_SINGLE = frozenset({
    0x2B50,
    0x2764,
    0xFE0F,
    0x2049,
    0x203C,
    # 有意的本地增强：ZWJ（零宽连接符）本身不是 emoji，但它是组合 emoji
    # （如 👨‍👩‍👧）的粘合剂。不把它算进 emoji，strip_emoji 会留下一串
    # 不可见的 U+200D 残渣——既污染正文又白占字符数。
    0x200D,
})

POLICY_REMOVE = "remove"  # 全部移除（渠道不需要 emoji）
POLICY_CAP = "cap3"  # 最多保留 N 个，超出按出现顺序丢弃
POLICY_KEEP = "keep"  # 不动

DEFAULT_MAX_KEEP = 3


def is_emoji(ch: str) -> bool:
    """单字符是否为 emoji（含变体选择符）。空字符串返回 False。"""
    if not ch:
        return False
    code = ord(ch)
    if code in _EMOJI_SINGLE:
        return True
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def count_emoji(text: str) -> int:
    return sum(1 for ch in text if is_emoji(ch))


def emoji_positions(text: str) -> list[int]:
    """返回 emoji 在文本中的字符下标（按码点计，ZWJ 组合会得到多个下标）。"""
    return [index for index, ch in enumerate(text) if is_emoji(ch)]


def strip_emoji(text: str) -> str:
    return "".join(ch for ch in text if not is_emoji(ch))


def apply_emoji_policy(
    text: str,
    policy: str,
    *,
    max_keep: int = DEFAULT_MAX_KEEP,
) -> tuple[str, list[str]]:
    """按渠道策略处理 emoji。

    Returns:
        (处理后的文本, 人类可读的改动说明列表)。无改动时说明为空列表。
    """
    total = count_emoji(text)
    if total == 0 or policy == POLICY_KEEP:
        return text, []

    if policy == POLICY_REMOVE:
        return strip_emoji(text), [f"按渠道策略移除 {total} 个 emoji"]

    if policy == POLICY_CAP:
        if total <= max_keep:
            return text, []
        kept = 0
        chars: list[str] = []
        for ch in text:
            if is_emoji(ch):
                kept += 1
                if kept > max_keep:
                    continue
            chars.append(ch)
        return "".join(chars), [
            f"emoji 超量（{total} 个 > 上限 {max_keep}），已保留前 {max_keep} 个"
        ]

    # 未知策略按不动处理，避免误删
    return text, []


def policy_from_channel(channel: dict) -> str:
    """从渠道配置推导 emoji 策略。

    显式配置的 emoji_policy 优先；否则按 require_emoji 推断：
    不需要 emoji 的渠道移除，需要的渠道最多保留 3 个。
    """
    explicit = str(channel.get("emoji_policy") or "").strip().lower()
    if explicit in (POLICY_REMOVE, POLICY_CAP, POLICY_KEEP):
        return explicit
    return POLICY_CAP if channel.get("require_emoji") else POLICY_REMOVE
