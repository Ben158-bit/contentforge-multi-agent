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

        # 3. 编辑策略阶段（human-in-the-loop 干预）
        edit = client.put(
            f"/api/tasks/{task_id}/stages/strategy",
            json={"output": {"core_value_proposition": "人工修改的主张"}},
        )
        assert edit.status_code == 200
        strategy_stage = [s for s in edit.json()["stages"] if s["stage_key"] == "strategy"][0]
        assert strategy_stage["output"]["core_value_proposition"] == "人工修改的主张"

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
