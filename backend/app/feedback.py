"""效果闭环：程序化特征提取 + LLM 规律归纳 + 入库合并（含模拟数据生成）。

核心原则（对齐记忆层 P1 教训）：特征提取完全程序化（不依赖 LLM），
LLM 只做规律归纳且输入仅限真实统计；任何失败静默降级，不阻断主流程。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional

from app import repository as repo
from app.config import get_llm_client

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


_FEATURE_LABELS = {
    "has_num": "标题含数字",
    "has_question": "标题用提问句式",
    "has_emoji": "标题含 emoji",
    "has_emotion_word": "标题含情绪词",
    "bullet_count": "正文要点式分段",
    "has_sources": "正文附来源引用",
}


def _strength_from_stats(high_ratio: float, low_ratio: float) -> float:
    """由高/低分组出现率差异映射强度（-2~+2），0 表示无差异。"""
    diff = high_ratio - low_ratio
    return round(max(-2.0, min(2.0, diff * 4.0)), 2)


def _feature_stats(high_group: list[dict], low_group: list[dict]) -> dict[str, tuple[float, float]]:
    """输入高/低分组特征列表，输出 {feature: (高分组出现率, 低分组出现率)}。"""
    if not high_group or not low_group:
        return {}
    stats: dict[str, tuple[float, float]] = {}
    for feat in _FEATURE_LABELS:
        h = sum(1 for f in high_group if f.get(feat)) / len(high_group)
        l = sum(1 for f in low_group if f.get(feat)) / len(low_group)
        stats[feat] = (round(h, 2), round(l, 2))
    return stats


_RULES_SYSTEM = (
    "你是内容效果分析师。根据给定的特征统计表（各特征在高分内容/低分内容中的出现率），"
    "归纳 1-3 条可指导下次创作的效果规律。只允许基于表格数据，禁止臆造。"
    "输出 JSON：{\"rules\":[{\"feature\":\"特征名\",\"text\":\"一句话规律\"}]}"
)


def derive_feedback_rules(
    stats: dict[str, tuple[float, float]], llm: Optional[Callable] = None,
) -> list[dict]:
    """LLM 归纳规律文本；strength 由程序化计算（LLM 不产生数值）。失败返回空列表。"""
    candidates = [
        (feat, h, l) for feat, (h, l) in stats.items()
        if abs(h - l) >= 0.2  # 只把有区分度的特征喂给 LLM
    ]
    if not candidates:
        return []
    llm = llm or get_llm_client()
    table = "\n".join(
        f"- {_FEATURE_LABELS[feat]}：高分 {h:.0%} / 低分 {l:.0%}" for feat, h, l in candidates)
    try:
        result = llm.chat(
            [{"role": "system", "content": _RULES_SYSTEM},
             {"role": "user", "content": f"特征统计表：\n{table}"}],
            json_mode=True, temperature=0.2,
        )
        payload = json.loads(result.content)
    except Exception:  # 静默降级：LLM 失败不阻断主流程
        logger.warning("效果规律归纳失败，跳过本轮学习", exc_info=True)
        return []
    rules = []
    for r in payload.get("rules", [])[:3]:
        feat = r.get("feature")
        if feat not in _FEATURE_LABELS:
            continue
        h, l = stats[feat]
        strength = _strength_from_stats(h, l)
        if strength == 0:
            continue
        rules.append({
            "feature": feat, "text": r.get("text", ""),
            "strength": strength, "source": "feedback",
        })
    return rules


async def learn_from_feedback(brand_id: int, rules: list[dict]) -> int:
    """效果规律入库；与已有同文本 feedback 规则合并（衰减加权）。返回新增条数。"""
    added = 0
    for r in rules:
        text = r.get("text", "").strip()
        if not text or r.get("source") != "feedback":
            continue
        merged = await repo.merge_feedback_rule(brand_id, text, r.get("strength", 1.0))
        if merged:
            added += 1
    logger.info("品牌 %s 效果学习：新增 %d 条规律", brand_id, added)
    return added
