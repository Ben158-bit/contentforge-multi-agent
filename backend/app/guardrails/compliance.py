"""违禁词 / 广告法词表检出。

内化自微博确定性引擎的 ``scan()``：按词长降序匹配、区间遮罩避免重复命中、
支持序数豁免（「第一次」不算绝对化的「第一」）。在其之上补了两点：

1. **英文单词边界**：纯 ASCII 字母的词要求两侧为非字母数字，
   避免 ``Glasswing`` 里的 ``ass`` 这类子串误匹配。
2. **成稿与输入素材分开统计**：通过 ``scope`` 参数区分，
   只有成稿命中参与合规判定（对齐微博引擎的做法）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

RULES_DIR = Path(__file__).resolve().parent / "rules"
WORDS_FILE = RULES_DIR / "forbidden_words.yaml"

Scope = Literal["draft", "source"]

CONTEXT_PAD = 10
_ASCII_ALPHA_RE = re.compile(r"^[A-Za-z]+$")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class WordEntry:
    word: str
    category: str
    suggestion: str
    basis: str
    severity: str = "high"
    exempt_next: str = ""


@dataclass(frozen=True)
class Wordlist:
    entries: tuple[WordEntry, ...]
    category_labels: dict[str, str] = field(default_factory=dict)

    def by_category(self) -> dict[str, list[WordEntry]]:
        grouped: dict[str, list[WordEntry]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.category, []).append(entry)
        return grouped


@dataclass
class WordHit:
    scope: str
    category: str
    word: str
    severity: str
    start: int
    end: int
    context: str
    suggestion: str
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "category": self.category,
            "word": self.word,
            "severity": self.severity,
            "context": self.context,
            "suggestion": self.suggestion,
            "basis": self.basis,
        }


@lru_cache
def load_wordlist() -> Wordlist:
    """加载词表资产（进程内缓存）。"""
    with open(WORDS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_categories = data.get("categories") or {}
    labels = {
        str(key): str(value.get("label", key)) if isinstance(value, dict) else str(key)
        for key, value in raw_categories.items()
    }
    default_severity = {
        str(key): (
            str(value.get("severity", "high")) if isinstance(value, dict) else "high"
        )
        for key, value in raw_categories.items()
    }
    default_basis = {
        str(key): (
            str(value.get("basis", "")) if isinstance(value, dict) else ""
        )
        for key, value in raw_categories.items()
    }

    entries: list[WordEntry] = []
    for item in data.get("entries") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or "").strip()
        if not word:
            continue
        category = str(item.get("category") or "platform_prohibited")
        entries.append(
            WordEntry(
                word=word,
                category=category,
                suggestion=str(item.get("suggestion") or "换一种说法或直接删除"),
                basis=str(item.get("basis") or default_basis.get(category, "")),
                severity=str(item.get("severity") or default_severity.get(category, "high")),
                exempt_next=str(item.get("exempt_next") or ""),
            )
        )
    if not entries:
        raise RuntimeError(f"违禁词表为空: {WORDS_FILE}")
    return Wordlist(entries=tuple(entries), category_labels=labels)


def _needs_word_boundary(word: str) -> bool:
    return bool(_ASCII_ALPHA_RE.match(word))


def _boundary_ok(text: str, start: int, end: int) -> bool:
    """英文词要求两侧不是字母数字，避免子串误匹配。"""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before and _ALNUM_RE.match(before):
        return False
    if after and _ALNUM_RE.match(after):
        return False
    return True


def _build_rules(
    wordlist: Wordlist,
    user_banned: Iterable[str] | None,
) -> list[WordEntry]:
    """合并内置词表与用户禁用词，按词长降序（长词优先，避免短词吃掉长词）。"""
    rules = list(wordlist.entries)
    for word in user_banned or []:
        cleaned = str(word or "").strip()
        if cleaned and not any(rule.word == cleaned for rule in rules):
            rules.append(
                WordEntry(
                    word=cleaned,
                    category="user_banned",
                    suggestion="用户自定义禁用词：换一种说法或直接删除",
                    basis="用户禁用词",
                )
            )
    rules.sort(key=lambda rule: len(rule.word), reverse=True)
    return rules


def scan_text(
    text: str,
    *,
    scope: Scope = "draft",
    wordlist: Wordlist | None = None,
    user_banned: Iterable[str] | None = None,
) -> list[WordHit]:
    """扫描一段文本，返回全部命中。

    Args:
        text: 待扫描文本（原样扫描，不做替换，保证命中位置可回溯）。
        scope: ``draft`` 成稿 / ``source`` 输入素材。只有 draft 命中参与合规判定。
        wordlist: 词表；默认使用内置资产。
        user_banned: 用户/品牌自定义禁用词（来自品牌档案）。
    """
    if not text:
        return []

    active = wordlist or load_wordlist()
    rules = _build_rules(active, user_banned)
    mask = bytearray(len(text))
    hits: list[WordHit] = []

    for rule in rules:
        word = rule.word
        length = len(word)
        search_from = 0
        while True:
            index = text.find(word, search_from)
            if index < 0:
                break
            end = index + length
            search_from = end

            # 序数豁免：如「第一次」不判为绝对化的「第一」
            if rule.exempt_next and end < len(text) and text[end] in rule.exempt_next:
                continue
            # 英文子串误匹配防护
            if _needs_word_boundary(word) and not _boundary_ok(text, index, end):
                continue
            # 区间遮罩：已被更长词覆盖则跳过
            if any(mask[index:end]):
                continue

            mask[index:end] = b"\x01" * length
            hits.append(
                WordHit(
                    scope=scope,
                    category=rule.category,
                    word=word,
                    severity=rule.severity,
                    start=index,
                    end=end,
                    context=text[max(0, index - CONTEXT_PAD) : end + CONTEXT_PAD],
                    suggestion=rule.suggestion,
                    basis=rule.basis,
                )
            )

    hits.sort(key=lambda hit: hit.start)
    return hits


def scan_texts(
    texts: Iterable[str],
    *,
    scope: Scope = "draft",
    wordlist: Wordlist | None = None,
    user_banned: Iterable[str] | None = None,
) -> list[WordHit]:
    """批量扫描多段文本（如多条变体），合并结果。"""
    results: list[WordHit] = []
    for text in texts:
        results.extend(
            scan_text(text, scope=scope, wordlist=wordlist, user_banned=user_banned)
        )
    return results


def summarize_hits(hits: Iterable[WordHit]) -> dict[str, int]:
    """按类别聚合命中数量，便于报告展示。"""
    summary: dict[str, int] = {}
    for hit in hits:
        summary[hit.category] = summary.get(hit.category, 0) + 1
    return summary


# --------------------------------------------------------------------------
# 人工填写的词表解析
# --------------------------------------------------------------------------
_USER_WORD_SEPARATORS = re.compile(r"[、,，;；.。/|｜\s]+")
MIN_USER_WORD_CHARS = 2
MAX_USER_WORD_CHARS = 12


def split_user_words(
    text: Any, *, max_chars: int = MAX_USER_WORD_CHARS
) -> list[str]:
    """把人手填写的「禁忌词/表达」自由文本切成可扫描的词表。

    为什么需要它：品牌档案里的 ``taboos`` 是自由文本，用户可能写
    ``"最,第一,根治"``，也可能写 ``"不夸大宣传、不承诺具体疗效"``——
    后者是**规则句子**，不是词。若把整句当词去做子串匹配，命中率约等于零
    （漏检），但若把长句硬拆成短片段，又会制造大量误报。

    所以策略是**宁可漏检、不可误报**：

    - 按常见分隔符切分；
    - 只保留长度在 ``[2, max_chars]`` 之间的片段（单字误报率过高，直接丢）；
    - 超长片段整条丢弃，**不**再尝试二次切分——没有分隔符的长串就是句子。

    空串、非字符串、全空白一律返回空列表。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    words: list[str] = []
    seen: set[str] = set()
    for chunk in _USER_WORD_SEPARATORS.split(text):
        word = chunk.strip()
        if not (MIN_USER_WORD_CHARS <= len(word) <= max_chars):
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        words.append(word)
    return words
