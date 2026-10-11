"""演示模式：无 API Key 时使用的假 LLM 客户端（FakeLLM）。

根据用户消息中的角色关键字识别流水线阶段，返回预置的结构化响应，
使完整流水线（含审校循环、人工确认、成本统计）无需真实 API 即可跑通。
适合：无 Key 体验、CI 测试、演示。

无状态设计（多任务并发安全）：
- 文案创作：被审校打回后（prompt 含"上一轮审校未通过"）输出含 CTA 的修订版，
  否则输出初始版（不含 CTA）。
- 内容审校：待审文案含"立即下单"（修订版特征）判通过，否则不通过。
- 去 AI 味：按 ``ai_tells_zh.yaml`` 里**已注册**的 pattern 做定点消除，
  正则是从规则资产现取而不是复制一份，保证演示改写与确定性扫描器
  永远盯同一批特征。
由此自然形成"第一次审校不通过 → 打回重写 → 通过"的演示循环，
不依赖任何共享计数器。
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from .guardrails.ai_tells import load_ai_tell_rules
from .llm import LLMResult
from .templates import get_channel

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
    "content_structure": [
        "开头钩子（打工人冬日场景）",
        "场景痛点共鸣（水凉了、不方便）",
        "产品卖点展开（保温/防漏/颜值）",
        "亲测体验（实测时长与场景）",
        "行动号召（收藏/评论）",
    ],
    "channel_strategy": "小红书侧重场景种草与测评，突出真实体验感",
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


# 各渠道的检索词口径（演示数据）。渠道上下文前移到 research 后，演示也必须
# 体现出「同一主题在不同渠道下关键词不同」，否则演示看不出这一步的作用。
_DEMO_KEYWORDS: dict[str, list[str]] = {
    "xiaohongshu": ["{t}推荐", "{t}测评", "{t}怎么选", "{t}真实体验", "{t}避坑"],
    "wechat": ["{t}行业观察", "{t}选购指南", "{t}深度解析", "{t}行业趋势", "{t}方法论"],
    "weibo": ["{t}热搜", "{t}话题讨论", "{t}怎么看", "{t}争议点", "{t}热点"],
    "product_page": ["{t}价格", "{t}规格参数", "{t}型号对比", "{t}材质", "{t}售后"],
}


# 主题 → 内容品类的粗粒度归类（演示用）。
#
# 目的：让演示文案的**口吻**随主题真实地变化，而不是「换主题词、句式一模一样」——
# 那样演示看不出「竞品分析在指导文案差异化」这一步的作用，用户也会觉得
# 「无论做什么产品，文案都是一个味」。归类是粗粒度的启发式，不追求穷举，
# 命中即切换口吻，未命中回落默认。
_DIGITAL_HINTS = ("键盘", "鼠标", "耳机", "显示器", "电脑", "笔记本", "手机", "相机",
                  "音箱", "平板", "充电", "机械", "电竞", "游戏")
_LIFESTYLE_HINTS = ("保温", "咖啡", "护肤", "防晒", "洗护", "香薰", "家居", "床品",
                    "水杯", "杯", "零食", "茶", "巧克力", "香氛", "护手", "唇膏")


def _topic_category(topic: str) -> str:
    """把主题归类到粗粒度品类：digital / lifestyle / default。"""
    t = topic or ""
    if any(h in t for h in _DIGITAL_HINTS):
        return "digital"
    if any(h in t for h in _LIFESTYLE_HINTS):
        return "lifestyle"
    return "default"


def _demo_keywords(topic: str, channel: str) -> list[str]:
    patterns = _DEMO_KEYWORDS.get(channel) or ["{t}推荐", "{t}测评", "{t}怎么选", "{t}使用体验", "{t}避坑"]
    return [p.format(t=topic) for p in patterns]


def _dynamic_research(topic: str, audience: str, channel: str = "") -> dict:
    return {
        "summary": f"围绕“{topic}”的用户关注点集中在使用场景、实际体验和购买风险，需结合真实资料进一步验证。",
        "trends": [f"{topic}相关内容消费持续增长", "测评与真实体验影响购买决策", "用户更关注具体场景而非空泛宣传"],
        "audience_insights": [f"{audience}是本任务的核心人群", f"关注{topic}的实际使用效果", "倾向通过口碑和对比降低决策风险"],
        "pain_points": [f"难以快速判断{topic}是否适合自己", "同类产品信息同质化", "担心宣传与实际体验存在差距"],
        "keywords": _demo_keywords(topic, channel),
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


def _demo_structure(topic: str, channel: str) -> list[str]:
    """演示模式的内容骨架：**直接取自 channels.yaml 的 structure**。

    从真源现取而不是在演示里另写一份，否则「渠道结构改了、演示还停在旧结构」——
    与「演示改写正则从 ai_tells_zh.yaml 现取」是同一个理由。
    """
    try:
        structure = str(get_channel(channel).get("structure") or "")
    except KeyError:
        structure = ""
    steps = [s.strip() for s in re.split(r"\s*(?:→|->|>)\s*", structure) if s.strip()]
    return [f"{step}（围绕「{topic}」）" for step in steps]


def _demo_review_checklist(channel: str) -> list[str]:
    """演示模式的审校清单：**直接取自 channels.yaml 的 review_checklist**。

    与 _demo_structure / _demo_keywords 同理——从真源现取而不是另写一份，
    否则「渠道清单改了、演示还停在旧清单」。
    """
    try:
        return [str(x) for x in (get_channel(channel).get("review_checklist") or [])]
    except KeyError:
        return []


def _dynamic_strategy(topic: str, brand: str, audience: str, channel: str, requirements: str) -> dict:
    return {
        "target_audience": audience,
        "core_value_proposition": f"{brand}帮助{audience}更轻松地解决{topic}相关需求",
        "key_messages": [f"围绕{topic}展示具体使用场景", "用可验证的体验说明产品价值", f"针对{audience}降低选择成本"],
        "tone_guidance": "清晰、可信、有场景感，避免没有依据的参数和夸张承诺",
        "content_angles": [f"{topic}真实体验记录", f"{topic}选购对比清单", f"{topic}使用场景解决方案"],
        # 渠道原生骨架（下游 copywriting 必须按它展开正文）
        "content_structure": _demo_structure(topic, channel),
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
        category = _topic_category(short_topic)
        # 口吻随品类切换：数码 → 硬核测评/参数党；生活 → 情绪种草；默认 → 理性反焦虑。
        # 无论哪套，都遵守演示「不编造」原则——不臆造具体参数/价格/销量，
        # 只给判断框架与可核验的体验口径。
        if category == "digital":
            body = (
                f"🎧 关于{short_topic}，先泼一盆冷水：参数表再好看，不适合你的场景就是白搭。\n\n"
                f"如果你是{short_audience}，别被「旗舰」「专业」这类词带节奏，先想清楚三点：\n"
                "① 你真正高频用到的功能是什么，② 哪些指标对你的体验影响最大，"
                "③ 到手后哪些细节能当场验证。\n\n"
                f"{short_brand}这次把「已确认」和「待确认」分开讲，不拿没有实测依据的参数充门面。"
                f"{requirement_note}\n\n"
                "我更看重的是：上手之后手感到底如何、宣传口径和实际体验对不对得上、"
                "有没有为「用不上的功能」多花钱。把这些逐项核一遍，比跟风冲首发靠谱。\n\n"
                f"{interaction}"
            )[:600]
            marker = "（修订版）" if revised else ""
            head = f"别急着下单｜{short_topic}实测避坑指南"
            scenario = f"先搞清楚{short_topic}在你手头到底是刚需还是「看起来用得上」，像{short_audience}最容易被参数带偏。"
            experience_detail = f"围绕{short_topic}的真实上手：核心指标是否匹配你的使用强度、细节体验与宣传是否一致，逐项核验后再下结论。"
            selling_points = [
                f"场景匹配：{short_topic}是否覆盖你的真实使用强度",
                "参数可核验：关键指标是否与宣传口径一致",
                "避坑清单：哪些功能其实用不上、哪些钱不该花",
            ]
        elif category == "lifestyle":
            body = (
                f"🌿 认真说，{short_topic}这类东西，最打动我的从来不是参数表，而是它出现在生活里的那个瞬间。\n\n"
                f"对{short_audience}来说，它能不能让你每天多一点舒心、少一点将就，比「全网热销」这种口号实在得多。"
                f"{short_brand}这次想聊的也是这个——不堆卖点，还原它真实融入日常的样子。{requirement_note}\n\n"
                "我更在意的：用起来舒不舒服、时间久了会不会后悔、值不值这个价。"
                "把「心动」和「实用」分开想，才是理性种草。\n\n"
                f"{interaction}"
            )[:600]
            marker = "（修订版）" if revised else ""
            head = f"真的会回购｜{short_topic}的日常幸福感"
            scenario = f"把{short_topic}放进真实的一天：像{short_audience}这样的生活节奏里，它带来的是便利还是负担。"
            experience_detail = f"围绕{short_topic}的日常体验：用起来是否顺手、是否真的提升了生活质量、有哪些细节让人回不去。"
            selling_points = [
                f"生活感：{short_topic}是否真实改善了你的日常",
                "体验可感：使用时的愉悦感与便利度",
                "理性种草：心动和实用分开看，不为噱头买单",
            ]
        else:
            body = (
                f"✨ 最近在研究{short_topic}，发现真正值得先看的不是夸张口号，而是它能不能融入你的日常。\n\n"
                f"如果你和{short_audience}一样正在做功课，可以先从使用场景、适用人群和信息透明度三个方面判断。"
                f"{short_brand}这次围绕真实需求来讲产品，不用没有依据的参数制造焦虑。{requirement_note}\n\n"
                "我更在意的是：关键信息是否说清楚、哪些参数还需要确认、买回去之后会在哪些场景真正用到。"
                "把这些问题逐项核对，比只看一句“闭眼入”更靠谱。🌿\n\n"
                f"{interaction}"
            )[:600]
            marker = "（修订版）" if revised else ""
            head = f"别急着下单｜{short_topic}用了三十天后的真实感受"
            scenario = f"先想清楚{short_topic}在什么场景下真正用得上，像{short_audience}这样的人最有体会。"
            experience_detail = f"围绕{short_topic}的真实体验：上手感受与宣传是否一致、哪些细节还需进一步确认，逐项核对后再下结论。"
            selling_points = [
                f"场景匹配：{short_topic}是否覆盖你的真实使用场景",
                "体验可核验：实际感受与宣传口径是否一致",
                "决策透明：参数、价格与售后逐项核对后再决定",
            ]
        return {"variants": [{
            "title": head[: 20 - len(marker)] + marker,
            "body": body,
            # ---- 渠道内容契约（与 channels.yaml 的 content_contract 对应）----
            # 契约字段同样**跟随主题生成**，且遵守演示「不编造」原则：
            # 假模型不知道「磁轴键盘」的真实卖点，所以只给判断框架，
            # 不臆造具体产品参数——否则会复现「标题是键盘、卖点却是长效保温」的错乱。
            "hook_type": "场景痛点",
            "scenario": scenario,
            "experience_detail": experience_detail,
            "selling_points": selling_points,
            "tags_by_tier": {
                "hot": [f"{short_topic}推荐", "通勤好物"],
                "longtail": [f"{short_topic}选购攻略", "真实体验", "好物分享", "避坑指南"],
            },
            "cta": "先收藏这份判断思路，需要的时候再翻出来对照。",
            "notes": "按小红书种草笔记结构生成：场景钩子 + 使用细节 + 分层标签",
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
            "body": body[:3000],
            "hashtags": [],
            # ---- 渠道内容契约（与 channels.yaml 的 content_contract 对应）----
            # section_headings 必须**原样出现在 body 里**，否则守门层会报
            # 「小标题未出现在正文中」——这正是该契约存在的意义。
            "outline": [
                "先明确使用场景：梳理使用频率与不能接受的短板",
                "再核对产品信息：区分已知卖点、规格依据与待确认项",
                "形成自己的判断清单：逐项核对后再决定",
            ],
            "section_headings": ["一、先明确使用场景", "二、再核对产品信息", "三、形成自己的判断清单"],
            "key_takeaway": f"选{topic}先看场景与依据，再谈价格和参数。",
            # 演示同样遵守「不许编造」：没有外部依据时如实写明（契约只要求非空）。
            "evidence_refs": ["暂无外部依据：本文只依据任务输入与品牌档案，未引入外部数据"],
            "cta": "如果这份框架对你有用，可以转发给正在挑同类的朋友。",
            "notes": "按公众号长文结构生成，使用分段论述且不添加话题标签",
        }]}
    cta = "现在就点击了解详情，选择适合你的方案。" if revised else "先从真实场景出发，判断它是否适合你的需求。"
    return {"variants": [{
        "title": f"{topic}怎么选？{brand}给{audience}的实用建议" + ("（修订版）" if revised else ""),
        "body": f"如果你正在关注{topic}，最重要的不是被夸张口号打动，而是确认它能否解决自己的真实需求。\n\n{brand}围绕{audience}的使用场景，先把体验、适用人群和注意事项讲清楚，再帮助你做选择。{('本次重点：' + requirements + '。') if requirements else ''}\n\n{cta}",
        "hashtags": [f"#{topic}", f"#{topic}推荐", "#真实体验"],
        "notes": "基于本次任务主题和受众生成，未添加未提供的产品参数",
    }]}


# 去 AI 味的演示处置表：pattern_id -> 替换文本。
# **正则不在这里重复**，运行时从 ai_tells_zh.yaml 取（单一真源）。
# 只处理 fatal 级否定句式——改写只需让 AI 味命中总数下降即可通过验收。
_DEMO_HUMANIZE_REPLACEMENTS = {
    "negative_parallel": "是",
    "not_x_but_y": "更准确地说",
}

# humanize 提示词用显式边界把「只改这一部分」的正文包起来
_BODY_BLOCK_RE = re.compile(r"<<<正文开始\s*\n(.*?)\n\s*正文结束>>>", re.DOTALL)


def _extract_body(text: str) -> str:
    """从去 AI 味的用户消息里取出「只改这一部分」的正文。"""
    match = _BODY_BLOCK_RE.search(text)
    return match.group(1) if match else ""


def _dynamic_humanize(text: str) -> dict:
    """演示用去 AI 味改写。

    正则在 ``load_ai_tell_rules()`` 里取，若规则资产改了写法，这里自动跟上——
    避免演示 fixture 与守门层出现「两套特征表」。
    """
    body = _extract_body(text)
    rules = load_ai_tell_rules()
    by_id = {pattern.id: pattern for pattern in rules.patterns}

    out = body
    changes: list[str] = []
    for pattern_id, replacement in _DEMO_HUMANIZE_REPLACEMENTS.items():
        rule = by_id.get(pattern_id)
        if rule is None or rule.kind != "regex" or rule.pattern is None:
            continue
        rewritten = rule.pattern.sub(replacement, out)
        if rewritten != out:
            changes.append(f"消除 AI 味特征「{rule.suggestion}」")
            out = rewritten
    return {"body": out, "changes": changes}


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
            # 渠道规范符合度清单：从 channels.yaml 现取，演示同样逐项核对
            *[{"item": item, "passed": True, "note": f"按{channel_name}规范核对"}
              for item in _demo_review_checklist(channel)],
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
        roles = ("市场调研分析师", "竞品分析专家", "营销策略总监", "文案创作专家", "内容审校专家",
                 "中文文案润色编辑")
        routing_text = system_text if any(role in system_text for role in roles) else text
        topic, brand, audience, requirements = _task_context(text)
        if "文案创作专家" in routing_text:
            payload = _dynamic_copy(topic, brand, audience, _field(text, "channel_id", ""), requirements,
                                    revised="上一轮审校未通过" in text)
        elif "中文文案润色编辑" in routing_text:
            payload = _dynamic_humanize(text)
        elif "内容审校专家" in routing_text:
            payload = _dynamic_review(text, topic, _field(text, "channel_id", ""))
        elif "市场调研分析师" in routing_text:
            payload = (_dynamic_research(topic, audience, _field(text, "channel_id", ""))
                       if topic != "该产品" else RESEARCH)
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
