"""渠道规范符合度清单（切入点 C）测试。

改造前：``review`` 审校是**自由判断**，渠道 ``copywriting_guide`` 只作为文本依据塞进
提示词，没有「逐项核对」的机制 —— 结果「审校有没有真的逐条看渠道规范」完全靠自觉。

改造后：每个渠道在 ``channels.yaml`` 声明 ``review_checklist``（判断力项清单），
``build_review_messages`` 把它渲染成「必须逐项核对、每项都写进 checklist 输出」的
硬要求，演示模式（FakeLLM）从同一份配置现取，同样逐项产出。

本文件守三件事：

1. **清单真渲染**（三重真源不漂移）：``channels.yaml`` 里写的每一项都出现在
   review 提示词里；FakeLLM 的清单也从配置派生，改配置跟着变；
2. **与确定性层互补不重复**：``review_checklist`` 绝不能重新核验守门层 / 契约层
   已经确定性判定的东西（字数、标签数量）——那会两套口径打架；
3. **可选字段**：未声明 ``review_checklist`` 的渠道退回通用审校，不渲染清单段、不报错。
"""
from __future__ import annotations

from app.agents.prompts import build_review_messages
from app.fake_llm import _demo_review_checklist, _dynamic_review
from app.templates import get_channel

ALL_CHANNELS = ("xiaohongshu", "wechat", "weibo", "product_page")

# 守门层 / 契约层已经确定性核验的数值口径 —— 审校清单绝不能重复核验它们。
# 例：小红书标题 18-20 字由 title_min/max_chars 管；「2-3 大标签 + 3-5 长尾」
# 由 content_contract 的 tiered 管。审校若再问一遍「标题是不是 18-20 字」，
# 就是与确定性口径打架。
DETERMINISTIC_MARKERS = (
    "18-20", "18 - 20", "18到20", "18 到 20",       # 小红书标题字数
    "300-600", "300 - 600",                          # 小红书正文字数
    "280 字", "280字",                               # 微博字数
    "64 字", "64字",                                 # 公众号标题字数
    "2-3", "2 - 3", "2到3", "2 到 3",                # 热门大标签数量
    "3-5", "3 - 5", "3到5", "3 到 5",                # 长尾标签数量
)


def _state(channel_id: str) -> dict:
    return {
        "input": {
            "topic": "智能保温杯新品上市",
            "brand_name": "屿暖",
            "target_audience": "25-35岁通勤上班族",
            "channel_id": channel_id,
            "extra_requirements": "",
        }
    }


def _user(messages: list[dict[str, str]]) -> str:
    return next(m["content"] for m in messages if m["role"] == "user")


# ==========================================================================
# 1. 四渠道都声明了审校清单，且逐项渲染进 review 提示词
# ==========================================================================
def test_all_channels_declare_review_checklist():
    for ch in ALL_CHANNELS:
        items = get_channel(ch).get("review_checklist")
        assert items, f"{ch} 应声明 review_checklist"
        assert len(items) >= 2, f"{ch} 的审校清单至少 2 项，实际 {len(items)}"


def test_review_prompt_renders_every_checklist_item():
    """三重真源不漂移：yaml 里写的每一项都出现在 review 提示词里。"""
    for ch in ALL_CHANNELS:
        channel = get_channel(ch)
        messages = build_review_messages(_state(ch), channel, [{"title": "t", "body": "b"}])
        user = _user(messages)
        assert "渠道规范符合度清单" in user, f"{ch} 应渲染清单标题"
        for item in channel["review_checklist"]:
            assert item in user, f"{ch} 的清单项未进入 review 提示词：{item}"


# ==========================================================================
# 2. 与确定性层互补不重复
# ==========================================================================
def test_review_checklist_does_not_recheck_deterministic_rules():
    """审校清单不能重新核验守门层 / 契约层已确定性判定的口径（字数、标签数量）。"""
    for ch in ALL_CHANNELS:
        channel = get_channel(ch)
        for item in channel["review_checklist"]:
            for marker in DETERMINISTIC_MARKERS:
                assert marker not in item, (
                    f"{ch} 的审校清单项重复了确定性已核验的口径：{item!r}"
                )


# ==========================================================================
# 3. 未声明 review_checklist 的渠道退回通用审校（可选字段）
# ==========================================================================
def test_review_without_checklist_still_works():
    channel = {"name": "测试渠道", "copywriting_guide": {"style": "通用"}}
    messages = build_review_messages(_state("unknown_channel"), channel, [{"title": "t", "body": "b"}])
    user = _user(messages)
    # 未声明 → 不渲染「必须逐项核对」的清单段，但仍能正常构建审校提示词
    assert "渠道规范符合度清单" not in user
    assert "渠道文案方法论" in user  # 通用 guide 段仍在


# ==========================================================================
# 4. 演示模式从配置现取，改配置跟着变
# ==========================================================================
def test_fake_review_checklist_follows_config(monkeypatch):
    """演示清单从 channels.yaml 派生，不是另写一份。"""
    xhs = _demo_review_checklist("xiaohongshu")
    assert xhs == get_channel("xiaohongshu")["review_checklist"]

    # 改配置 → 演示清单跟着变（证明是派生）
    monkeypatch.setitem(get_channel("xiaohongshu"), "review_checklist", ["只此一项"])
    assert _demo_review_checklist("xiaohongshu") == ["只此一项"]


def test_fake_review_includes_channel_checklist_items():
    """_dynamic_review 产出的 checklist 覆盖渠道的 review_checklist 每一项。"""
    for ch in ALL_CHANNELS:
        review = _dynamic_review("正文 立即下单 保温杯", "保温杯", ch)
        items = [c["item"] for c in review["checklist"]]
        for want in get_channel(ch)["review_checklist"]:
            assert want in items, f"{ch} 的演示 checklist 漏了清单项：{want}"
