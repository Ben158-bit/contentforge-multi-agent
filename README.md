# ContentForge · 多 Agent 营销内容工作台

> 基于 **LangGraph** 的多 Agent 协作系统：市场调研 → 竞品分析 → 策略制定 → 文案创作 → 审校优化，
> 每个阶段产出可**人工确认（human-in-the-loop）** 后再流入下一阶段。
> 面向 AI 应用/LLM 应用开发岗位的完整作品：**工程化架构 + 实时可观测性 + 开箱即用演示**。

## ✨ 亮点

- **多 Agent 编排**：LangGraph 图状态机 + SQLite checkpoint 持久化，任务可断点恢复
- **确定性守门层**：字数降档（五级）、广告法违禁词、话题标签规范、Emoji 策略、标题体检、AI 味扫描、**渠道内容契约核验**——**能枚举的规则一律用代码保证 100% 复现**，只把需要判断力的部分留给模型
- **渠道内容契约**：每个渠道在 `channels.yaml` 里用**结构化的字段清单**声明「这篇稿子必须有什么」（如小红书的钩子类型/场景/体验细节/3-5 个卖点/分层标签/CTA），守门层逐项核验并给出「哪一项缺了」；只**标记**进待办，绝不自动编造内容，也不改变硬合规判定
- **渠道差异前移**：渠道上下文（结构/调性/长度/关键词口径）注入 `research` 与 `strategy`，策略阶段产出该渠道**原生的内容骨架**（`content_structure`）并由 `copywriting` 按序展开 —— 渠道差异不再只压在最后一棒，而是从调研就开始分化
- **去 AI 味改写（仅公众号）**：确定性预扫 → 模型改写 → 确定性复扫 → 验收判定（命中必须下降 / 不引入新的致命句式 / 不塌陷长度），未通过或异常**一律保留原文**，绝不静默改坏
- **记忆层**：品牌档案（显式）+ 纠错学习（隐式）——确认时勾选「记入偏好」，系统自动从你的修改中学习品牌内容偏好，下次创作自动应用
- **Human-in-the-loop**：每阶段产出可预览、编辑、确认；编辑上游阶段（调研/竞品/策略）后确认会**重跑下游使编辑真正生效**；审校不通过自动打回创作（带上一轮反馈）重写
- **实时可观测性**：SSE 推送阶段进度；阶段级/任务级 token 成本与耗时统计（DeepSeek 计价）
- **演示模式**：无 API Key 即可体验完整流水线（内置 FakeLLM，覆盖全部 Agent 角色，含审校打回与去 AI 味改写演示）
- **安全与可靠性**：Bearer Token 鉴权 + 写接口限流 + 提示词注入防护 + 请求体限制 + request_id 结构化日志；任务级超时看门狗 + 启动恢复 + 后台并发上限；**失败原因落库并在界面展示**，任务不会静默卡在「等待」
- **全栈工程化**：FastAPI + React(Vite) + SQLite，Docker 部署，547 项后端测试 + 32 项评测测试 + 84 项前端测试 + GitHub Actions CI

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
                     │                                        │            │
                     │  去 AI 味(humanize，仅公众号) ◄─────────┘            │
                     │        │                                            │
                     │        ▼                                            │
                     │   确定性守门层(guardrail) ──► review ──► human_confirm │
                     │                              ▲     │      (interrupt)  │
                     │                   打回(带反馈) │     │ 通过          │
                     │                              └─────┘                │
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
| `research` 市场调研 | 行业趋势、受众洞察、痛点、关键词（关键词按**渠道检索口径**产出） | 任务输入 + 渠道规范 | 结构化调研报告 |
| `competitor` 竞品分析 | 竞品定位/卖点/内容策略拆解、机会点 | 调研报告 | 竞品矩阵 |
| `strategy` 策略制定 | 目标受众、核心主张、内容角度、语调 + **该渠道原生的内容骨架** | 调研+竞品+渠道规范 | 营销策略 |
| `copywriting` 文案创作 | 按渠道模板生成 2-3 个变体 | 策略+渠道模板 | 文案变体 |
| `humanize` 去 AI 味 | **仅公众号**：确定性预扫 → 模型改写 → 确定性复扫 → 验收判定；未通过或异常一律**保留原文** | 文案变体 | 处置报告 + 改写稿 |
| `guardrail` 确定性守门层 | 字数降档 / 违禁词 / 标签 / Emoji / AI 味扫描 / 标题体检，产出结构化报告 | 文案变体+渠道规则 | 守门报告 |
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
# 后端（必须用项目自带的 .venv —— 系统 Python 没有 langgraph / aiosqlite）
cd backend
../.venv/Scripts/python.exe -m pytest tests/ -q          # 编排/仓储/API/SSE/演示/安全/可靠性/存储

# 评测套件
cd ../evals && python -m pytest -q

# 前端
cd ../frontend && npm test
```

> **并发写库的注意事项（已修复，留作背景）**：项目由多条路径并发读写同一个 WAL 库
> （API 层 aiosqlite + 流水线线程里的同步 sqlite3）。进程内必须持有 `app/db_anchor.py`
> 的保活连接——否则 WAL 会在「最后一个连接关闭」时删除 `-wal` / `-shm`，
> 并发的新连接重建它们会失败并抛 `attempt to write a readonly database`，
> 表现为流水线跑到一半整任务失败（成本 0、阶段全停在「等待」）。
> 该竞态的复盘、对照实验数据与回归测试见 `app/db_anchor.py` 与 `tests/test_db_wal.py`。

> **不要用原生 `confirm()` / `alert()` / `prompt()`（已修复，留作背景）**：
> 破坏性操作（重新生成、删除任务、删除品牌）的确认一律用 `components/ConfirmButton.tsx`
> 的行内确认条。原生模态框在嵌入式宿主（内置浏览器预览面板 = sandbox iframe /
> Electron 内嵌视图）里**会阻塞渲染进程却不显示**，用户看到的只是「点击后毫无反应」，
> 且网络面板干净、控制台无报错，完全无从排查。`src/no-native-dialogs.test.ts`
> 会扫描源码阻止其回归。

## 📁 目录结构

```
backend/
├── app/
│   ├── agents/          # LangGraph 编排核心
│   │   ├── graph.py     #   图组装：流水线+审校循环+checkpoint
│   │   ├── pipeline_definition.py  # 流水线定义模型（节点/边/契约，加载期校验）
│   │   ├── pipelines/   #   流水线 YAML（marketing.yaml：阶段顺序与契约）
│   │   ├── nodes/       #   Agent 节点（_common 管道 + 各阶段 + 守门 + 去 AI 味 + 人工确认）
│   │   ├── prompts.py   #   Prompt 模板体系（JSON 输出）
│   │   ├── state.py     #   状态 schema
│   │   └── runner.py    #   同步执行器：逐步回写进度到 SQLite
│   ├── guardrails/      # 确定性守门层（纯函数，无 LLM）
│   │   ├── text_metrics.py  # 字数口径唯一真源
│   │   ├── length_policy.py # 五级字数降档
│   │   ├── compliance.py    # 广告法违禁词
│   │   ├── hashtags.py      # 话题标签规范
│   │   ├── content_contract.py # 渠道内容契约核验 + 分层标签 → hashtags 推导
│   │   ├── emoji_policy.py  # Emoji 策略
│   │   ├── ai_tells.py      # AI 味扫描（正则+结构指标）
│   │   ├── title_check.py   # 标题六维体检
│   │   ├── report.py        # 守门报告 Pydantic 模型
│   │   ├── postprocess.py   # 编排：归一化 → 检出 → 报告
│   │   └── rules/           # 规则资产（YAML：违禁词 / AI 味 / 标题）
│   ├── api/routes.py    # REST + SSE 路由
│   ├── db.py            # SQLite 数据层（版本化迁移 v1–v4）
│   ├── db_sql.py        # 统一同步 SQL 层（runner/memory 复用）
│   ├── db_anchor.py     # WAL 保活锚点：挡住「关连接即删 -wal/-shm」的并发竞态
│   ├── repository.py    # 异步 CRUD
│   ├── config.py        # 配置管理（.env 校验）
│   ├── fake_llm.py      # 演示模式 FakeLLM
│   ├── memory.py        # 记忆层：品牌上下文 + 纠错学习
│   ├── logging_setup.py # 结构化 JSON 日志 + request_id
│   ├── security.py      # 鉴权 / 限流 / 请求体限制 / request_id
│   ├── templates/       # 渠道配置（channels.yaml：风格提示 + 守门参数）
│   └── main.py          # FastAPI 入口
├── tests/               # 后端测试
└── requirements.txt
frontend/                # React(Vite)+TS 单页应用
evals/                   # 评测套件（数据集 / 指标 / 基线 / 回归闸门）
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

## 📐 渠道内容契约

**要解决的问题**：渠道要求原先只是提示词里的一句话（「标题 18-20 字」「2-3 个热门大标签 + 3-5 个长尾标签」）。
模型可能没照做，而系统**看不见**——写了规则 ≠ 执行了规则。

**做法**：把可穷举的要求从提示词里「提」出来，变成有字段、可核验的声明式契约。

```yaml
# backend/app/templates/channels.yaml（小红书节选）
content_contract:
  - {field: hook_type, kind: enum, label: 开头钩子类型,
     values: [场景痛点, 反常识, 数字对比, 用户提问, 结论前置, 真实体验, 平替避坑]}
  - {field: selling_points, kind: list, label: 核心卖点, min: 3, max: 5}
  - field: tags_by_tier
    kind: tiered
    label: 分层话题标签
    feeds: hashtags            # ← 标签的唯一来源
    tiers:
      - {key: hot,      label: 热门大标签,   min: 2, max: 3}
      - {key: longtail, label: 精准长尾标签, min: 3, max: 5}
  - {field: cta, kind: text, label: 行动号召}
```

五种 `kind`：`text`（非空）/ `list`（数量区间，可带 `item_fields`）/ `enum`（取值集合）/
`tiered`（分层数量 + 扁平化）/ `headings`（数量 + **每个标题必须原样出现在正文里**）。

三条不可越界的约定（均有测试守着）：

1. **只标记，不改判**。契约缺口进 `needs_human`，但**绝不改变 `passed`**。
   `passed` 只表达硬合规（字数 / 违禁词 / 致命 AI 味）；「内容不够好」是 `review` 的职责。
2. **核验者绝不补内容**。不生成缺失的 CTA、不替模型编卖点——缺什么就如实报「缺什么」。
3. **未声明契约的渠道完全不核验**，不做任何隐式推断。

**三重真源一致性**（`channels.yaml` 声明 ↔ `prompts.py` 告诉模型填什么 ↔ `content_contract.py` 核验什么）
由 `tests/test_content_contract.py` 双向钉死，任一处漂移即测试失败。

**标签的单一来源**：小红书的 `tags_by_tier` 是标签唯一真源，在**创作节点出口**就扁平化成
可发布形态（按渠道样式补 `#`）写入 `hashtags`——不能等守门层，因为 `runner` 按**节点名**写阶段产出，
守门节点回写状态通道不会更新 `copywriting` 那行记录，而前端编辑器读的正是它。

## 🔀 渠道差异前移（`research` / `strategy`）

**要解决的问题**：改造前 `research` / `competitor` / `strategy` 只拿到 `channel_id` 这个**字符串**，
拿不到渠道规范——于是「做小红书笔记」和「做电商详情页」的调研与策略是**同一套逻辑**，
渠道差异全部压在 `copywriting` 最后一棒，只能靠提示词硬拧。

**做法**（真源仍只有 `channels.yaml`，不新增第二份渠道描述）：

| 环节 | 变化 |
|---|---|
| `research` | 注入渠道上下文；`keywords` 改为按**该渠道用户的检索习惯**产出（小红书「推荐/测评/避坑」vs 公众号「行业观察/深度解析」） |
| `strategy` | 注入渠道上下文；新增 `content_structure` —— 由渠道标准结构（`channels.yaml` 的 `structure`）**适配本任务**后的有序骨架 |
| `copywriting` | 正文**必须按 `content_structure` 顺序展开**；策略没给骨架时退回渠道标准结构（同样来自 `channels.yaml`） |
| `review` | 把「正文是否按骨架展开」列为一条 checklist 项 |
| `competitor` | **刻意不发渠道上下文** —— 竞品是跨渠道竞争的，只从单一渠道视角拆解会被带偏（有测试守着） |

演示模式的骨架同样从 `channels.yaml` 现取（`_demo_structure`），所以改配置演示跟着变，
不会有「配置改了、演示还停在旧结构」的漂移。

## 📋 渠道规范符合度清单（`review` 逐项核对）

**要解决的问题**：改造前 `review` 审校是**自由判断**，渠道 `copywriting_guide` 只作为文本依据塞进
提示词——「审校有没有真的逐条看渠道规范」完全靠自觉。规则写了，但没人核对它执行了没有。

**做法**：每个渠道在 `channels.yaml` 声明 `review_checklist`（判断力项清单），
`build_review_messages` 把它渲染成「必须逐项核对、每项都写进 checklist 输出」的硬要求，
前端把 checklist 逐项 ✓/✕ 展示。

**与确定性层的边界**（有测试守着，互补不重复）：

| 层 | 管什么 | 谁核验 |
|---|---|---|
| 守门层 / 契约层 | 「有没有 / 超没超」：字段缺不缺、枚举合不合法、字数超没超、标签数量 | 确定性代码（零 token） |
| `review_checklist` | 「好不好 / 对不对」：公式用没用对、风格像不像、配比合不合理 | 审校 Agent（判断力） |

因此 `review_checklist` 里**绝不允许**出现「标题 18-20 字」「2-3 大标签 + 3-5 长尾」这类已被确定性
覆盖的项——那是重复核验，两套口径必然打架。演示模式同样从 `channels.yaml` 现取，改配置跟着变。

## ✍️ 编辑器实时字数辅助

**要解决的问题**：文案变体编辑器是纯 input / textarea，**没有任何实时反馈**——标题写多少字、
正文超没超上限，要等守门层跑完才知道，然后被打回再改。后端确定性数据（字数口径、达标区间）
没有反馈到编辑侧，形成「写了规则、核验了规则、但编辑时看不到规则」的断点。

**做法**：
- 后端 `TitleCheckModel` 把**渠道标题达标区间**（`min_chars` / `max_chars`，来自 `channels.yaml`）
  回写进守门报告；正文上限 `max_chars` 本就随报告返回。
- 前端编辑器在标题 / 正文下方**实时显示字数**：标题「N 字 · 达标 18–20 字」（越界标琥珀色），
  正文「N / max_chars 字」（超限标红，并给出超出字数）。
- 计数用 `Array.from(s).length` 按 code point 计，**精确对齐后端 `raw` 口径**（`len(text)`），
  避免 emoji 等代理对造成的「编辑侧 40 字、守门层 39 字」漂移。
- 区间值一律由后端带来，前端**不硬编码**渠道区间表；渠道未配置区间（微博 / 产品页）则不显示提示。

## 🎨 设计 token 与深色模式

**要解决的问题**：样式里的颜色 / 圆角 / 阴影大量是字面量（`#fdecec`、`#9333ea` 等散落各处），
只有一套亮色主题；头部是紫蓝渐变——典型的「AI 模板味」。

**做法**：
- **token 单一来源**：所有颜色 / 圆角 / 阴影收敛进 `:root` 的 CSS 变量（中性 / 品牌 / 状态 / 圆角 / 阴影），
  业务样式一律引用 token，**组件与 CSS 里禁止硬编码色值**。
- **深色模式**：`html[data-theme="dark"]` 只覆盖 token，业务样式无需感知主题；
  同时声明 `color-scheme` 让原生控件（select / 滚动条）跟随。
- **主题切换**：`useTheme` hook——localStorage `cf-theme` 优先，否则跟随系统 `prefers-color-scheme`；
  用户没手动选过时跟随系统实时变化。图标用内联 SVG（非 emoji）。
- **防 FOUC**：`index.html` 内联脚本在 React 挂载前设好 `data-theme`，避免深色用户首帧白屏闪烁。

```css
/* 业务样式只引用 token，两种主题自动适配 */
.error-box { background: var(--red-soft); border-color: var(--red-border); }
```

## 🩺 守门报告可操作化

**要解决的问题**：守门报告把 `needs_human` 平铺成字符串——违禁词只显示词、AI 味命中
根本不显示，用户看不到「命中位置在哪、建议怎么改」，更没法一键去改。

**做法**：报告从 `variant_reports` 的结构化字段重建问题清单（`needs_human` 的每一项
——硬裁 / 超限 / 违禁词 / 致命 AI 味 / 标题长度——都能重建，不再平铺字符串），
按变体分组，每条问题带：

- **严重度徽章**：致命（红）/ 警告（琥珀）/ 提示（灰）；违禁词 `high` 与 AI 味 `fatal` 归一为「致命」
- **命中位置**：违禁词的上下文摘录（`context`）、AI 味的命中片段（`samples`）
- **怎么改**：替换 / 改写建议（来自规则资产的 `suggestion`）
- **「定位」按钮**：平滑滚动到编辑器对应字段（标题 / 正文）并高亮

内容契约的逐项结果展示在编辑器的变体卡上（紧挨字段）；守门报告只保留通过比例
（`内容契约 N/M`），避免同一缺口详情在两处重复。

AI 味命中的**中文分类名**（如「语言与句式」）由后端从规则资产的 `categories` 节带过来
（`category_label`）——前端不维护 category id → 中文 的映射表。

## 🔀 阶段编辑下游影响可视化

**要解决的问题**：编辑上游阶段（调研 / 竞品 / 策略）后，下游各阶段仍显示「已完成」
的**旧产出**——它们会在确认时被重跑，但界面上看不出「这些结果已经作废了」，
用户容易误以为下游还是最新的。

**做法**（下游关系唯一真源 = 前端 `STAGES` 顺序，与后端流水线定义一致）：

- **编辑中**：打开某阶段的编辑框时提示「保存后将重新执行下游：X → Y → Z」；
  末端阶段（审校）则明说「无下游需重新执行」。
- **编辑后**：所有「已编辑」阶段的下游在流水线上标为琥珀虚线「待重算」，
  提示其当前产出已作废、将在确认时重新执行。

## 🏷️ 标签可发布形态预览

编辑器的话题标签输入框下方实时渲染**发布后长什么样**：小红书 `#话题`（单井号）、
微博 `#话题#`（成对井号）、公众号 / 产品页提示「本渠道不使用话题标签」。

- 渠道标签风格（`hashtag_style`）由后端守门报告从 `channels.yaml` 带回（含缺省推断后的最终值）——
  **前端不维护「哪个渠道用哪种样式」的映射表**，与契约 label / 标题区间同一条铁律。
- 渲染行为与守门层 `normalize_tag` 对齐：先剥掉已有井号再按风格补，已规范化的标签进预览不会变成双井号。

## 🎯 竞品分析真实数据源（红狐 Hub）

竞品分析此前是纯 LLM —— 竞品定位、卖点、内容策略全靠模型内部知识编，
没有真实竞品实例可核验。现在接入了**红狐 Hub**（`redfox.hk` 数据 API，
`REDFOX_API_KEY` 为 Windows 用户级环境变量，不入库不写文件）：

- **三渠道覆盖**（能力不对等，按红狐现状如实接入）：
  小红书（笔记搜索：正文/标签/赞藏评/互动/链接）、公众号（文章搜索：标题/摘要/阅读赞藏评/链接）、
  微博（仅账号搜索：官方账号/粉丝/认证主体 —— 红狐无微博内容搜索接口）。
- **注入 + 溯源**：真实竞品实例注入竞品分析提示词（要求基于真实数据拆解、不得编造竞品），
  同时作为 `evidence` 字段附在竞品分析结果里 —— 前端竞品分析卡展示
  「真实竞品实例 N 条」，每条带真实互动数据与「查看原文」链接。
- **优雅降级**：无 key / 未知渠道 / 网络失败一律返回空列表退回模型内部知识，
  绝不中断流水线；渠道 → 红狐端点映射是**单一真源**（只在 `agents/competitor_source.py`）。
- **核心品类词提炼**：营销主题常是完整长句（红狐对长句命中差），抓取前先按
  常见营销动作词/后缀截断出核心品类词（「磁轴键盘新品上市推广」→「磁轴键盘」），
  并按「核心词 → 原主题 → 品牌」多级回退，谁先抓到非空用谁。
- **零新依赖**：`urllib` 直连 + 两级网络回退（先直连再系统代理），
  不引入 `redfox-python-sdk`。

## ✍️ 竞品分析指导文案口吻差异化

**要解决的问题**：竞品分析的结论（竞品定位/卖点/内容策略 + 差异化机会点）
此前只被策略阶段消费，且被压缩成一句 `tone_guidance` —— 文案创作阶段
**根本看不到竞品怎么写**，无论什么主题都只能按「渠道固定语调 + 一句话策略」写，
口吻天然同质化（换个产品还是同一个味）。

**落地**：

- **`build_copywriting_messages` 注入竞品分析结论**：竞品定位/卖点/内容策略、
  `gaps` 差异化机会点、真实竞品标题样本（evidence）全部进入文案创作提示词；
  system 与 user 双侧要求「口吻、切入角度、句式必须与竞品刻意区分，
  从差异化机会点里挑竞品未占住的切入点」。无竞品分析时行为不变（有测试守）。
- **演示模式口吻随品类切换**：FakeLLM 的文案按主题归类（数码 → 硬核避坑参数党 /
  生活 → 情绪种草 / 默认 → 理性反焦虑）切换口吻模板，换主题能明显感受到口吻差异；
  依旧遵守「不编造参数」原则，只给判断框架。
- **真实模式（配 DeepSeek Key 后）**：竞品分析先抓真实竞品笔记（evidence），
  LLM 基于真实数据提炼差异化机会点，文案创作再基于这些结论刻意避开竞品套路 ——
  实测「磁轴键盘」任务产出的 3 个变体口吻各异（吐槽种草 / 实测参数 / 避坑评测），
  且都回应了竞品 gap（可调触发键程、手感不肉、反价格虚高、FPS+码字双场景）。

## 📄 文档

- [演示流程](docs/demo.md) — 端到端演示步骤与预期效果
