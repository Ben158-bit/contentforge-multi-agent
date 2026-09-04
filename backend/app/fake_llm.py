"""演示模式：无 API Key 时使用的假 LLM 客户端（FakeLLM）。

根据用户消息中的角色关键字识别流水线阶段，返回预置的结构化响应，
使完整流水线（含审校循环、人工确认、成本统计）无需真实 API 即可跑通。
适合：无 Key 体验、CI 测试、演示。

无状态设计（多任务并发安全）：
- 文案创作：被审校打回后（prompt 含"上一轮审校未通过"）输出含 CTA 的修订版，
  否则输出初始版（不含 CTA）。
- 内容审校：待审文案含"立即下单"（修订版特征）判通过，否则不通过。
由此自然形成"第一次审校不通过 → 打回重写 → 通过"的演示循环，
不依赖任何共享计数器。
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from .llm import LLMResult

RESEARCH = {
    "summary": "【演示数据】该品类增长迅速，健康生活方式带动消费升级。",
    "trends": ["健康化/功能化消费升级", "社交内容电商增长", "KOL 种草成为主要决策路径"],
    "audience_insights": ["25-35 岁都市白领是核心人群", "注重性价比与颜值", "愿意为情绪价值买单"],
    "pain_points": ["同类产品同质化严重", "消费者担心保温效果虚假宣传", "价格透明度不足"],
    "keywords": ["保温杯推荐", "长效保温", "通勤好物", "秋冬必备", "高颜值水杯"],
}

COMPETITOR = {
    "competitors": [
        {
            "name": "竞品A（演示）", "positioning": "高端商务", "selling_points": ["钛合金材质", "大容量"],
            "content_strategy": "强调材质与场景", "strengths": ["品牌溢价"], "weaknesses": ["价格高"],
        }
    ],
    "gaps": ["『24 小时长效保温 + 亲民价格』组合尚未被主打", "缺少针对通勤场景的内容内容"],
}

STRATEGY = {
    "target_audience": "25-35 岁都市白领，通勤与办公场景",
    "core_value_proposition": "一杯暖芯，24 小时暖到你心里",
    "key_messages": ["24 小时长效保温实测", "通勤/办公/居家全场景", "高颜值设计，送礼有面子"],
    "tone_guidance": "亲切温暖、有生活感、适度幽默",
    "content_angles": ["打工人冬日续命神器", "实测 24 小时保温挑战", "通勤好物清单"],
    "channel_strategy": "小红书侧重场景种草与测评，突出真实体验感",
}

COPYWRITING = {
    "variants": [
        {
            "title": "打工人冬日续命神器｜实测 24h 保温杯",
            "body": "冬天到了，谁不想随时喝上一口热乎的？☕\n这款暖芯保温杯真的绝了——早上装的热水，晚上下班还是烫嘴的！\n\n✅ 24 小时长效保温（实测）\n✅ 颜值在线，通勤/办公都能打\n✅ 密封防漏，放包里超安心\n\n姐妹们冲就完事了！",
            "hashtags": ["#保温杯推荐", "#打工人好物", "#秋冬必备"],
            "notes": "演示数据：侧重场景种草与实测感",
        }
    ]
}

COPYWRITING_REVISED = {
    "variants": [
        {
            "title": "打工人冬日续命神器｜实测 24h 保温杯（修订版）",
            "body": "冬天到了，谁不想随时喝上一口热乎的？☕\n这款暖芯保温杯真的绝了——早上装的热水，晚上下班还是烫嘴的！\n\n✅ 24 小时长效保温（实测）\n✅ 颜值在线，通勤/办公都能打\n✅ 密封防漏，放包里超安心\n\n限时立减 30 元，立即下单，这个冬天暖到心里！",
            "hashtags": ["#保温杯推荐", "#打工人好物", "#秋冬必备"],
            "notes": "演示数据：已按审校反馈补充 CTA",
        }
    ]
}

REVIEW_FAIL = {
    "passed": False,
    "feedback": "演示反馈：缺少明确的行动号召与价格信息，建议补充 CTA。",
    "checklist": [
        {"item": "卖点覆盖", "passed": True, "note": "保温 24h 已覆盖"},
        {"item": "行动号召", "passed": False, "note": "结尾缺少购买引导"},
        {"item": "渠道适配", "passed": True, "note": "符合小红书风格"},
    ],
}

REVIEW_PASS = {
    "passed": True,
    "feedback": "",
    "checklist": [
        {"item": "卖点覆盖", "passed": True, "note": "保温 24h 已覆盖"},
        {"item": "行动号召", "passed": True, "note": "已补充 CTA"},
        {"item": "渠道适配", "passed": True, "note": "符合小红书风格"},
    ],
}

_STAGE_KEYWORDS = [
    ("市场调研", RESEARCH),
    ("竞品分析", COMPETITOR),
    ("营销策略总监", STRATEGY),
]


def _field(text: str, key: str, default: str = "") -> str:
    match = re.search(rf"(?m)^\s*-\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else default


def _task_context(text: str) -> tuple[str, str, str, str]:
    requirements = _field(text, "extra_requirements", "").strip()
    if requirements.lower() in {"无", "未指定", "无额外要求", "none", "null"}:
        requirements = ""
    return (
        _field(text, "topic", "该产品"),
        _field(text, "brand_name", "示例品牌"),
        _field(text, "target_audience", "目标用户"),
        requirements,
    )


def _dynamic_research(topic: str, audience: str) -> dict:
    return {
        "summary": f"围绕“{topic}”的用户关注点集中在使用场景、实际体验和购买风险，需结合真实资料进一步验证。",
        "trends": [f"{topic}相关内容消费持续增长", "测评与真实体验影响购买决策", "用户更关注具体场景而非空泛宣传"],
        "audience_insights": [f"{audience}是本任务的核心人群", f"关注{topic}的实际使用效果", "倾向通过口碑和对比降低决策风险"],
        "pain_points": [f"难以快速判断{topic}是否适合自己", "同类产品信息同质化", "担心宣传与实际体验存在差距"],
        "keywords": [f"{topic}推荐", f"{topic}测评", f"{topic}怎么选", f"{topic}使用体验", f"{topic}避坑"],
    }


def _dynamic_competitor(topic: str) -> dict:
    return {
        "competitors": [{
            "name": f"同类{topic}品牌（演示）", "positioning": f"以{topic}核心功能为卖点",
            "selling_points": [f"突出{topic}基础功能", "通过场景展示降低理解成本"],
            "content_strategy": f"围绕{topic}的真实使用场景做测评和对比",
            "strengths": ["品类认知较成熟"], "weaknesses": ["同质化表达较多"],
        }],
        "gaps": [f"缺少针对具体场景的{topic}体验内容", "产品差异和证据表达不够清晰"],
    }


def _dynamic_strategy(topic: str, brand: str, audience: str, channel: str, requirements: str) -> dict:
    return {
        "target_audience": audience,
        "core_value_proposition": f"{brand}帮助{audience}更轻松地解决{topic}相关需求",
        "key_messages": [f"围绕{topic}展示具体使用场景", "用可验证的体验说明产品价值", f"针对{audience}降低选择成本"],
        "tone_guidance": "清晰、可信、有场景感，避免没有依据的参数和夸张承诺",
        "content_angles": [f"{topic}真实体验记录", f"{topic}选购对比清单", f"{topic}使用场景解决方案"],
        "channel_strategy": f"针对 {channel or '目标渠道'} 先展示场景，再说明价值，最后给出下一步行动",
        "requirements_applied": requirements or "无额外要求",
    }


def _dynamic_copy(topic: str, brand: str, audience: str, channel: str,
                  requirements: str, revised: bool) -> dict:
    if channel == "product_page":
        title = f"{brand} {topic}，为{audience}设计的产品介绍" + ("（修订版）" if revised else "")
        requirement_items = [
            item.strip() for item in re.split(r"[，,、；;\n]+", requirements)
            if item.strip() and item.strip().lower() not in {"无", "未指定", "无额外要求", "none", "null"}
        ]
        selling_points = [
            {
                "label": item,
                "feature": item,
                "advantage": f"将“{item}”作为明确的商品信息呈现",
                "benefit": f"帮助{audience}快速判断是否符合实际需求",
                "proof": "来自任务附加要求",
            }
            for item in requirement_items[:5]
        ]
        while len(selling_points) < 3:
            defaults = [
                {
                    "label": "使用场景匹配", "feature": "使用场景匹配",
                    "advantage": f"围绕{audience}的实际场景说明适用边界",
                    "benefit": "帮助用户先判断产品是否适合自己",
                    "proof": "由任务目标受众推导，非产品参数",
                },
                {
                    "label": "核心功能与参数", "feature": "核心功能与参数待补充",
                    "advantage": "资料补齐后再形成可核验的差异化说明",
                    "benefit": "避免基于不完整信息做购买决定",
                    "proof": "待补充产品资料",
                },
                {
                    "label": "规格与购买信息", "feature": "规格与购买信息待补充",
                    "advantage": "集中展示可核验的商品信息",
                    "benefit": "降低购买前的确认成本",
                    "proof": "待补充产品资料",
                },
            ]
            selling_points.append(defaults[len(selling_points)])
        specifications = ([{"name": "已提供商品信息", "value": item} for item in requirement_items]
                          or [{"name": "具体规格", "value": "待补充"}])
        cta = ("查看商品详情，核对完整规格后再决定是否购买。" if revised
               else "查看商品详情，确认它是否适合你的需求。")
        points_text = "\n".join(
            f"- {item['feature']}｜优势：{item['advantage']}｜用户利益：{item['benefit']}"
            for item in selling_points
        )
        body = (
            f"产品介绍\n{brand} {topic}，围绕{audience}的实际使用场景，清晰说明产品价值。\n\n"
            f"核心卖点\n{points_text}\n\n"
            "适用场景\n适合正在了解、对比或准备购买相关产品的用户。\n\n"
            "规格与信任说明\n当前任务未提供具体尺寸、材质、认证或售后承诺，以上信息需以实际商品资料为准。\n\n"
            f"{cta}"
        )
        return {"variants": [{
            "title": title, "body": body, "selling_points": selling_points,
            "specifications": specifications,
            "trust_badges": ["规格、认证和售后信息以实际商品资料为准"],
            "cta": cta, "hashtags": [],
            "notes": "按电商产品页结构生成，未臆造未提供的规格和资质",
        }]}
    if channel == "weibo":
        short_topic, short_brand, short_audience = topic[:60], brand[:30], audience[:70]
        topic_lower = short_topic.lower()
        if "键盘" in short_topic:
            focus = "轴体噪音、键帽回弹和办公室距离"
            hook_type = "场景痛点"
        elif any(word in topic_lower for word in ("咖啡", "耳机", "枕头", "防晒")):
            focus = "使用场景、核心体验和参数边界"
            hook_type = "用户提问"
        elif requirements and any(word in requirements for word in ("促销", "折扣", "半价", "限时")):
            focus = "活动规则、真实优惠和适用人群"
            hook_type = "数字对比"
        else:
            focus = "使用场景、核心指标和购买边界"
            hook_type = "结论前置"
        cta = "点击查看详情" if revised else "评论区说说你的使用场景"
        body = (
            f"{short_topic}怎么选？先说结论：{short_audience}别只看“静音/高性能”四个字，"
            f"先核对{focus}。{short_brand}这次把选择依据和待确认信息分开讲，"
            f"适合再对照自己的场景做决定。{cta}。"
        )[:280]
        return {"variants": [{
            "title": f"{topic}｜{brand}",
            "body": body,
            "hashtags": [f"#{topic}", f"#{topic}推荐", "#真实体验"],
            "hook_type": hook_type,
            "reasoning_chain": "场景现象-选择机制-核对动作-评论或详情页验证",
            "proof_used": ["用户提供的主题、受众和额外要求"],
            "risk_flags": ["未提供的参数、销量和效果不作承诺"],
            "notes": "按微博短动态结构生成，使用主题相关判断维度并控制在 280 字内",
        }]}
    if channel == "xiaohongshu":
        short_topic, short_brand, short_audience = topic[:50], brand[:30], audience[:50]
        requirement_note = f"\n这次特别关注：{requirements[:80]}。" if requirements else ""
        interaction = ("欢迎在评论区聊聊，也可以收藏这份判断思路。" if revised
                       else f"你选{short_topic}时最关注什么？先把自己的判断标准列出来。")
        body = (
            f"✨ 最近在研究{short_topic}，发现真正值得先看的不是夸张口号，而是它能不能融入你的日常。\n\n"
            f"如果你和{short_audience}一样正在做功课，可以先从使用场景、适用人群和信息透明度三个方面判断。"
            f"{short_brand}这次围绕真实需求来讲产品，不用没有依据的参数制造焦虑。{requirement_note}\n\n"
            "我更在意的是：关键信息是否说清楚、哪些参数还需要确认、买回去之后会在哪些场景真正用到。"
            "把这些问题逐项核对，比只看一句“闭眼入”更靠谱。🌿\n\n"
            f"{interaction}"
        )[:600]
        return {"variants": [{
            "title": f"别急着下单｜{short_topic}先看这几点" + ("（修订版）" if revised else ""),
            "body": body,
            "hashtags": [f"#{short_topic}", f"#{short_topic}推荐", "#选购攻略", "#真实体验", "#好物分享", "#避坑指南"],
            "notes": "按小红书种草笔记结构生成，保留生活化语气、Emoji 与长尾标签",
        }]}
    if channel == "wechat":
        requirement_note = f"本文额外关注：{requirements}。" if requirements else ""
        body = (
            f"导语\n面对{topic}，真正困难的往往不是找到商品，而是判断它是否适合{audience}。"
            f"{requirement_note}\n\n"
            f"一、先明确使用场景\n选择{topic}前，应先梳理使用频率、核心需求和不能接受的短板，"
            "避免被脱离场景的宣传信息带偏。\n\n"
            f"二、再核对产品信息\n{brand}相关内容应把已知卖点、规格依据和待确认信息分开呈现。"
            "对未提供的材质、尺寸、认证、价格或售后条件，不作推测性承诺。\n\n"
            "三、形成自己的判断清单\n把核心功能、适用边界、规格参数和购买条件逐项核对，"
            "才能降低信息不完整带来的决策风险。\n\n"
            f"结语\n如果你正在比较{topic}，建议先保存这份检查框架，再结合商品页的真实资料做决定。"
        )
        return {"variants": [{
            "title": f"选择{topic}之前，先把这三个问题想清楚" + ("（修订版）" if revised else ""),
            "body": body[:3000], "hashtags": [],
            "notes": "按公众号长文结构生成，使用分段论述且不添加话题标签",
        }]}
    cta = "现在就点击了解详情，选择适合你的方案。" if revised else "先从真实场景出发，判断它是否适合你的需求。"
    return {"variants": [{
        "title": f"{topic}怎么选？{brand}给{audience}的实用建议" + ("（修订版）" if revised else ""),
        "body": f"如果你正在关注{topic}，最重要的不是被夸张口号打动，而是确认它能否解决自己的真实需求。\n\n{brand}围绕{audience}的使用场景，先把体验、适用人群和注意事项讲清楚，再帮助你做选择。{('本次重点：' + requirements + '。') if requirements else ''}\n\n{cta}",
        "hashtags": [f"#{topic}", f"#{topic}推荐", "#真实体验"],
        "notes": "基于本次任务主题和受众生成，未添加未提供的产品参数",
    }]}


def _dynamic_review(text: str, topic: str, channel: str) -> dict:
    channel_name = {
        "xiaohongshu": "小红书", "wechat": "微信公众号",
        "weibo": "微博", "product_page": "电商产品页",
    }.get(channel, channel or "目标渠道")
    has_cta = any(word in text for word in (
        "立即下单", "立即购买", "立即了解", "点击了解", "查看详情", "查看商品详情",
        "欢迎咨询", "评论区", "阅读全文", "保存这份",
    ))
    topic_matched = topic == "该产品" or topic in text
    passed = has_cta and topic_matched
    feedback_parts = []
    if not topic_matched:
        feedback_parts.append(f"文案需明确围绕“{topic}”展开")
    if not has_cta:
        feedback_parts.append("结尾需补充清晰且不夸大承诺的行动号召")
    return {
        "passed": passed,
        "feedback": "；".join(feedback_parts),
        "checklist": [
            {"item": "主题一致性", "passed": topic_matched, "note": f"核对主题：{topic}"},
            {"item": "渠道适配", "passed": True, "note": f"按{channel_name}规则审校"},
            {"item": "行动号召", "passed": has_cta, "note": "检查是否提供明确下一步"},
        ],
    }


class FakeLLMClient:
    """与 LLMClient 同接口的演示客户端（无状态，多任务并发安全）。"""

    def __init__(self, model: str = "fake-demo") -> None:
        self.model = model

    def chat(
        self,
        messages: Iterable[dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict[str, str]] = None,
        json_mode: bool = False,
    ) -> LLMResult:
        text = " ".join(m["content"] for m in messages if m.get("content"))
        # 优先读取 system 角色，避免策略 prompt 中的“竞品分析”上下文抢先匹配。
        system_text = " ".join(m["content"] for m in messages if m.get("role") == "system")
        roles = ("市场调研分析师", "竞品分析专家", "营销策略总监", "文案创作专家", "内容审校专家")
        routing_text = system_text if any(role in system_text for role in roles) else text
        topic, brand, audience, requirements = _task_context(text)
        if "文案创作专家" in routing_text:
            payload = _dynamic_copy(topic, brand, audience, _field(text, "channel_id", ""), requirements,
                                    revised="上一轮审校未通过" in text)
        elif "内容审校专家" in routing_text:
            payload = _dynamic_review(text, topic, _field(text, "channel_id", ""))
        elif "市场调研分析师" in routing_text:
            payload = _dynamic_research(topic, audience) if topic != "该产品" else RESEARCH
        elif "竞品分析专家" in routing_text:
            payload = _dynamic_competitor(topic) if topic != "该产品" else COMPETITOR
        elif "营销策略总监" in routing_text:
            payload = _dynamic_strategy(topic, brand, audience,
                                        _field(text, "channel_id", ""), requirements) if topic != "该产品" else STRATEGY
        else:
            payload = {"error": "无法识别的阶段"}
        return LLMResult(
            content=json.dumps(payload, ensure_ascii=False),
            model=self.model,
            prompt_tokens=100,
            completion_tokens=50,
            latency_seconds=0.1,
        )

    @property
    def display_name(self) -> str:
        return self.model
