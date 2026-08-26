"""演示模式：无 API Key 时使用的假 LLM 客户端（FakeLLM）。

根据用户消息中的角色关键字识别流水线阶段，返回预置的结构化响应，
使完整流水线（含审校循环、人工确认、成本统计）无需真实 API 即可跑通。
适合：无 Key 体验、CI 测试、演示。

审校 Agent 的演示行为：第一次返回"不通过"（展示打回创作循环），
之后返回"通过"。
"""
from __future__ import annotations

import json
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
    ("文案创作专家", COPYWRITING),
    ("内容审校专家", None),  # 单独处理（审校轮次）
]


class FakeLLMClient:
    """与 LLMClient 同接口的演示客户端。"""

    def __init__(self, model: str = "fake-demo") -> None:
        self.model = model
        self._review_calls = 0

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
        payload: dict = {"error": "无法识别的阶段"}
        for keyword, response in _STAGE_KEYWORDS:
            if keyword in text:
                payload = response if response is not None else self._review_response()
                break
        return LLMResult(
            content=json.dumps(payload, ensure_ascii=False),
            model=self.model,
            prompt_tokens=100,
            completion_tokens=50,
            latency_seconds=0.1,
        )

    def _review_response(self) -> dict:
        """演示审校：第一次不通过（触发打回），后续通过。"""
        self._review_calls += 1
        return REVIEW_FAIL if self._review_calls == 1 else REVIEW_PASS

    @property
    def display_name(self) -> str:
        return self.model
