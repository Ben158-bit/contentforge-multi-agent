"""效果闭环：程序化特征提取 + LLM 规律归纳 + 入库合并（含模拟数据生成）。

核心原则（对齐记忆层 P1 教训）：特征提取完全程序化（不依赖 LLM），
LLM 只做规律归纳且输入仅限真实统计；任何失败静默降级，不阻断主流程。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 情绪词/提问/emoji 的简单特征检测（演示级，不追求完备）
_QUESTION_RE = re.compile(r"[？?]")
_NUM_RE = re.compile(r"[0-9０-９]")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_EMOTION_WORDS = ("惊喜", "后悔", "踩坑", "必看", "干货", "避雷", "种草", "绝了", "救命")


def _title_of(content: dict) -> str:
    t = content.get("title") or ""
    return t if isinstance(t, str) else str(t)


def _body_of(content: dict) -> str:
    b = content.get("body") or ""
    return b if isinstance(b, str) else str(b)


def extract_features(content: dict) -> dict[str, Any]:
    """对单条内容做程序化特征提取（输入仅为真实内容，不调用 LLM）。"""
    title = _title_of(content)
    body = _body_of(content)
    # 要点数：行首序号（1./第一/·/-/*）计为要点
    bullet_count = len(re.findall(
        r"(?:^|\n)\s*(?:\d+[\.、]|第?[一二三四五六七八九十百]+|[·\-*])", body))
    return {
        "title_len": len(title),
        "has_num": 1 if _NUM_RE.search(title) else 0,
        "has_question": 1 if _QUESTION_RE.search(title) else 0,
        "has_emoji": 1 if _EMOJI_RE.search(title) else 0,
        "has_emotion_word": 1 if any(w in title for w in _EMOTION_WORDS) else 0,
        "bullet_count": bullet_count,
        "has_sources": 1 if content.get("sources") else 0,
    }
