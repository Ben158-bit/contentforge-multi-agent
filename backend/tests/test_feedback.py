"""效果闭环测试：特征提取、规律提炼、回填幂等、模拟相关性、注入排序、API。"""
from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-fb-test-")

import pytest  # noqa: E402

from app import repository as repo  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
async def _init_db():
    await init_db()
    yield


async def test_feedback_upsert_idempotent():
    tid = await repo.create_task("保温杯", "xiaohongshu", brand_name="暖芯")
    await repo.upsert_content_feedback(tid, views=1000, conversions=30, score=3.5)
    await repo.upsert_content_feedback(tid, views=2000, conversions=60, score=4.0)
    row = await repo.get_content_feedback(tid)
    assert row["views"] == 2000 and row["conversions"] == 60 and row["score"] == 4.0
    rows = await repo.list_content_feedback()
    assert len(rows) == 1  # 幂等：同 content_id 不新增


# ===================== 特征提取 =====================

from app.feedback import extract_features  # noqa: E402


def test_extract_features():
    content = {
        "title": "3 个保温杯选购技巧，为什么买贵不如买对？",
        "body": "第一点：看材质。\n第二点：看保温时长。\n第三点：看密封性。",
        "hashtags": ["#保温杯", "#好物"],
        "notes": "",
    }
    f = extract_features(content)
    assert f["has_num"] == 1          # 标题含数字
    assert f["has_question"] == 1     # 标题含问句
    assert f["title_len"] == len("3 个保温杯选购技巧，为什么买贵不如买对？")
    assert f["bullet_count"] == 3     # 正文要点数（按行/编号）
    assert f["has_emoji"] == 0


# ===================== 规律提炼 =====================

from app.feedback import _strength_from_stats, derive_feedback_rules  # noqa: E402
from app.llm import LLMResult  # noqa: E402


class _FakeLLM:
    """模拟 LLM：固定返回合法 JSON（对齐 test_api.py 的 chat 接口）。"""

    def chat(self, messages, **kwargs):
        return LLMResult(
            content='{"rules": ['
                    '{"feature": "has_num", "text": "标题含数字点击率高"}, '
                    '{"feature": "has_emoji", "text": "标题含 emoji 效果差"}]}',
            model="fake", prompt_tokens=1, completion_tokens=1, latency_seconds=0.0,
        )


def test_strength_from_stats():
    assert _strength_from_stats(0.80, 0.30) > 0   # 高分组更多 → 正向规则
    assert _strength_from_stats(0.20, 0.70) < 0   # 低分组更多 → 负向（避坑）规则
    assert _strength_from_stats(0.5, 0.5) == 0    # 无差异 → 0（不产生规则）


def test_derive_feedback_rules_uses_stats():
    stats = {"has_num": (0.8, 0.3), "has_emoji": (0.2, 0.6)}
    rules = derive_feedback_rules(stats, llm=_FakeLLM())
    assert len(rules) >= 1
    assert all(r["source"] == "feedback" for r in rules)
    assert all(isinstance(r["strength"], float) for r in rules)
    # has_num 高分占比高 → 正向 strength；has_emoji 低分占比高 → 负向 strength
    by_feature = {r["feature"]: r["strength"] for r in rules}
    assert by_feature["has_num"] > 0
    assert by_feature["has_emoji"] < 0


# ===================== 入库合并 + 注入排序 =====================

from app.feedback import learn_from_feedback  # noqa: E402
from app.memory import get_brand_context  # noqa: E402


async def test_learn_from_feedback_merge_and_inject():
    bid = await repo.create_brand("反馈暖芯")
    rules = [
        {"feature": "has_num", "text": "标题含数字点击率高",
         "strength": 1.5, "source": "feedback"},
    ]
    added = await learn_from_feedback(bid, rules)
    assert added == 1
    # 同文本再学一次 → 合并（strength 衰减加权），不新增行
    added = await learn_from_feedback(bid, rules)
    assert added == 0
    prefs = await repo.list_brand_preferences(bid)
    assert len(prefs) == 1
    assert abs(prefs[0]["strength"] - 2.25) < 1e-6  # 1.5 + 1.5*0.5（衰减累计）

    # 注入：feedback 高 strength 规则排最前
    await repo.add_brand_preference(bid, "普通人工规则", source="manual", strength=0.5)
    ctx = get_brand_context(bid, max_rules=5)
    assert ctx["preferences"][0] == "标题含数字点击率高"


# ===================== API 全链路 =====================

os.environ["DEEPSEEK_API_KEY"] = "sk-test"  # 让 lifespan 能构建图

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.graph import build_graph  # noqa: E402
from app.main import app  # noqa: E402

# FakeLLM 响应（对齐 test_api.py）：让流水线可跑到 copywriting/确认
_FB_RESEARCH = json.dumps({"summary": "s", "trends": ["t"], "audience_insights": ["a"],
                           "pain_points": ["p"], "keywords": ["k"]}, ensure_ascii=False)
_FB_COMPETITOR = json.dumps({"competitors": [], "gaps": ["g"]}, ensure_ascii=False)
_FB_STRATEGY = json.dumps({"target_audience": "白领", "core_value_proposition": "v",
                           "key_messages": ["m"], "tone_guidance": "g",
                           "content_angles": ["a"], "channel_strategy": "s"}, ensure_ascii=False)
_FB_COPY = json.dumps({"variants": [{"title": "3 个保温杯选购技巧，为什么买贵不如买对？",
                                     "body": "第一点：看材质。第二点：看保温时长。第三点：看密封性。",
                                     "hashtags": [], "notes": "n"}]}, ensure_ascii=False)
_FB_REVIEW = json.dumps({"passed": True, "feedback": "", "checklist": []}, ensure_ascii=False)


class _ApiFakeLLM:
    """对齐 test_api.py 的 FakeLLM（chat 接口 + 响应序列）。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def chat(self, messages, **kwargs):
        content = self.responses[self.calls]
        self.calls += 1
        return LLMResult(content=content, model="fake", prompt_tokens=100,
                         completion_tokens=50, latency_seconds=0.01)


@pytest.fixture()
def client(tmp_path):
    with TestClient(app) as c:
        # 必须在 lifespan 之后注入，否则被真实 graph 覆盖
        fake = _ApiFakeLLM([_FB_RESEARCH, _FB_COMPETITOR, _FB_STRATEGY, _FB_COPY, _FB_REVIEW] * 2)
        app.state.graph = build_graph(fake, db_path=tmp_path / "ckpt.db")
        yield c


def test_feedback_api_flow(client):
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 201
    r = client.post("/api/tasks", json={"topic": "保温杯", "channel_id": "xiaohongshu"})
    assert r.status_code == 201
    tid = r.json()["id"]

    # 手动回填
    r = client.post(f"/api/tasks/{tid}/feedback",
                    json={"views": 1200, "conversions": 42, "score": 3.8})
    assert r.status_code == 200
    body = r.json()
    assert body["views"] == 1200 and body["is_simulated"] == 0

    # 重复回填 → 幂等更新
    r = client.post(f"/api/tasks/{tid}/feedback",
                    json={"views": 1500, "conversions": 50, "score": 4.1})
    body = r.json()
    assert body["views"] == 1500

    # 模拟回填（特征相关，score 在 0-5）
    r = client.post(f"/api/tasks/{tid}/feedback/simulate")
    assert r.status_code == 200
    sim = r.json()
    assert sim["is_simulated"] == 1
    assert 0 <= sim["score"] <= 5

    # 读取
    r = client.get(f"/api/tasks/{tid}/feedback")
    assert r.status_code == 200
    assert r.json()["is_simulated"] == 1

    # 规律列表（无 feedback 规律 → 空）
    import uuid as _uuid
    bid = client.post("/api/brands",
                      json={"name": f"反馈暖芯-{_uuid.uuid4().hex[:6]}"}).json()["id"]
    r = client.get(f"/api/brands/{bid}/feedback-rules")
    assert r.status_code == 200 and r.json() == []

    # 删除规律（不存在时静默成功）
    r = client.delete(f"/api/brands/{bid}/feedback-rules/999")
    assert r.status_code == 200 and r.json() == {"ok": True}

    # 不存在任务 → 404
    r = client.post("/api/tasks/999999/feedback", json={"views": 1, "conversions": 0, "score": 1})
    assert r.status_code == 404


# ===================== 回填后自动学习（闭环触发） =====================

import json as _json  # noqa: E402

from app.feedback import run_feedback_learning  # noqa: E402


async def _seed_content_task(topic: str, title: str, score: float) -> int:
    tid = await repo.create_task(topic, "xiaohongshu")
    await repo.upsert_stage(
        tid, "copywriting", status="completed",
        output=_json.dumps({"variants": [{"title": title, "body": "第一点：看材质。",
                                          "hashtags": [], "notes": ""}]}, ensure_ascii=False),
    )
    await repo.upsert_content_feedback(tid, views=100, conversions=10, score=score)
    return tid


async def test_run_feedback_learning_flow():
    """回填数据后自动学习：高分内容含数字特征 → 提炼出正向 has_num 规律入库。"""
    import uuid as _uuid2
    bid = await repo.create_brand(f"反馈暖芯-学习-{_uuid2.uuid4().hex[:6]}")
    # 高分 2 条（标题含数字）+ 低分 2 条（标题无数字）
    await _seed_content_task("t1", "3 个选购技巧", 4.5)
    await _seed_content_task("t2", "7 天提升效率", 4.2)
    await _seed_content_task("t3", "选购指南", 1.5)
    await _seed_content_task("t4", "日常分享", 1.2)

    added = await run_feedback_learning(bid, llm=_FakeLLM())
    assert added >= 1

    rules = await repo.list_brand_preferences(bid)
    feedback_rules = [r for r in rules if r.get("source") == "feedback"]
    assert len(feedback_rules) >= 1
    assert feedback_rules[0]["strength"] > 0  # 含数字特征 → 正向规则
