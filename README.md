# ContentForge · 多 Agent 营销内容工作台

> 基于 **LangGraph** 的多 Agent 协作系统：市场调研 → 竞品分析 → 策略制定 → 文案创作 → 审校优化，
> 每个阶段产出可**人工确认（human-in-the-loop）** 后再流入下一阶段。
> 面向 AI 应用/LLM 应用开发岗位的完整作品：**工程化架构 + 实时可观测性 + 开箱即用演示**。

## ✨ 亮点

- **多 Agent 编排**：LangGraph 图状态机 + SQLite checkpoint 持久化，任务可断点恢复
- **记忆层**：品牌档案（显式）+ 纠错学习（隐式）——确认时勾选「记入偏好」，系统自动从你的修改中学习品牌内容偏好，下次创作自动应用
- **Human-in-the-loop**：每阶段产出可预览、编辑、确认；编辑上游阶段（调研/竞品/策略）后确认会**重跑下游使编辑真正生效**；审校不通过自动打回创作（带上一轮反馈）重写
- **实时可观测性**：SSE 推送阶段进度；阶段级/任务级 token 成本与耗时统计（DeepSeek 计价）
- **演示模式**：无 API Key 即可体验完整流水线（内置 FakeLLM，含审校打回演示）
- **安全与可靠性**：Bearer Token 鉴权 + 写接口限流 + 提示词注入防护 + 请求体限制 + request_id 结构化日志；任务级超时看门狗 + 启动恢复 + 后台并发上限
- **全栈工程化**：FastAPI + React(Vite) + SQLite，Docker 部署，65 项后端测试 + 11 项前端测试 + GitHub Actions CI

## 🏗 架构

```
┌─────────────┐   SSE / REST    ┌──────────────────────────────────────────┐
│  React SPA  │ ──────────────► │              FastAPI 后端                  │
│  (Vite)     │                 │                                            │
│  · 任务列表  │                 │  /api/tasks       任务 CRUD + 后台执行      │
│  · 流水线视图│                 │  /api/tasks/{id}/events  SSE 进度推送      │
│  · 文案编辑  │                 │  /api/tasks/{id}/confirm  人工确认          │
│  · SSE 订阅  │                 │  /api/stats          成本/耗时统计         │
└─────────────┘                 └──────────────────┬─────────────────────────┘
                                                   │
                     LangGraph 流水线（agents/）   │
                     ┌─────────────────────────────▼───────────────────────┐
                     │ research → competitor → strategy → copywriting      │
                     │                                    ▲        │        │
                     │                        打回(带反馈)  │        │ 审校   │
                     │                                    └── review◄┘       │
                     │                                          │ 通过       │
                     │                                    human_confirm      │
                     │                                    (interrupt 暂停)    │
                     └──────────────────────┬────────────────────────────────┘
                                            │ checkpoint
                              ┌─────────────▼─────────────┐   ┌──────────────────┐
                              │ SQLite (业务库: 任务/阶段/ │   │ DeepSeek API      │
                              │ 工件 + checkpoint 库)     │   │ (或 FakeLLM 演示)  │
                              └───────────────────────────┘   └──────────────────┘
```

**流水线细节**（`backend/app/agents/`）：

| 节点 | 职责 | 输入 | 输出 |
|---|---|---|---|
| `research` 市场调研 | 行业趋势、受众洞察、痛点、关键词 | 任务输入 | 结构化调研报告 |
| `competitor` 竞品分析 | 竞品定位/卖点/内容策略拆解、机会点 | 调研报告 | 竞品矩阵 |
| `strategy` 策略制定 | 目标受众、核心主张、内容角度、语调 | 调研+竞品 | 营销策略 |
| `copywriting` 文案创作 | 按渠道模板生成 2-3 个变体 | 策略+渠道模板 | 文案变体 |
| `review` 审校优化 | 对照策略清单检查，不通过打回创作 | 策略+变体 | 通过/反馈 |
| `human_confirm` | `interrupt` 暂停图，等待人工确认 | 全部产出 | 确认结果 |

## 🚀 快速开始

### 1. 后端

```bash
cd backend
python -m venv ../.venv
../.venv/Scripts/pip install -r requirements.txt     # Windows；macOS/Linux 用 ../.venv/bin/pip

# 配置（二选一）
cp .env.example .env                                 # 填入 DEEPSEEK_API_KEY
# 或 演示模式：设置 FAKE_LLM=true（无需 API Key）
# 公网/生产部署务必设置 API_TOKEN（前端构建时用 VITE_API_TOKEN 注入）

../.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

验证：`curl http://localhost:8000/api/health` → `{"status":"ok","graph_ready":true}`

### 2. 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173（/api 自动代理到后端）
```

### 3. 端到端冒烟

```bash
bash scripts/e2e-smoke.sh   # 启动前后端 → 创建任务 → SSE → 确认定稿 → 统计
```

## 🧪 测试

```bash
cd backend
../.venv/Scripts/python -m pytest tests/ -q    # 65 项：编排/仓储/API/SSE/演示/安全/可靠性/记忆层
```

## 📁 目录结构

```
backend/
├── app/
│   ├── agents/          # LangGraph 编排核心
│   │   ├── graph.py     #   图组装：流水线+审校循环+checkpoint
│   │   ├── nodes.py     #   5 个 Agent 节点 + 人工确认
│   │   ├── prompts.py   #   Prompt 模板体系（JSON 输出）
│   │   ├── state.py     #   状态 schema
│   │   └── runner.py    #   同步执行器：逐步回写进度到 SQLite
│   ├── api/routes.py    # REST + SSE 路由
│   ├── db.py            # SQLite 数据层（版本化迁移：v1+v2）
│   ├── db_sql.py        # 统一同步 SQL 层（runner/memory 复用）
│   ├── repository.py    # 异步 CRUD
│   ├── config.py        # 配置管理（.env 校验）
│   ├── fake_llm.py      # 演示模式 FakeLLM
│   ├── memory.py        # 记忆层：品牌上下文 + 纠错学习
│   ├── logging_setup.py # 结构化 JSON 日志 + request_id
│   ├── security.py      # 鉴权 / 限流 / 请求体限制 / request_id
│   └── main.py          # FastAPI 入口
├── tests/               # 65 项测试
└── requirements.txt
frontend/                # React(Vite)+TS 单页应用
scripts/e2e-smoke.sh     # 端到端冒烟脚本
```

## 🔌 API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tasks` | 创建任务并启动流水线（后台执行，可关联品牌） |
| GET | `/api/tasks` / `/api/tasks/{id}` | 任务列表 / 详情（含阶段与工件） |
| PUT | `/api/tasks/{id}/stages/{stage}` | 编辑阶段产出（人工干预，上游编辑后 confirm 会重跑下游） |
| POST | `/api/tasks/{id}/confirm` | 人工确认，携带编辑后文案；可选 learn+brand_id 触发纠错学习 |
| POST | `/api/tasks/{id}/rerun` | 重置 checkpoint 重跑整个流水线 |
| GET | `/api/tasks/{id}/events` | SSE 实时进度推送 |
| GET | `/api/stats` / `/api/channels` | 成本统计 / 渠道模板 |
| POST/GET/PUT/DELETE | `/api/brands...` | 品牌档案 CRUD（记忆层） |

## 🧠 关键技术决策

- **LangGraph 0.6.x + 同步执行**：实测 1.x 在 Python 3.10 下 `interrupt` 的 async 执行路径存在上下文缺陷（`trace=False` 分支不设置 config contextvar / `run_in_executor` 丢上下文），0.6.x 同步路径最稳（见 `requirements.txt` 注释）
- **SQLite 双库**：业务库（任务/阶段/工件）+ checkpoint 库（LangGraph 断点），WAL 模式支持多连接
- **成本可观测**：每个节点记录 token 用量，按 DeepSeek 计价折算人民币，SSE 实时推送

## 📄 文档

- [演示流程](docs/demo.md) — 端到端演示步骤与预期效果
- [简历亮点](docs/resume-highlights.md) — 项目面试话术与亮点总结
