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
    "你是品牌内容偏好分析师。用户编辑了 AI 生成的文案，程序已检测出用户实际修改点。"
    "请仅基于这些修改点，提炼不超过 2 条、可直接执行的品牌内容偏好规则。"
    "若修改点是内容事实（如保温时长、价格、成分、规格），规则应表述为品牌必须采用的事实口径。"
    "禁止推测或编造用户未修改的内容。只输出 JSON：{\"rules\": [\"...\"]}。"
)


def _diff_text(label: str, a: str, b: str) -> list[str]:
    """子串级差异：只输出实际变更片段（合并相邻块、带少量上下文）。

    equal 块跳过、相邻变更块合并，每个变更块附前后 4 字符上下文，
    避免把未修改的长文本喂给 LLM（防止其臆造与用户编辑无关的规则）。
    """
    import difflib

    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    spans = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    if not spans:
        return []
    merged: list[list[int]] = [list(spans[0])]
    for s in spans[1:]:
        if s[0] - merged[-1][1] < 10 and s[2] - merged[-1][3] < 10:
            merged[-1][1], merged[-1][3] = s[1], s[3]
        else:
            merged.append(list(s))
    ctx = 4
    out: list[str] = []
    for ai1, ai2, bj1, bj2 in merged:
        out.append(
            f"{label}: 「{a[max(0, ai1-ctx):min(len(a), ai2+ctx)]}」→「{b[max(0, bj1-ctx):min(len(b), bj2+ctx)]}」"
        )
    return out


def diff_variants(ai_variants: list, user_variants: list) -> list[str]:
    """程序化定位 AI 原版与用户定稿的差异，返回人类可读的修改点描述。

    按变体索引配对、逐字段对比（忽略 notes 说明字段）；字符串字段做子串级
    diff（只保留实际变更片段），非字符串字段整值对比。返回空列表 = 无实质差异。
    """
    diffs: list[str] = []
    n = max(len(ai_variants), len(user_variants))
    for i in range(n):
        a = ai_variants[i] if i < len(ai_variants) else None
        u = user_variants[i] if i < len(user_variants) else None
        if isinstance(a, dict) and isinstance(u, dict):
            for field in sorted(set(a) | set(u)):
                if field == "notes":
                    continue
                av, uv = a.get(field), u.get(field)
                if av == uv:
                    continue
                label = f"变体{i+1}.{field}"
                if isinstance(av, str) and isinstance(uv, str):
                    diffs.extend(_diff_text(label, av, uv))
                else:
                    diffs.append(
                        f"{label}: {json.dumps(av, ensure_ascii=False)} → {json.dumps(uv, ensure_ascii=False)}"
                    )
        elif a != u:
            diffs.append(
                f"变体{i+1}: {json.dumps(a, ensure_ascii=False)} → {json.dumps(u, ensure_ascii=False)}"
            )
    return diffs


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
    """对比 AI 原版与用户定稿，用 LLM 提取偏好规则；失败返回空列表。

    仅把程序检测出的"实际修改点"喂给 LLM（而非全量文案对比），避免 LLM
    从未修改内容中臆造规则（真机实测：全量对比会按示例模板编造
    "标题避免感叹号"等与用户编辑无关的规则）。
    """
    if not ai_variants or not user_variants:
        return []
    diffs = diff_variants(ai_variants, user_variants)
    if not diffs:
        return []
    messages = [
        {"role": "system", "content": RULES_SYSTEM},
        {
            "role": "user",
            "content": (
                "用户实际修改点：\n"
                + "\n".join(f"- {d}" for d in diffs)
                + "\n\n注意：仅基于以上修改点提炼规则，用户未修改的内容不得作为规则来源。"
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
            "ORDER BY (source = 'feedback') DESC, ABS(strength) DESC, id DESC LIMIT ?",
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
