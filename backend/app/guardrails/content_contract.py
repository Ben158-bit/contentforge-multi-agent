"""内容契约核验（确定性，零 token）。

## 为什么需要这一层

``templates/channels.yaml`` 的 ``copywriting_guide`` 把渠道方法论写得很细
（小红书标题 18-20 字公式、四段式结构、「2-3 个热门大标签 + 3-5 个精准长尾标签」……），
但它**只是提示词里的一段话** —— 没有任何机制检查模型是否照做。
「写了规则」和「执行了规则」之间那条缝，就是内容质量的漏损点：
规则越具体，没人核验时就越像一句建议。

本模块把 guide 里**可穷举**的部分抽成结构化契约（``content_contract``，声明在渠道配置里），
由守门层逐项核验。

## 边界（与守门层其余部分保持一致）

- 只**标记**，不改写、不补全：缺 ``cta`` 不会替你写一个，只会报出来；
- **不影响报告里的 ``passed``**：``passed`` 表达的是硬合规（字数 / 违禁词 / 致命 AI 味）。
  「内容不够好」与「不能交付」是两件事，判断前者是 review 节点的职责
  （缺口会随守门摘要进 review 提示词）；
- 未声明 ``content_contract`` 的渠道**完全不核验**（不推断、不兜底）——
  与守门参数的「缺省推断」不同：契约是「要求」，没有要求就没有缺口。

## 五种 kind

| kind | 要求 |
|---|---|
| ``text`` | 非空字符串 |
| ``list`` | 数组，数量在 ``[min, max]``；带 ``item_fields`` 时每项都必须含这些子字段 |
| ``enum`` | 取值必须落在 ``values`` 里 |
| ``tiered`` | 分层对象，每层数量在各自 ``[min, max]`` |
| ``headings`` | 字符串数组，数量 ≥ ``min``，且**每个小标题都必须真的出现在正文里** |

## 标签的单一来源（``feeds``）

``tiered`` 条目可以声明 ``feeds: hashtags``，表示该字段是标签的**唯一来源**：
模型只输出分层结构，``hashtags`` 由 :func:`flatten_tiered_tags` 扁平化得到。
不这么做的话「hashtags」与「分层结果」会成为两处真源，迟早互相打架 ——
而「按层核验标签配比」恰恰是本模块存在的理由之一，两处真源会让它自相矛盾。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .hashtags import normalize_tags, rules_from_channel

KIND_TEXT = "text"
KIND_LIST = "list"
KIND_ENUM = "enum"
KIND_TIERED = "tiered"
KIND_HEADINGS = "headings"

KNOWN_KINDS = frozenset({KIND_TEXT, KIND_LIST, KIND_ENUM, KIND_TIERED, KIND_HEADINGS})

# ``feeds`` 只能写入这些**标准变体字段**。做成白名单是为了让「配置里把字段名写错」
# 变成加载期的显式失败，而不是一个永远不生效的静默配置。
FEED_TARGETS = frozenset({"hashtags"})

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ContractCheck:
    """单条契约项的核验结果。``ok=False`` 即缺口。"""

    field: str
    label: str
    kind: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "label": self.label,
            "kind": self.kind,
            "ok": self.ok,
            "detail": self.detail,
        }

    def summary(self) -> str:
        """给人读的一行（进 ``needs_human``）。"""
        return f"内容契约「{self.label}」{self.detail}" if self.detail else f"内容契约「{self.label}」不满足"


# ==========================================================================
# 契约资产
# ==========================================================================
def contract_from_channel(channel: dict) -> list[dict]:
    """取渠道声明的契约条目；未声明（或声明为空）返回空列表 = 不核验。"""
    raw = channel.get("content_contract")
    if not isinstance(raw, (list, tuple)):
        return []
    return [
        dict(item)
        for item in raw
        if isinstance(item, dict) and item.get("field")
    ]


def tiered_tags_entry(channel: dict) -> dict | None:
    """返回「feeds 到标准标签字段」的 tiered 条目；没有则 None。"""
    for item in contract_from_channel(channel):
        if item.get("kind") == KIND_TIERED and item.get("feeds") in FEED_TARGETS:
            return item
    return None


def tag_source_field(channel: dict) -> str | None:
    """返回本渠道「标签唯一来源」的字段名；没有则 None。"""
    entry = tiered_tags_entry(channel)
    return str(entry["field"]) if entry else None


def flatten_tiered_tags(value: Any, entry: dict) -> list[str]:
    """按 ``tiers`` 声明顺序把分层标签扁平化成一个列表。

    顺序即「大标签在前、长尾在后」——这与创作提示词里给模型的顺序一致，
    保证「标签顺序」在两个消费者眼里含义相同。
    """
    if not isinstance(value, dict):
        return []
    out: list[str] = []
    for tier in entry.get("tiers") or []:
        if not isinstance(tier, dict):
            continue
        items = value.get(tier.get("key"))
        if isinstance(items, (list, tuple)):
            out.extend(str(item).strip() for item in items if str(item).strip())
        elif isinstance(items, str) and items.strip():
            out.append(items.strip())
    return out


def derive_hashtags(variant: dict[str, Any], channel: dict) -> list[str]:
    """从分层标签结构推导 ``hashtags``。

    渠道未声明 tiered 标签来源、或模型没填出来时返回空列表
    （**返回空 ≠ 应该把标签清空**，调用方要区分这两件事）。
    """
    entry = tiered_tags_entry(channel)
    if entry is None:
        return []
    return flatten_tiered_tags(variant.get(entry["field"]), entry)


def styled_tiered_hashtags(variant: dict[str, Any], channel: dict) -> list[str]:
    """分层标签 → **可发布形态**（扁平化 + 按渠道样式补井号 / 截断）。

    ``min_count`` 传 0：**刻意不做兜底补标签**。兜底是「数量不足时按主题补一个」
    的补救动作，属于守门层归一化阶段（它手里有主题），创作节点出口不该凭空多出标签。
    """
    bare = derive_hashtags(variant, channel)
    if not bare:
        return []
    rules = rules_from_channel(channel)
    styled, _ = normalize_tags(
        bare, style=rules["style"], min_count=0, max_count=rules["max_count"]
    )
    return styled


def apply_derived_hashtags(variant: dict[str, Any], channel: dict) -> bool:
    """把推导出的标签写进 ``variant['hashtags']``（标签的**唯一来源**）。

    在**创作节点出口**就调用一次。理由是一次真实端到端跑出来的教训：
    只在守门层推导时，``copywriting`` 阶段的产出里 ``hashtags`` 是空的 ——
    而前端变体编辑器、导出、下游读的都是**那一份**，
    于是出现「守门层核验通过、界面上标签却是空」的断裂。
    """
    tags = styled_tiered_hashtags(variant, channel)
    if not tags:
        return False
    variant["hashtags"] = tags
    return True


# ==========================================================================
# 核验
# ==========================================================================
def _squash(text: str) -> str:
    """去掉所有空白再比较。

    守门层在归一化时会对正文重排换行（``downgrade_body`` 逐句以 ``\\n`` 连接），
    所以「小标题是否出现在正文里」不能按原样做子串匹配。
    """
    return _WHITESPACE_RE.sub("", text)


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_list(value: Any) -> list | None:
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def _count_problem(count: int, min_count: int, max_count: int) -> str:
    """返回数量不合规的说明；合规返回空串。"""
    if min_count and count < min_count:
        return f"只有 {count} 项，少于要求的 {min_count} 项"
    if max_count and count > max_count:
        return f"有 {count} 项，超出上限 {max_count} 项"
    return ""


def _check_text(value: Any) -> tuple[bool, str]:
    return (_non_empty_text(value), "" if _non_empty_text(value) else "缺失或为空")


def _check_enum(value: Any, allowed: list[str]) -> tuple[bool, str]:
    joined = "/".join(allowed)
    if not _non_empty_text(value):
        return False, f"缺失；应取值于 {joined}"
    if str(value).strip() not in allowed:
        return False, f"取值为「{value}」，不在允许集合：{joined}"
    return True, ""


def _check_list(value: Any, min_count: int, max_count: int, item_fields: list[str]) -> tuple[bool, str]:
    items = _as_list(value)
    if items is None:
        return False, "缺失或不是数组"
    problem = _count_problem(len(items), min_count, max_count)
    if problem:
        return False, problem
    if not item_fields:
        return True, ""
    broken: list[str] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            broken.append(str(index))
            continue
        if any(not _non_empty_text(item.get(name)) for name in item_fields):
            broken.append(str(index))
    if broken:
        return False, f"第 {'、'.join(broken)} 项缺子字段（需含：{'/'.join(item_fields)}）"
    return True, ""


def _check_tiered(value: Any, tiers: list[dict]) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "缺失或不是分层对象"
    problems: list[str] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        key = str(tier.get("key") or "")
        tier_label = str(tier.get("label") or key)
        items = value.get(key)
        count = len(items) if isinstance(items, (list, tuple)) else 0
        problem = _count_problem(count, int(tier.get("min") or 0), int(tier.get("max") or 0))
        if problem:
            problems.append(f"{tier_label}{problem}")
    return (not problems), "；".join(problems)


def _check_headings(value: Any, body: str, min_count: int) -> tuple[bool, str]:
    items = _as_list(value)
    if items is None:
        return False, "缺失或不是数组"
    problem = _count_problem(len(items), min_count, 0)
    if problem:
        return False, problem
    squashed_body = _squash(body)
    missing = [
        str(item)
        for item in items
        if _squash(str(item)) and _squash(str(item)) not in squashed_body
    ]
    if missing:
        return False, f"小标题未出现在正文中：{'、'.join(missing)}"
    return True, ""


def _check_one(variant: dict[str, Any], entry: dict) -> ContractCheck:
    field_name = str(entry["field"])
    label = str(entry.get("label") or field_name)
    kind = str(entry.get("kind") or KIND_TEXT)
    value = variant.get(field_name)
    min_count = int(entry.get("min") or 0)
    max_count = int(entry.get("max") or 0)

    if kind == KIND_TEXT:
        ok, detail = _check_text(value)
    elif kind == KIND_ENUM:
        ok, detail = _check_enum(value, [str(v) for v in (entry.get("values") or [])])
    elif kind == KIND_LIST:
        ok, detail = _check_list(
            value, min_count, max_count, [str(name) for name in (entry.get("item_fields") or [])]
        )
    elif kind == KIND_TIERED:
        ok, detail = _check_tiered(value, list(entry.get("tiers") or []))
    elif kind == KIND_HEADINGS:
        ok, detail = _check_headings(value, str(variant.get("body") or ""), min_count)
    else:
        # 未知 kind 必须是显式失败：静默跳过等于「配置写错也没人知道」。
        ok, detail = False, f"未知的契约 kind：{kind}"

    return ContractCheck(field=field_name, label=label, kind=kind, ok=ok, detail=detail)


def check_content_contract(variant: dict[str, Any], channel: dict) -> list[ContractCheck]:
    """逐项核验单条变体的内容契约。

    Returns:
        契约项核验结果列表；渠道未声明契约时返回空列表。
    """
    entries = contract_from_channel(channel)
    if not entries:
        return []
    return [_check_one(variant, entry) for entry in entries]


def gaps_of(checks: Iterable[ContractCheck]) -> list[ContractCheck]:
    """从核验结果里挑出缺口（``ok=False``）。"""
    return [check for check in checks if not check.ok]
