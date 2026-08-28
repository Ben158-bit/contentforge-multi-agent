"""记忆层服务：品牌档案上下文读取 + 纠错学习（偏好规则提取）。

纠错学习（用户确认时触发，显式勾选才学）：
1. 对比 AI 原版 variants 与用户提交 variants（忽略 notes 字段）
2. 无差异 → 不学习（省 LLM 调用）
3. 有差异 → LLM 提取偏好规则（≤2 条、可直接执行）
4. 提取失败/超时 → 静默降级（不阻塞确认流程）

品牌上下文读取：供创作节点同步注入（与编排执行路径一致，用 sqlite3 直连）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .config import get_llm_client
from .db_sql import conn as _conn, exec as _exec

logger = logging.getLogger(__name__)

RULES_SYSTEM = (
    "你是品牌内容偏好分析师。对比 AI 生成的营销文案与用户最终定稿，"
    "提炼出不超过 2 条、可直接执行的品牌内容偏好规则。"
    "规则必须具体且可执行（例如：标题避免感叹号；正文不出现'限时促销'话术；"
    "用'老用户专享'替代'立减'表达）。只输出 JSON：{\"rules\": [\"...\"]}。"
)


def variants_differ(ai_variants: list, user_variants: list) -> bool:
    """判断用户提交的文案与 AI 原版是否有实质差异（忽略 notes 等说明字段）。"""

    def _norm(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: _norm(x) for k, x in v.items() if k not in ("notes",)}
        if isinstance(v, list):
            return [_norm(x) for x in v]
        return v

    return _norm(ai_variants) != _norm(user_variants)


def extract_rules(llm, ai_variants: list, user_variants: list) -> list[str]:
    """对比 AI 原版与用户定稿，用 LLM 提取偏好规则；失败返回空列表。"""
    if not ai_variants or not user_variants:
        return []
    messages = [
        {"role": "system", "content": RULES_SYSTEM},
        {
            "role": "user",
            "content": (
                "AI 生成的文案变体：\n"
                + json.dumps(ai_variants, ensure_ascii=False, indent=2)
                + "\n\n用户最终确认的文案变体：\n"
                + json.dumps(user_variants, ensure_ascii=False, indent=2)
            ),
        },
    ]
    try:
        result = llm.chat(messages, json_mode=True, temperature=0.2)
        payload = json.loads(result.content)
        rules = [str(r).strip() for r in payload.get("rules", []) if str(r).strip()]
        return rules[:2]
    except Exception:
        logger.warning("偏好规则提取失败，静默降级（不影响确认流程）", exc_info=True)
        return []


def learn_from_confirmation(
    brand_id: int, ai_variants: list, user_variants: list,
    source_task: Optional[int] = None,
) -> list[str]:
    """确认时学习：有差异才提取规则并入库，返回新增规则列表（空 = 未学习）。

    同步函数（由 to_thread 包裹后调用，入库用 sqlite3 直连）。
    """
    if not brand_id or not variants_differ(ai_variants, user_variants):
        return []
    rules = extract_rules(get_llm_client(), ai_variants, user_variants)
    if not rules:
        return []
    conn = _conn()
    try:
        for rule in rules:
            conn.execute(
                "INSERT INTO brand_preferences (brand_id, rule_text, source_task) "
                "VALUES (?, ?, ?)",
                (brand_id, rule, source_task),
            )
        conn.commit()
    finally:
        conn.close()
    logger.info("品牌 %s 纠错学习：新增 %d 条偏好规则", brand_id, len(rules))
    return rules


def get_brand_context(brand_id: Optional[int], max_rules: int = 5) -> Optional[dict]:
    """读取品牌档案与最近偏好规则（同步，供创作节点注入）。无品牌/无档案返回 None。"""
    if not brand_id:
        return None
    conn = _conn()
    try:
        brand = conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone()
        if brand is None:
            return None
        prefs = conn.execute(
            "SELECT rule_text FROM brand_preferences WHERE brand_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (brand_id, max_rules),
        ).fetchall()
        return {
            "id": brand["id"],
            "name": brand["name"],
            "tone": brand["tone"],
            "core_claims": brand["core_claims"],
            "audience": brand["audience"],
            "taboos": brand["taboos"],
            "preferences": [p["rule_text"] for p in prefs],
        }
    finally:
        conn.close()
