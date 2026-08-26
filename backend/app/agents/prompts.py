"""Prompt 模板体系。

5 个 Agent 的 system/user 消息构建函数。所有 Agent 均要求
结构化 JSON 输出（配合 LLM 的 json_mode），输出 schema 见各函数 docstring。

约定：所有 build_*_messages 返回 OpenAI 消息列表
[{"role": "system", "content": ...}, {"role": "user", "content": ...}]。
"""
from __future__ import annotations

import json
from typing import Any

from .state import AgentState


def _sys(role: str, duties: str) -> str:
    return (
        f"你是营销内容生产流水线中的【{role}】。\n{duties}\n"
        "你只能输出一个合法的 JSON 对象，不要输出任何多余的文字、解释或 Markdown 代码块。"
    )


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# ===================== 1. 市场调研 Agent =====================

def build_research_messages(
    state: AgentState, search_results: list | None = None
) -> list[dict[str, str]]:
    """输出 schema:
    {"summary": str, "trends": [str], "audience_insights": [str],
     "pain_points": [str], "keywords": [str],
     "sources": [{"title": str, "url": str}]}

    Args:
        search_results: 联网检索结果（SearchResult 列表），注入后调研有真实依据。
            为空时退化为模型内部知识。
    """
    inp = state["input"]
    system = _sys(
        "市场调研分析师",
        "负责对指定主题/品牌进行市场调研，输出：行业趋势、目标受众洞察、用户痛点、"
        "搜索关键词。优先基于提供的联网检索资料作答，结论要具体、可执行，"
        "避免空泛套话。",
    )
    sources_section = ""
    if search_results:
        lines = "\n".join(
            f"- 标题：{r.title}\n  来源：{r.url}\n  摘要：{(r.snippet or '')[:200]}"
            for r in search_results
        )
        sources_section = (
            "\n\n以下是联网检索到的参考资料（必须优先基于这些资料得出结论，"
            "并在输出 JSON 的 sources 字段中引用你实际使用到的来源）：\n"
            f"{lines}"
        )
    user = (
        f"请针对以下营销任务完成市场调研：\n"
        f"- 营销主题：{inp.get('topic', '')}\n"
        f"- 品牌/产品：{inp.get('brand_name', '未指定')}\n"
        f"- 目标受众：{inp.get('target_audience', '未指定')}\n"
        f"- 目标渠道：{inp.get('channel_id', '')}\n"
        f"- 附加要求：{inp.get('extra_requirements', '无') or '无'}"
        f"{sources_section}\n\n"
        f"请输出 JSON：{{\"summary\": \"调研摘要(200字内)\", "
        f"\"trends\": [\"3-5条行业趋势\"], \"audience_insights\": [\"3-5条受众洞察\"], "
        f"\"pain_points\": [\"3-5条用户痛点\"], \"keywords\": [\"5-8个搜索关键词\"], "
        f"\"sources\": [{{\"title\": \"资料来源标题\", \"url\": \"资料来源链接\"}}]}}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ===================== 2. 竞品分析 Agent =====================

def build_competitor_messages(state: AgentState) -> list[dict[str, str]]:
    """输出 schema:
    {"competitors": [{"name", "positioning", "selling_points", "content_strategy",
                      "strengths", "weaknesses"}], "gaps": [str]}
    """
    inp = state["input"]
    research = state.get("research", {})
    system = _sys(
        "竞品分析专家",
        "基于市场调研结果，拆解 3-5 个代表性竞品的定位、卖点、内容策略，"
        "并指出差异化机会点。分析要具体到可指导后续策略。",
    )
    user = (
        f"营销主题：{inp.get('topic', '')}，品牌：{inp.get('brand_name', '未指定')}。\n"
        f"市场调研摘要：{research.get('summary', '无')}\n"
        f"行业趋势：{json.dumps(research.get('trends', []), ensure_ascii=False)}\n\n"
        f"请输出 JSON：{{\"competitors\": [{{\"name\": \"竞品名\", "
        f"\"positioning\": \"定位\", \"selling_points\": [\"核心卖点\"], "
        f"\"content_strategy\": \"其内容策略\", \"strengths\": [\"优势\"], "
        f"\"weaknesses\": [\"劣势\"]}}], \"gaps\": [\"2-4个差异化机会点\"]}}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ===================== 3. 策略制定 Agent =====================

def build_strategy_messages(state: AgentState) -> list[dict[str, str]]:
    """输出 schema:
    {"target_audience": str, "core_value_proposition": str, "key_messages": [str],
     "tone_guidance": str, "content_angles": [str], "channel_strategy": str}
    """
    inp = state["input"]
    research = state.get("research", {})
    competitor = state.get("competitor", {})
    system = _sys(
        "营销策略总监",
        "综合市场调研与竞品分析，为指定渠道制定内容营销策略：精确定义目标受众、"
        "提炼核心价值主张与关键信息、明确语调与内容角度。策略要可直接指导文案创作。",
    )
    user = (
        f"营销主题：{inp.get('topic', '')}，品牌：{inp.get('brand_name', '未指定')}，"
        f"目标渠道：{inp.get('channel_id', '')}，目标受众：{inp.get('target_audience', '未指定')}。\n"
        f"市场调研：{_dump(research)}\n竞品分析：{_dump(competitor)}\n"
        f"附加要求：{inp.get('extra_requirements', '无') or '无'}\n\n"
        f"请输出 JSON：{{\"target_audience\": \"精确定义的目标受众画像\", "
        f"\"core_value_proposition\": \"一句话核心价值主张\", "
        f"\"key_messages\": [\"3-5条关键信息\"], \"tone_guidance\": \"语调与文风指南\", "
        f"\"content_angles\": [\"3-5个内容角度\"], "
        f"\"channel_strategy\": \"针对该渠道的发布策略要点\"}}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ===================== 4. 文案创作 Agent =====================

def build_copywriting_messages(state: AgentState, channel: dict) -> list[dict[str, str]]:
    """输出 schema:
    {"variants": [{"title": str, "body": str, "hashtags": [str], "notes": str}]}
    """
    inp = state["input"]
    strategy = state.get("strategy", {})
    feedback = state.get("review_feedback", "")
    feedback_section = (
        f"\n上一轮审校未通过，请严格按以下反馈修改文案：\n{feedback}" if feedback else ""
    )
    guide = channel.get("copywriting_guide") or {}
    guide_section = "\n".join(
        f"- {k}: {v}" for k, v in guide.items()
    ) if guide else "（无额外指南）"
    system = _sys(
        "文案创作专家",
        "依据策略与渠道规范创作营销文案，一次输出 2-3 个风格不同的变体。"
        "文案必须覆盖核心卖点、贴合渠道语调与结构要求、自然融入关键词。",
    )
    user = (
        f"营销主题：{inp.get('topic', '')}，品牌：{inp.get('brand_name', '未指定')}。\n"
        f"策略：{_dump(strategy)}\n"
        f"渠道规范：\n{_dump(channel)}\n"
        f"渠道文案方法论（必须遵守）：\n{guide_section}\n"
        f"附加要求：{inp.get('extra_requirements', '无') or '无'}"
        f"{feedback_section}\n\n"
        f"请输出 JSON：{{\"variants\": [{{\"title\": \"标题\", "
        f"\"body\": \"正文(遵守渠道 max_chars 限制)\", "
        f"\"hashtags\": [\"话题标签(如渠道要求)\"], "
        f"\"notes\": \"创作思路说明(100字内)\"}}]}}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ===================== 5. 审校优化 Agent =====================

def build_review_messages(
    state: AgentState, channel: dict, variants: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """输出 schema:
    {"passed": bool, "feedback": str,
     "checklist": [{"item": str, "passed": bool, "note": str}]}
    """
    inp = state["input"]
    strategy = state.get("strategy", {})
    guide = channel.get("copywriting_guide") or {}
    guide_section = "\n".join(
        f"- {k}: {v}" for k, v in guide.items()
    ) if guide else "（无额外指南）"
    system = _sys(
        "内容审校专家",
        "对照策略与渠道规范逐条检查文案质量：卖点覆盖、渠道适配（语调/长度/结构）、"
        "关键词植入、合规与情感表达。不通过时必须给出具体、可执行的修改建议。",
    )
    user = (
        f"营销主题：{inp.get('topic', '')}，品牌：{inp.get('brand_name', '未指定')}，"
        f"目标渠道：{inp.get('channel_id', '')}。\n"
        f"策略要求：{_dump(strategy)}\n渠道规范：{_dump(channel)}\n"
        f"渠道文案方法论（审校依据）：\n{guide_section}\n"
        f"待审文案变体：\n{_dump(variants)}\n"
        f"附加要求：{inp.get('extra_requirements', '无') or '无'}\n\n"
        f"请输出 JSON：{{\"passed\": true或false, "
        f"\"feedback\": \"不通过时的具体修改建议(200字内)\", "
        f"\"checklist\": [{{\"item\": \"检查项\", \"passed\": true或false, "
        f"\"note\": \"说明\"}}]}}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
