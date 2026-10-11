"""字数口径——全局唯一真源。

曾经项目里存在两套口径：`channels.yaml` 的 `max_chars`（正文语义）与
`evals/metrics.py` 的 `CHANNEL_MAX_CHARS`（把标题+标签+备注合并后比较）。
本模块把口径收敛到一处；P5 起评测脚本（`evals/metrics.py`）也已改为
直接从这里取 `count_variant()` 与 `mode_from_channel()`，第二套口径已删除。

口径定义（与内化的微博确定性引擎一致）：

- 主判定口径 ``raw`` = ``len(text)``，**含标点、话题标签、@ 与换行**
- 英文按**字符**计，不按词
- ``variants[].body`` 受渠道 ``max_chars`` 约束；``title`` / ``hashtags``
  单独统计，不参与 body 判定
"""
from __future__ import annotations

from typing import Any, Literal

CountMode = Literal["raw", "no_whitespace"]

DEFAULT_MODE: CountMode = "raw"


def count_chars(text: str, mode: CountMode = DEFAULT_MODE) -> int:
    """按指定口径统计字符数。

    Args:
        text: 待统计文本；None / 空串返回 0。
        mode: ``raw`` 含所有字符（含空白与换行）；
              ``no_whitespace`` 剔除所有空白字符（对齐微博长文版口径）。
    """
    if not text:
        return 0
    if mode == "raw":
        return len(text)
    if mode == "no_whitespace":
        return sum(1 for ch in text if not ch.isspace())
    raise ValueError(f"未知字数口径: {mode!r}，可选 raw / no_whitespace")


def visual_width(text: str) -> float:
    """视觉宽度估算：东亚全角字符计 1.0，其余计 0.5。仅用于报告展示。"""
    width = 0.0
    for ch in text:
        width += 0.5 if ord(ch) < 0x2E80 else 1.0
    return width


def hashtags_to_text(hashtags: Any) -> str:
    """把 hashtags 字段统一成用于统计的文本（非 list 时按单值处理）。"""
    if hashtags is None:
        return ""
    if isinstance(hashtags, (list, tuple)):
        return " ".join(str(tag) for tag in hashtags if str(tag).strip())
    return str(hashtags)


def count_variant(variant: dict[str, Any], mode: CountMode = DEFAULT_MODE) -> dict[str, int]:
    """分项统计一条文案变体的字数。

    返回键：
        body       正文长度（**唯一受渠道 max_chars 约束的项**）
        title      标题长度
        hashtags   话题标签总长度
        notes      模型自述长度（不对外发布）
        published  body + title + hashtags（实际投放文本）
        total      published + notes
    """
    body = str(variant.get("body") or "")
    title = str(variant.get("title") or "")
    tags_text = hashtags_to_text(variant.get("hashtags"))
    notes = str(variant.get("notes") or "")

    body_chars = count_chars(body, mode)
    title_chars = count_chars(title, mode)
    tag_chars = count_chars(tags_text, mode)
    notes_chars = count_chars(notes, mode)
    published = body_chars + title_chars + tag_chars

    return {
        "body": body_chars,
        "title": title_chars,
        "hashtags": tag_chars,
        "notes": notes_chars,
        "published": published,
        "total": published + notes_chars,
    }


def mode_from_channel(channel: dict) -> CountMode:
    """从渠道配置读取字数口径，缺省为 raw。"""
    mode = str(channel.get("char_count_mode") or DEFAULT_MODE).strip().lower()
    return mode if mode in ("raw", "no_whitespace") else DEFAULT_MODE  # type: ignore[return-value]
