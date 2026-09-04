"""API 集成测试。

覆盖：
- 健康检查 / 渠道列表（无 graph 依赖）
- 缺少 API Key 时任务创建返回 400（降级可用）
- 注入 FakeLLM 图后：任务创建 → 后台执行 → 到达人工确认 → 确认完成 → 重跑
"""
from __future__ import annotations

import json
import os
import tempfile
import time

# 必须在导入 app 之前设置（临时数据目录 + 假 API Key，让 lifespan 能构建图）
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-api-test-")
os.environ["DEEPSEEK_API_KEY"] = "sk-test"

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.graph import build_graph  # noqa: E402
from app.llm import LLMResult  # noqa: E402
from app.main import app  # noqa: E402


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    def chat(self, messages, **kwargs):
        content = self.responses[self.calls]
        self.calls += 1
        return LLMResult(
            content=content, model="fake", prompt_tokens=100,
            completion_tokens=50, latency_seconds=0.01,
        )


RESEARCH = json.dumps({"summary": "s", "trends": ["t"], "audience_insights": ["a"],
                       "pain_points": ["p"], "keywords": ["k"]}, ensure_ascii=False)
COMPETITOR = json.dumps({"competitors": [], "gaps": ["g"]}, ensure_ascii=False)
STRATEGY = json.dumps({"target_audience": "白领", "core_value_proposition": "v",
                       "key_messages": ["m"], "tone_guidance": "g",
                       "content_angles": ["a"], "channel_strategy": "s"}, ensure_ascii=False)
COPY = json.dumps({"variants": [{"title": "标题", "body": "正文", "hashtags": [], "notes": "n"}]},
                  ensure_ascii=False)
REVIEW_PASS = json.dumps({"passed": True, "feedback": "", "checklist": []}, ensure_ascii=False)


def _wait_status(client: TestClient, task_id: int, target: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/tasks/{task_id}").json()
        if last["status"] == target:
            return last
        time.sleep(0.1)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内到达 {target}，当前 {last['status'] if last else None}")


def test_health_and_channels():
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        channels = client.get("/api/channels").json()
        assert {c["id"] for c in channels} >= {"xiaohongshu", "wechat", "weibo", "product_page"}


def test_delete_missing_task_returns_404():
    with TestClient(app) as client:
        resp = client.delete("/api/tasks/999999999")
        assert resp.status_code == 404


def test_create_task_without_graph_returns_400(monkeypatch):
    """缺少 API Key（图构建失败）时创建任务应返回 400。"""
    import app.main as main_module

    def _boom():
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    monkeypatch.setattr(main_module, "get_llm_client", _boom)
    with TestClient(app) as client:
        resp = client.post("/api/tasks", json={"topic": "x", "channel_id": "xiaohongshu"})
        assert resp.status_code == 400
        assert "DEEPSEEK_API_KEY" in resp.json()["detail"]


def test_task_full_flow(tmp_path):
    """创建任务 → 后台执行 → 到达人工确认 → 编辑阶段 → 确认完成 → 重跑。"""
    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS] * 2)
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")

    with TestClient(app) as client:
        app.state.graph = graph

        # 1. 创建任务
        resp = client.post("/api/tasks", json={
            "topic": "智能保温杯", "channel_id": "xiaohongshu",
            "brand_name": "暖芯", "target_audience": "25-35 都市白领",
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # 2. 后台执行到人工确认
        detail = _wait_status(client, task_id, "waiting_human")
        stage_status = {s["stage_key"]: s["status"] for s in detail["stages"]}
        assert set(stage_status) == {"research", "competitor", "strategy",
                                     "copywriting", "review"}
        assert all(v == "completed" for v in stage_status.values())
        assert detail["total_cost"] > 0
        assert detail["stages"][0]["output"]["summary"] == "s"

        # 3. 编辑文案阶段（human-in-the-loop 干预）
        edit = client.put(
            f"/api/tasks/{task_id}/stages/copywriting",
            json={"variants": [{"title": "人工编辑标题", "body": "正文",
                                "hashtags": [], "notes": "人工干预"}]},
        )
        assert edit.status_code == 200
        copy_stage = [s for s in edit.json()["stages"] if s["stage_key"] == "copywriting"][0]
        assert copy_stage["output"][0]["title"] == "人工编辑标题"

        # 4. 确认推进（携带编辑后的文案）
        confirm = client.post(
            f"/api/tasks/{task_id}/confirm",
            json={"variants": [{"title": "标题-确认", "body": "正文-确认",
                                "hashtags": [], "notes": "人工定稿"}]},
        )
        assert confirm.status_code == 200
        done = _wait_status(client, task_id, "completed")
        assert done["artifacts"][0]["content"]["title"] == "标题-确认"
        assert done["total_cost"] > 0
        # 单阶段成本之和应等于任务累计成本（阶段成本为增量，非累计值）
        stage_cost_sum = round(sum(s["cost"] for s in done["stages"]), 6)
        assert stage_cost_sum == round(done["total_cost"], 6), (stage_cost_sum, done["total_cost"])

        # 5. 重跑（从 checkpoint 重置后重新生成）
        rerun = client.post(f"/api/tasks/{task_id}/rerun")
        assert rerun.status_code == 200
        rerun_detail = _wait_status(client, task_id, "waiting_human")
        assert rerun_detail["status"] == "waiting_human"


def test_task_sse_and_stats(tmp_path):
    """SSE 进度推送（真实 HTTP 服务器 + 流式读取）与统计端点。

    说明：ASGITransport 不流式（缓冲整个 body），SSE 长连接无法用它测试，
    因此启动真实 uvicorn 服务器（后台线程，同进程共享 app 以注入 fake graph）。
    """
    import socket
    import threading

    import httpx
    import uvicorn

    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS] * 2)
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")

    # 获取空闲端口
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # 线程中不注册信号
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn 服务器启动失败"
        app.state.graph = graph  # 注入 fake graph（同进程共享）

        base = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=10) as client:
            # 创建任务
            resp = client.post(f"{base}/api/tasks", json={
                "topic": "智能保温杯", "channel_id": "xiaohongshu", "brand_name": "暖芯",
            })
            assert resp.status_code == 201
            task_id = resp.json()["id"]
            # 等待到达人工确认
            deadline = time.time() + 15
            while time.time() < deadline:
                d = client.get(f"{base}/api/tasks/{task_id}").json()
                if d["status"] == "waiting_human":
                    break
                time.sleep(0.1)
            assert d["status"] == "waiting_human"

            # SSE：读首帧 → confirm → 读 done 事件
            frames = []
            with client.stream("GET", f"{base}/api/tasks/{task_id}/events") as sse:
                assert sse.status_code == 200
                assert "text/event-stream" in sse.headers["content-type"]
                it = sse.iter_lines()
                first = next(it)
                while first and not first.startswith("data:"):
                    first = next(it)
                assert '"waiting_human"' in first

                # 确认推进（SSE 连接保持时发起新请求）
                confirm = client.post(
                    f"{base}/api/tasks/{task_id}/confirm",
                    json={"variants": [{"title": "定稿", "body": "正文",
                                        "hashtags": [], "notes": ""}]},
                )
                assert confirm.status_code == 200

                # 继续读流直到 done
                for line in it:
                    frames.append(line)
                    if "event: done" in line:
                        break
                else:
                    raise AssertionError(f"SSE 未收到 done 事件: frames={frames}")

            assert any('"completed"' in l for l in frames if l.startswith("data:"))

            # 统计端点
            stats = client.get(f"{base}/api/stats").json()
            assert stats["task_count"] >= 1
            assert stats["completed_count"] >= 1
            assert stats["total_cost"] > 0
            assert stats["avg_cost"] > 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class ReflectiveLLM:
    """文案标题回显策略主张，用于验证上游编辑真正影响下游文案。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        text = " ".join(m["content"] for m in messages if m.get("content"))
        if "市场调研分析师" in text:
            out = {"summary": "s", "trends": ["t"], "audience_insights": ["a"],
                   "pain_points": ["p"], "keywords": ["k"]}
        elif "竞品分析专家" in text:
            out = {"competitors": [], "gaps": ["g"]}
        elif "营销策略总监" in text:
            out = {"target_audience": "白领", "core_value_proposition": "原主张",
                   "key_messages": ["m"], "tone_guidance": "g",
                   "content_angles": ["a"], "channel_strategy": "s"}
        elif "文案创作专家" in text:
            import re
            match = re.search(r'core_value_proposition["\']?\s*:\s*["\']([^"\']+)', text)
            value = match.group(1) if match else "?"
            out = {"variants": [{"title": f"标题-{value}", "body": "正文",
                                 "hashtags": [], "notes": ""}]}
        elif "内容审校专家" in text:
            out = {"passed": True, "feedback": "", "checklist": []}
        else:
            out = {}
        return LLMResult(content=json.dumps(out, ensure_ascii=False), model="fake",
                         prompt_tokens=100, completion_tokens=50, latency_seconds=0.01)


def test_edit_upstream_stage_affects_copywriting(tmp_path):
    """编辑上游阶段后 confirm：下游重新生成文案，编辑内容真正生效。"""
    graph = build_graph(ReflectiveLLM(), db_path=tmp_path / "ckpt.db")

    with TestClient(app) as client:
        app.state.graph = graph

        resp = client.post("/api/tasks", json={
            "topic": "智能保温杯", "channel_id": "xiaohongshu", "brand_name": "暖芯",
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        detail = _wait_status(client, task_id, "waiting_human")
        copy_stage = [s for s in detail["stages"] if s["stage_key"] == "copywriting"][0]
        assert any("原主张" in v["title"] for v in copy_stage["output"])

        # 编辑策略阶段
        edit = client.put(
            f"/api/tasks/{task_id}/stages/strategy",
            json={"output": {"core_value_proposition": "人工修改的主张"}},
        )
        assert edit.status_code == 200

        # confirm：因上游编辑触发下游重跑，任务应再次回到 waiting_human
        confirm = client.post(f"/api/tasks/{task_id}/confirm", json={"variants": []})
        assert confirm.status_code == 200
        # 等待下游重跑完成：轮询直到文案反映编辑内容（confirm 已后台化，立即返回）
        deadline = time.time() + 15
        titles = []
        while time.time() < deadline:
            d = client.get(f"/api/tasks/{task_id}").json()
            copy_stage = [s for s in d["stages"] if s["stage_key"] == "copywriting"][0]
            titles = [v["title"] for v in copy_stage["output"]]
            if d["status"] == "waiting_human" and any("人工修改的主张" in t for t in titles):
                break
            time.sleep(0.1)
        assert any("人工修改的主张" in t for t in titles), titles

        # 第二次确认完成定稿
        confirm2 = client.post(f"/api/tasks/{task_id}/confirm", json={
            "variants": [{"title": "定稿", "body": "正文", "hashtags": [], "notes": ""}],
        })
        assert confirm2.status_code == 200
        done = _wait_status(client, task_id, "completed")
        assert done["artifacts"][0]["content"]["title"] == "定稿"


class SlowLLM(ReflectiveLLM):
    """每个 LLM 调用睡 0.5s，用于验证 confirm/rerun 后台化不阻塞请求。"""

    def chat(self, messages, **kwargs):
        time.sleep(0.5)
        return super().chat(messages, **kwargs)


def test_confirm_returns_immediately(tmp_path):
    """confirm 触发编辑回填重跑（含 LLM 调用）时，接口应立即返回而非阻塞。"""
    graph = build_graph(SlowLLM(), db_path=tmp_path / "ckpt.db")
    with TestClient(app) as client:
        app.state.graph = graph
        resp = client.post("/api/tasks", json={
            "topic": "智能保温杯", "channel_id": "xiaohongshu", "brand_name": "暖芯",
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        _wait_status(client, task_id, "waiting_human", timeout=30)

        client.put(
            f"/api/tasks/{task_id}/stages/strategy",
            json={"output": {"core_value_proposition": "人工修改的主张"}},
        )
        start = time.time()
        confirm = client.post(f"/api/tasks/{task_id}/confirm", json={"variants": []})
        elapsed = time.time() - start
        assert confirm.status_code == 200
        assert elapsed < 1.0, f"confirm 阻塞了 {elapsed:.2f}s"
        _wait_status(client, task_id, "waiting_human", timeout=30)


def test_rerun_returns_immediately(tmp_path):
    """rerun 重跑整条流水线（含多次 LLM 调用）时，接口应立即返回而非阻塞。"""
    graph = build_graph(SlowLLM(), db_path=tmp_path / "ckpt.db")
    with TestClient(app) as client:
        app.state.graph = graph
        resp = client.post("/api/tasks", json={
            "topic": "智能保温杯", "channel_id": "xiaohongshu", "brand_name": "暖芯",
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        _wait_status(client, task_id, "waiting_human", timeout=30)

        start = time.time()
        rerun = client.post(f"/api/tasks/{task_id}/rerun")
        elapsed = time.time() - start
        assert rerun.status_code == 200
        assert elapsed < 1.0, f"rerun 阻塞了 {elapsed:.2f}s"
        _wait_status(client, task_id, "waiting_human", timeout=30)


def test_concurrent_tasks_finish_under_semaphore(tmp_path):
    """并发创建多个任务时全部正常到达人工确认（信号量排队，不崩不超限）。"""
    graph = build_graph(SlowLLM(), db_path=tmp_path / "ckpt.db")
    with TestClient(app) as client:
        app.state.graph = graph
        task_ids = []
        for i in range(5):
            resp = client.post("/api/tasks", json={
                "topic": f"智能保温杯{i}", "channel_id": "xiaohongshu", "brand_name": "暖芯",
            })
            assert resp.status_code == 201
            task_ids.append(resp.json()["id"])
        for tid in task_ids:
            _wait_status(client, tid, "waiting_human", timeout=60)


class RuleLLM:
    """固定返回一条偏好规则的假 LLM（纠错学习用）。"""

    def chat(self, messages, **kwargs):
        from app.llm import LLMResult

        return LLMResult(content=json.dumps({"rules": ["标题避免感叹号"]}),
                         model="fake", prompt_tokens=10, completion_tokens=5,
                         latency_seconds=0.01)


def test_brands_api_and_learn_flow(tmp_path, monkeypatch):
    """品牌 CRUD API + 关联品牌任务 + 勾选学习偏好入库（API 全链路）。"""
    import app.memory as memory_module

    monkeypatch.setattr(memory_module, "get_llm_client", lambda: RuleLLM())
    fake = FakeLLM([RESEARCH, COMPETITOR, STRATEGY, COPY, REVIEW_PASS] * 2)
    graph = build_graph(fake, db_path=tmp_path / "ckpt.db")

    with TestClient(app) as client:
        app.state.graph = graph
        # 品牌 CRUD
        resp = client.post("/api/brands", json={"name": "暖芯", "tone": "温暖亲切"})
        assert resp.status_code == 201
        bid = resp.json()["id"]
        assert client.get("/api/brands").json()[0]["name"] == "暖芯"
        assert client.get(f"/api/brands/{bid}").json()["tone"] == "温暖亲切"
        assert client.post("/api/brands", json={"name": "暖芯"}).status_code == 409
        assert client.get("/api/brands/99999").status_code == 404
        upd = client.put(f"/api/brands/{bid}", json={"tone": "专业可信"})
        assert upd.status_code == 200 and upd.json()["tone"] == "专业可信"

        # 关联品牌创建任务
        resp = client.post("/api/tasks", json={
            "topic": "智能保温杯", "channel_id": "xiaohongshu",
            "brand_name": "暖芯", "brand_id": bid,
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        _wait_status(client, task_id, "waiting_human")

        # 确认 + 勾选学习（提交与 AI 原版不同的文案 → 触发学习）
        confirm = client.post(f"/api/tasks/{task_id}/confirm", json={
            "variants": [{"title": "标题-用户修改", "body": "正文", "hashtags": [], "notes": ""}],
            "learn": True,
            "brand_id": bid,
        })
        assert confirm.status_code == 200
        _wait_status(client, task_id, "completed")

        # 偏好已入库
        brand = client.get(f"/api/brands/{bid}").json()
        assert len(brand["preferences"]) == 1
        assert brand["preferences"][0]["rule_text"] == "标题避免感叹号"

        # 删除品牌 → 偏好级联清理
        assert client.delete(f"/api/brands/{bid}").status_code == 200
        assert client.get(f"/api/brands/{bid}").status_code == 404
