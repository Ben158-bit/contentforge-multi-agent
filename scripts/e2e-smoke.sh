#!/usr/bin/env bash
# 端到端联调冒烟：后端(FastAPI + FAKE_LLM 演示模式) + 前端(Vite dev) 代理连通性
# 覆盖：健康检查、前端页面、Vite 代理 /api、任务创建、流水线执行、SSE 推送、确认定稿
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT=8000
FRONTEND_PORT=5173
DATA_DIR=$(mktemp -d)
PY=backend/.venv/Scripts/python

echo "==> 启动后端（FAKE_LLM 演示模式，端口 $BACKEND_PORT）"
FAKE_LLM=true DATA_DIR="$DATA_DIR" "$PY" -m uvicorn app.main:app --app-dir backend --port "$BACKEND_PORT" --log-level warning &
BACKEND_PID=$!

echo "==> 启动前端 dev server（端口 $FRONTEND_PORT）"
(cd frontend && npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
FRONTEND_PID=$!

cleanup() { kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true; }
trap cleanup EXIT

# 等待后端就绪
for _ in $(seq 1 40); do
  curl -sf "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1 && break
  sleep 1
done

# 等待前端就绪
for _ in $(seq 1 40); do
  curl -sf "http://localhost:$FRONTEND_PORT/" >/dev/null 2>&1 && break
  sleep 1
done

echo "==> 1) 后端健康检查"
curl -s "http://localhost:$BACKEND_PORT/api/health"
echo

echo "==> 2) 前端页面标题"
curl -sf "http://localhost:$FRONTEND_PORT/" | grep -o '<title>[^<]*</title>'

echo "==> 3) Vite 代理 /api → 后端"
curl -s "http://localhost:$FRONTEND_PORT/api/health"
echo

echo "==> 4) 创建任务（经前端代理，演示模式）"
TASK=$(curl -s -X POST "http://localhost:$FRONTEND_PORT/api/tasks" \
  -H 'Content-Type: application/json' \
  -d '{"topic":"智能保温杯新品推广","channel_id":"xiaohongshu","brand_name":"暖芯","target_audience":"25-35 都市白领","extra_requirements":"突出 24 小时保温"}')
TASK_ID=$(echo "$TASK" | "$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "任务 #$TASK_ID 已创建"

echo "==> 5) 等待流水线执行到人工确认（演示模式含一次审校打回）"
STATUS="pending"
for _ in $(seq 1 40); do
  STATUS=$(curl -s "http://localhost:$FRONTEND_PORT/api/tasks/$TASK_ID" \
    | "$PY" -c "import sys,json;print(json.load(sys.stdin)['status'])")
  [ "$STATUS" = "waiting_human" ] && break
  sleep 1
done
echo "任务状态: $STATUS"

echo "==> 6) SSE 进度推送（前 4 行）"
curl -s -N --max-time 6 "http://localhost:$FRONTEND_PORT/api/tasks/$TASK_ID/events" | head -4

echo "==> 7) 确认定稿（经前端代理）"
curl -s -X POST "http://localhost:$FRONTEND_PORT/api/tasks/$TASK_ID/confirm" \
  -H 'Content-Type: application/json' \
  -d '{"variants":[{"title":"定稿标题","body":"定稿正文","hashtags":["#保温杯"]}]}' \
  | "$PY" -c "import sys,json;t=json.load(sys.stdin);print('确认后状态:', t['status'], '| 工件数:', len(t['artifacts']))"

echo "==> 8) 统计"
curl -s "http://localhost:$FRONTEND_PORT/api/stats"
echo
echo "E2E SMOKE PASS"
