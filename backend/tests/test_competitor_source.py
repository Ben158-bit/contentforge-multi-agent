"""竞品数据源 + 竞品分析真实数据注入的测试。

覆盖：
- 互动数字量归一（'1.5w' → 15000 等）
- 三渠道字段转换（小红书 / 公众号 / 微博 → 统一证据结构）
- 优雅降级（无 key / 未知渠道 / 网络失败）
- 提示词注入（build_competitor_messages 带 evidence）
- 节点装配（make_competitor_node 注入 / 无源 / 源失败降级）
"""
from __future__ import annotations

import json

from app.agents.competitor_source import (
    CompetitorDataSource,
    _extract_core_keyword,
    _num,
    fetch_competitor_evidence,
)
from app.agents.nodes import make_competitor_node
from app.agents.prompts import build_competitor_messages
from app.llm import LLMResult


# --------------------------------------------------------------------------
# 数字归一
# --------------------------------------------------------------------------
def test_num_parses_wan_and_str():
    assert _num(15822) == 15822
    assert _num("1.5w") == 15000
    assert _num("9335") == 9335
    assert _num("5000+") == 5000
    assert _num(None) == 0
    assert _num("") == 0
    assert _num("abc") == 0


# --------------------------------------------------------------------------
# 三渠道字段转换（monkeypatch 掉网络）
# --------------------------------------------------------------------------
def test_fetch_xiaohongshu_normalizes_fields(monkeypatch):
    def fake_post(path, payload):
        return {"code": 2000, "data": {"articles": [{
            "id": "abc", "title": "磁轴键盘开箱", "desc": "正文 #磁轴键盘",
            "authorNickname": "测评君", "authorFans": "1.2w",
            "likedCount": 1000, "collectedCount": 200, "commentsCount": 30,
            "sharedCount": 10, "interactiveCount": 1240,
            "shareInfoLink": "https://x/abc", "createTime": "2026-01-01 00:00:00",
        }]}}

    monkeypatch.setenv("REDFOX_API_KEY", "ak_test")
    monkeypatch.setattr("app.agents.competitor_source._post_json", fake_post)
    ev = fetch_competitor_evidence("xiaohongshu", "磁轴键盘")
    assert len(ev) == 1
    e = ev[0]
    assert e["channel"] == "xiaohongshu" and e["kind"] == "note"
    assert e["title"] == "磁轴键盘开箱"
    assert e["metrics"]["likes"] == 1000
    assert e["metrics"]["fans"] == 12000
    assert e["link"] == "https://x/abc"
    assert e["published_at"] == "2026-01-01 00:00:00"


def test_fetch_wechat_normalizes_fields(monkeypatch):
    def fake_post(path, payload):
        return {"code": 2000, "data": {"list": [{
            "title": "1599元磁轴键盘", "author": "数码测评", "summary": "开箱体验",
            "readCount": 4907, "likeCount": 2, "collectCount": 3,
            "commentCount": None, "shareCount": 47,
            "workUrl": "https://mp.weixin.qq.com/s/xxx", "publishTime": "2026-09-21 00:12:17",
        }]}}

    monkeypatch.setenv("REDFOX_API_KEY", "ak_test")
    monkeypatch.setattr("app.agents.competitor_source._post_json", fake_post)
    ev = fetch_competitor_evidence("wechat", "磁轴键盘")
    assert len(ev) == 1
    e = ev[0]
    assert e["channel"] == "wechat" and e["kind"] == "article"
    assert e["metrics"]["reads"] == 4907
    assert e["metrics"]["comments"] == 0  # None → 0
    assert e["link"] == "https://mp.weixin.qq.com/s/xxx"


def test_fetch_weibo_normalizes_account(monkeypatch):
    def fake_post(path, payload):
        return {"code": 2000, "data": [
            {"name": "BACKSPACE退格", "customVerify": "BACKSPACE退格 官方微博",
             "fansCount": 463, "verified": True, "uid": 123},
        ]}

    monkeypatch.setenv("REDFOX_API_KEY", "ak_test")
    monkeypatch.setattr("app.agents.competitor_source._post_json", fake_post)
    ev = fetch_competitor_evidence("weibo", "磁轴键盘")
    assert len(ev) == 1
    e = ev[0]
    assert e["kind"] == "account"
    assert e["metrics"]["fans"] == 463
    assert e["verified"] is True
    assert e["summary"] == "BACKSPACE退格 官方微博"


# --------------------------------------------------------------------------
# 优雅降级
# --------------------------------------------------------------------------
def test_fetch_no_key_degrades(monkeypatch):
    monkeypatch.delenv("REDFOX_API_KEY", raising=False)
    assert fetch_competitor_evidence("xiaohongshu", "磁轴键盘") == []


def test_fetch_blank_keyword_degrades(monkeypatch):
    monkeypatch.setenv("REDFOX_API_KEY", "ak_test")
    assert fetch_competitor_evidence("xiaohongshu", "  ") == []


def test_fetch_unknown_channel_degrades(monkeypatch):
    monkeypatch.setenv("REDFOX_API_KEY", "ak_test")
    assert fetch_competitor_evidence("product_page", "磁轴键盘") == []


def test_fetch_network_failure_degrades(monkeypatch):
    monkeypatch.setenv("REDFOX_API_KEY", "ak_test")

    def boom(path, payload):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.agents.competitor_source._post_json", boom)
    assert fetch_competitor_evidence("xiaohongshu", "磁轴键盘") == []


def test_source_available_flips_on_key():
    assert CompetitorDataSource(api_key="ak_x").available is True
    assert CompetitorDataSource(api_key="").available is False


# --------------------------------------------------------------------------
# 核心词提炼 + 多级关键词回退
# --------------------------------------------------------------------------
def test_extract_core_keyword_strips_marketing_suffix():
    assert _extract_core_keyword("磁轴键盘新品上市推广") == "磁轴键盘"
    assert _extract_core_keyword("智能保温杯新品上市推广") == "智能保温杯"
    assert _extract_core_keyword("XX品牌磁轴键盘测评") == "XX品牌磁轴键盘"
    assert _extract_core_keyword("护肤品推荐攻略") == "护肤品"


def test_extract_core_keyword_passthrough():
    # 无营销后缀 → 原样返回；过短/空 → 原样返回
    assert _extract_core_keyword("磁轴键盘") == "磁轴键盘"
    assert _extract_core_keyword("") == ""
    # 后缀打头截断后过短 → 回退原词
    assert _extract_core_keyword("新品磁轴键盘") == "新品磁轴键盘"


def test_fetch_falls_back_from_core_to_topic_to_brand(monkeypatch):
    """核心词 0 条 → 回退原主题 → 回退品牌；谁先命中用谁。"""
    calls: list[str] = []

    def fake_fetch(channel_id, keyword, limit=8):
        calls.append(keyword)
        # 只有「磁轴键盘」这个核心词能搜到（模拟真实：长句搜不到）
        return [{"title": "命中"}] if keyword == "磁轴键盘" else []

    monkeypatch.setattr(
        "app.agents.competitor_source.fetch_competitor_evidence", fake_fetch)
    src = CompetitorDataSource(api_key="ak_x")
    state = {"input": {"topic": "磁轴键盘新品上市推广",
                       "brand_name": "自研品牌", "channel_id": "xiaohongshu"}}
    ev = src.fetch(state)
    assert ev == [{"title": "命中"}]
    assert calls == ["磁轴键盘"]  # 核心词命中即返回，不再往后回退


def test_fetch_falls_back_to_topic_when_core_empty(monkeypatch):
    """核心词也 0 条 → 回退原主题。"""
    calls: list[str] = []

    def fake_fetch(channel_id, keyword, limit=8):
        calls.append(keyword)
        return [{"title": "命中"}] if keyword == "原主题词" else []

    monkeypatch.setattr(
        "app.agents.competitor_source.fetch_competitor_evidence", fake_fetch)
    src = CompetitorDataSource(api_key="ak_x")
    state = {"input": {"topic": "原主题词", "brand_name": "", "channel_id": "wechat"}}
    ev = src.fetch(state)
    assert ev == [{"title": "命中"}]
    assert calls == ["原主题词"]


# --------------------------------------------------------------------------
# 提示词注入
# --------------------------------------------------------------------------
def _evidence():
    return [{
        "channel": "xiaohongshu", "kind": "note", "title": "磁轴键盘开箱",
        "summary": "一分钟学会换轴", "author": "测评君",
        "metrics": {"likes": 9335, "fans": 30165},
        "link": "https://x/abc", "published_at": "2026-01-01",
    }]


def test_build_competitor_messages_injects_evidence():
    state = {"input": {"topic": "磁轴键盘", "brand_name": ""},
             "research": {"summary": "s", "trends": []}}
    msgs = build_competitor_messages(state, _evidence())
    user = msgs[-1]["content"]
    assert "磁轴键盘开箱" in user
    assert "https://x/abc" in user
    assert "测评君" in user
    assert "不要编造不存在的竞品" in user


def test_build_competitor_messages_no_evidence_is_unchanged():
    state = {"input": {"topic": "磁轴键盘"},
             "research": {"summary": "s", "trends": []}}
    msgs = build_competitor_messages(state, None)
    assert "真实内容平台抓取" not in msgs[-1]["content"]


# --------------------------------------------------------------------------
# 节点装配
# --------------------------------------------------------------------------
class CapturingLLM:
    def __init__(self, response: str):
        self.response = response
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return LLMResult(content=self.response, model="fake")


class FakeSource:
    def __init__(self, evidence):
        self.evidence = evidence

    def fetch(self, state):
        return self.evidence


def _state():
    return {"input": {"topic": "磁轴键盘", "brand_name": ""}, "research": {}}


def test_competitor_node_injects_and_attaches_evidence():
    llm = CapturingLLM(json.dumps(
        {"competitors": [{"name": "A"}], "gaps": []}, ensure_ascii=False))
    node = make_competitor_node(llm, competitor_source=FakeSource(_evidence()))
    out = node(_state())

    assert "磁轴键盘开箱" in llm.messages[-1]["content"]
    assert out["competitor"]["evidence"][0]["title"] == "磁轴键盘开箱"
    assert out["stage_status"]["competitor"] == "completed"


def test_competitor_node_without_source_no_evidence():
    llm = CapturingLLM(json.dumps({"competitors": [], "gaps": []}, ensure_ascii=False))
    node = make_competitor_node(llm, competitor_source=None)
    out = node(_state())
    assert "evidence" not in out["competitor"]


def test_competitor_node_source_failure_degrades():
    class Broken:
        def fetch(self, state):
            raise RuntimeError("boom")

    llm = CapturingLLM(json.dumps({"competitors": [], "gaps": []}, ensure_ascii=False))
    node = make_competitor_node(llm, competitor_source=Broken())
    out = node(_state())
    assert out["competitor"]["gaps"] == []
    assert "evidence" not in out["competitor"]
