"""话题标签规范化。

三种风格（由渠道配置的 ``hashtag_style`` 决定）：

- ``paired`` —— ``#话题#``（微博：必须成对，否则不生效）
- ``hash``   —— ``#话题``（小红书：单个井号）
- ``none``   —— 该渠道不使用话题标签，全部清空

做四件事：去空、补井号、按渠道上限截断、数量不足时用主题兜底。
"""
from __future__ import annotations

from typing import Any, Iterable

from .length_policy import clean, clip

STYLE_PAIRED = "paired"
STYLE_HASH = "hash"
STYLE_NONE = "none"

STYLES = (STYLE_PAIRED, STYLE_HASH, STYLE_NONE)
FALLBACK_TAG_CHARS = 10
_HASH_CHARS = "#＃"


def normalize_tag(raw: Any, style: str = STYLE_PAIRED) -> str:
    """把单个标签规范化成目标风格的写法。空值或纯井号返回空串。"""
    if style == STYLE_NONE:
        return ""
    text = clean(raw).strip(_HASH_CHARS).strip()
    if not text:
        return ""
    if style == STYLE_PAIRED:
        return f"#{text}#"
    return f"#{text}"


def normalize_tags(
    tags: Iterable[Any] | None,
    *,
    style: str = STYLE_PAIRED,
    min_count: int = 0,
    max_count: int = 0,
    fallback_topic: str | None = None,
) -> tuple[list[str], list[str]]:
    """规范化整组标签。

    Returns:
        (规范化后的标签列表, 人类可读的调整说明列表)
    """
    notes: list[str] = []
    raw_list = list(tags) if tags else []

    if style == STYLE_NONE:
        if raw_list:
            notes.append(
                f"该渠道不使用话题标签，已清空 {len(raw_list)} 个"
                "（渠道配置 require_hashtags 为 false）"
            )
        return [], notes

    if style not in STYLES:
        style = STYLE_PAIRED

    cleaned: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for raw in raw_list:
        tag = normalize_tag(raw, style)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        cleaned.append(tag)

    if duplicates:
        notes.append(f"去除重复标签 {duplicates} 个")

    if max_count and len(cleaned) > max_count:
        notes.append(
            f"标签超量（{len(cleaned)} 个 > 上限 {max_count}），已保留前 {max_count} 个"
        )
        cleaned = cleaned[:max_count]

    if min_count and len(cleaned) < min_count and fallback_topic:
        filler_source = clip(fallback_topic, FALLBACK_TAG_CHARS)
        filler = normalize_tag(filler_source, style)
        if filler and filler.casefold() not in seen:
            cleaned.append(filler)
            notes.append(
                f"标签不足 {min_count} 个，已按主题补 {filler}（建议换成平台上真实在跑的话题）"
            )

    return cleaned, notes


def rules_from_channel(channel: dict) -> dict[str, Any]:
    """从渠道配置推导标签规则。

    显式 ``hashtag_style`` / ``hashtag_min`` / ``hashtag_max`` 优先；
    缺省时按 ``require_hashtags`` 推断（需要则为 paired，不需要则清空）。
    """
    style = str(channel.get("hashtag_style") or "").strip().lower()
    if style not in STYLES:
        style = STYLE_PAIRED if channel.get("require_hashtags") else STYLE_NONE

    def _as_int(key: str, default: int) -> int:
        value = channel.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return {
        "style": style,
        "min_count": _as_int("hashtag_min", 0),
        "max_count": _as_int("hashtag_max", 0),
    }
