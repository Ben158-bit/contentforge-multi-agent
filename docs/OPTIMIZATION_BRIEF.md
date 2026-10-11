# ContentForge 现状说明书（优化启动用）

> 本文写给「要设计优化方案的专家」。
> 目标：让你**不必先读完全部源码**就能准确判断哪里该改、哪里不能动。
> 所有结论都在当前副本 `contentforge-v2` 上核对过（2026-10-10）。
>
> ⚠️ 同目录的 `OPTIMIZATION_ROADMAP.md` 写于更早版本，**流水线写的是 6 阶段（缺 humanize 与 guardrail）**，
> 其「可扩展性 / 生产部署 / GitHub 展示」视角也与本轮目标不符。**以本文为准。**

---

## 1. 一句话定位

基于 **LangGraph** 的多 Agent 营销内容生产系统：把一句营销需求，跑成可交付的多渠道文案。
核心卖点是 **「能枚举的规则用确定性代码保证 100% 复现，只把需要判断力的部分留给模型」**。

技术栈：Python 3.10 + FastAPI + LangGraph **0.6.11（固定，严禁升 1.x）** + DeepSeek + SQLite + React 18/Vite/TS。

---

## 2. 怎么跑起来（演示模式，无需 API Key）

```bash
# 后端（必须在 backend/ 下启动：DATA_DIR=./data 依赖 cwd）
cd backend && NO_PROXY=127.0.0.1,localhost ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（/api 已代理到 8000）
cd frontend && npx vite --host 127.0.0.1 --port 5173
```

- `.env` 已设 `FAKE_LLM=true`（FakeLLM 覆盖全部 Agent 角色，含审校打回与去 AI 味改写）、`API_TOKEN` 留空（本地免鉴权）。
- 健康检查 `GET /api/health` → `{"status":"ok","graph_ready":true,"db_ok":true}`。
- 改后端代码需**重启**（未开 `--reload`）。

---

## 3. 流水线现状（7 阶段）

`backend/app/agents/pipelines/marketing.yaml` 是**唯一真源**。

| # | 阶段 | kind | 调模型 | 职责 |
|---|---|---|---|---|
| 1 | `research` 市场调研 | agent | ✅ | 趋势 / 受众洞察 / 痛点 / 关键词（**按渠道检索口径**）；配了联网搜索则注入真实来源 |
| 2 | `competitor` 竞品分析 | agent | ✅ | 3-5 个竞品定位·卖点·内容策略 + 差异化机会点（**刻意渠道无关**） |
| 3 | `strategy` 策略制定 | agent | ✅ | 目标受众 / 核心价值主张 / 关键信息 / 语调 / 内容角度 + **该渠道原生内容骨架**（`content_structure`） |
| 4 | `copywriting` 文案创作 | agent | ✅ | 按渠道产出 **2-3 个变体**；审校打回时带 feedback 重写 |
| 5 | `humanize` 去 AI 味 | agent | 仅公众号 | 确定性预扫 → 模型改写 → 复扫 → 验收；不通过保留原文 |
| 6 | `guardrail` 质量守门 | **step** | ❌ **零 token** | 字数降档 / 标签规范化 / emoji 策略 / 违禁词 / AI 味扫描 / 标题体检 |
| 7 | `review` 审校优化 | agent | ✅ | 对照策略与渠道规范逐条检查，不通过给 feedback 并打回创作 |
| — | `human_confirm` | human_gate | ❌ | `interrupt` 暂停，等人工确认/编辑 |

**成本**：非公众号渠道 **5 次模型调用**；公众号 **6 次**（多一次 humanize）。
**审校回路**：`review → copywriting` 最多 `MAX_REVISION_ROUNDS=3` 轮。

---

## 4. ⭐ 内容质量现状（本轮第一优先级，请重点看）

### 4.1 质量由三个东西共同决定

```
提示词体系 (agents/prompts.py, 445 行)
   ├── 渠道方法论 (templates/channels.yaml 的 copywriting_guide)
   └── 品牌记忆 (memory.py：品牌档案 + 从人工修改中学到的偏好)
```

### 4.2 ✅ 曾经的缺口（切入点 B 已修复）：**前 3 个阶段完全与渠道无关**

> **本节描述的是改造前的状态，保留作为背景。** 渠道上下文（desc / tone / structure /
> max_chars / seo_keywords）现已注入 `research` 与 `strategy`，策略阶段产出渠道原生的
> `content_structure` 并由 `copywriting` 按序展开 —— 见 §13「切入点 B（已实施）」。

改造前 `research` / `competitor` / `strategy` 的提示词只把 `channel_id` 当**一个字符串**传进去，
**没有拿到该渠道的 `copywriting_guide`**。也就是说：

- 做小红书笔记和做电商详情页，**调研和策略阶段的产出是同一套逻辑**；
- 真正「像小红书 / 像公众号」的差异，**全部压在 copywriting 这一个节点上**。

这与「小红书、公众号分开细化完成」的目标是直接冲突的 —— 当时渠道差异只体现在最后一棒。
（`competitor` **刻意保持渠道无关**：竞品是跨渠道竞争的，只从单一渠道视角拆解会被带偏。）

### 4.3 ⚠️ 曾经的缺口（切入点 A 已修复）：**copywriting 只给了 2 个渠道专属输出契约**

> **本节描述的是改造前的状态，保留作为背景。** 现已为四个渠道全部声明 `content_contract`，
> 并由守门层逐项核验——见 §13「切入点 A（已实施）」。

改造前 `build_copywriting_messages()` 里有三分支（prompts.py:237-269）：

| 渠道 | 改造前输出 schema | 是否有专属结构化字段 |
|---|---|---|
| `product_page` | 富 schema：`selling_points`（FAB 四段式）+ `specifications` + `trust_badges` + `cta` | ✅ 有 |
| `weibo` | 富 schema：`hook_type` + `reasoning_chain` + `proof_used` + `risk_flags` | ✅ 有 |
| **其他（含 `xiaohongshu`、`wechat`）** | **通用 schema：`title` / `body` / `hashtags` / `notes`** | ❌ **没有** |

**这是最明显的一块缺口**：小红书在 `channels.yaml` 里的 `copywriting_guide` 其实写得很细
（标题 18-20 字公式、300-600 字四段式、2-3 大标签 + 3-5 长尾标签、70/20/10 内容配比、互动率/收藏率指标），
但**这些只作为 `- key: value` 文本行塞进 user prompt，没有任何结构化输出字段去强制模型逐项落地**。
公众号同理。结果就是：**规则写了，但没有任何机制检查它有没有被执行**。
→ 已由切入点 A 补上「声明式契约 + 确定性核验」，并做成**只标记、不改判、不补内容**的闭环。

> 曾成立的一致推论（切入点 C 已处理）：`review` 审校曾只拿到 `copywriting_guide` 作依据、**自由判断**，
> 没有对应「小红书标题公式用没用上」这类结构化核对项。
> 现由 `review_checklist` 逐项核对 —— 见 §13「切入点 C（已实施）」。注意其边界：它管「公式用没用对」
> 这类**判断力**项，「标题是不是 18-20 字」这类**确定性**口径仍只由守门层管（二者互补不重复）。

### 4.4 去 AI 味目前只覆盖公众号

`humanize` 的开关是渠道级的：`channel_id == wechat` 才生效，其他渠道 `skipped` 且零 token。
小红书同样是「AI 味重灾区」，但**当前完全不做处理**。

### 4.5 已知的提示词层薄弱点

- ✅ **已修复（切入点 A 顺带）**：`_marketing_controls()` 里的渠道名映射原只覆盖 `weibo` 与 `product_page`，
  **小红书 / 公众号会走到 `channel_id` 原样回显**（写的是 `xiaohongshu` 这种英文 id）。
  现改为从 `channels.yaml` 的 `name` 取显示名，任何新增渠道自动生效。
- 变体数量 2-3 是模型自由决定的，没有结构化约束。
- `research` 的 `keywords` 只有 5-8 个字符串，没有搜索量/竞争度等维度，而小红书这类平台**关键词是核心杠杆**。

---

## 5. 守门层与去 AI 味：边界（**不要越界**）

### 5.1 守门层 `backend/app/guardrails/`（纯函数，不调模型）

| 模块 | 职责 |
|---|---|
| `text_metrics.py` | **字数口径唯一真源** |
| `length_policy.py` | 五级字数降档 |
| `compliance.py` | 广告法违禁词 |
| `hashtags.py` | 话题标签规范化（`paired` = `#话题#` / `hash` = `#话题`） |
| `content_contract.py` | **渠道内容契约核验**（五种 kind）+ 分层标签 → `hashtags` 推导（切入点 A 新增） |
| `emoji_policy.py` | `remove` / `cap3` / `keep` |
| `ai_tells.py` | AI 味扫描（正则 + 结构指标） |
| `title_check.py` | 标题体检 |
| `postprocess.py` | 编排：归一化 → 检出 → 报告 |
| `report.py` | 报告 Pydantic 模型 |
| `rules/*.yaml` | 规则资产：`forbidden_words` / `ai_tells_zh` / `title_rules` |

**⛔ 分界线（务必保持）**：守门层只自动修「删信息以外、不改语义」的事
（emoji / 标签 / 字数降档）；**违禁词替换与 AI 味改写一律只标记，绝不静默改写**。

守门层跑完会把**归一化后的变体回写 `copywriting`**（下游必须看到实际被检查过的那一份），
报告进 `guardrail` 状态键 + 独立阶段行，`needs_human` 送到 `human_confirm` 的 interrupt payload。

### 5.2 去 AI 味 `nodes/humanize.py`

**混合闭环**：确定性预扫 → LLM 改写 → 确定性复扫 → `evaluate_rewrite()` 纯函数验收。

验收条件（全满足才采纳）：正文非空 + 命中总数**严格下降** + 不引入新 fatal + 长度不塌陷（≥原文 50%）；
**任一不满足即保留原文**。四种处置必须互相区分：

| 处置 | 含义 |
|---|---|
| `accepted` | 已采纳改写 |
| `rejected` | 有结果但没过验收 |
| `skipped` | 无需改写或超预算 |
| `failed` | **没有得到可用结果**（异常 / 非 JSON / 空正文） |

只有 fatal/warning 级命中才触发改写（info 级不值得一次调用）；`MAX_REWRITES_PER_RUN = 3`。

---

## 6. 渠道体系（`backend/app/templates/channels.yaml`）

四个渠道：`xiaohongshu` 小红书 / `wechat` 微信公众号 / `weibo` 微博 / `product_page` 电商产品页。

**⚠️ 字段分三类，别混用**：

| 类别 | 字段 | 消费者 |
|---|---|---|
| 风格提示 | `tone` / `require_emoji` / `require_hashtags` / `copywriting_guide` | **模型**（整份配置会被渲染进创作提示词） |
| 守门参数 | `emoji_policy` / `hashtag_style` / `hashtag_min` / `hashtag_max` / `title_min_chars` / `title_max_chars` / `char_count_mode` | **守门层**（确定性代码） |
| 内容契约 | `content_contract`（`field` / `kind` / `label` / `min` / `max` / `values` / `tiers` / `item_fields` / `feeds`） | **模型**（照字段产出）+ **守门层**（逐项核验）——双消费者，所以必须两边同步 |

当前取值（P5 起全部显式声明）：

| 渠道 | emoji | hashtag_style | 标签上限 | 标题区间 | 正文上限 |
|---|---|---|---|---|---|
| 小红书 | cap3 | `hash`（单井号） | 8 | **18-20 字** | 1200 |
| 微博 | cap3 | `paired`（`#话题#`） | 4 | 不判定 | 280 |
| 公众号 | remove | none | — | **0-64 字**（平台限制） | 3000 |
| 产品页 | remove | none | — | 不判定 | 1500 |

⚠️ `hashtag_min` 的语义是「**该渠道必须有标签**」而不是「必须有多少个」——
`normalize_tags` 数量不足时**只兜底补 1 个**。所以代码层取值 `1`，
「5-8 个」「2-4 个」这类风格数量必须留在提示词里，写进 `hashtag_min` 会造成**声明值与实现不符**。

---

## 7. 架构分层与「改一处要动哪里」

```
pipelines/*.yaml + pipeline_definition.py   ← 纯数据（绝不 import 节点实现）
        ↓
graph.py                                    ← 唯一组合根（解析 factory/router 名）
        ↓
nodes/                                      ← 单向依赖定义
        ↓
runner.py / api/routes.py                   ← 按「图所挂定义」取阶段集合与可编辑性
```

`nodes/` 是**包**：`_common.py`（共用 LLM 管道 + **成本公式唯一真源**）+ `__init__.py`（5 个 LLM 阶段 + `human_confirm`）+ `guardrail.py` + `humanize.py`。

**⛔ 三条硬约束**：

1. **阶段不能改名**：`key == state_key` 由校验器把关。理由不是代码硬编码，而是
   **提示词构造器按状态通道名读上下文**（如 `build_competitor_messages` 写死 `state.get("research")`）——
   放宽它只会让「改个名字后静默收不到上下文」变成合法配置。**换展示名请用 `label`**。
2. **可编辑判据是「产出即交付物」，不是「是不是 Agent」**：`NodeSpec.editable: bool | None`。
   humanize 是 Agent 但产出是处置报告、真正改的是 `copywriting` → `editable: false`。
   `editable: true` 出现在非 Agent 阶段上会在**加载期报错**。
3. **`nodes/` 子模块绝不能 import `__init__`**（包内循环，ImportError 只在特定导入顺序下出现，极难排查）。
   共用管道一律放 `_common.py`。

**改动阶段集合时必须同步**：`pipelines/marketing.yaml`（version / 节点数 / 边数）；
前端 `Pipeline.tsx` 的 `STAGES` 与 `EDITABLE_STAGES`、`types.ts` 的 `StageKey`；
硬断言测试 `test_api` / `test_graph` / `test_demo_mode` / `test_guardrail_node` / `test_pipeline_definition` / `Pipeline.test.tsx`。

**新增一条流水线 = 新增一个 YAML**（这是已验证的扩展能力，`seo_rewrite` 就是第二条流水线的雏形）。

---

## 8. 前端现状

React 18 + Vite + TS，无 UI 框架、无组件库，`styles.css` 单文件 492 行手写样式。

| 组件 | 行数 | 职责 |
|---|---|---|
| `App.tsx` | 60 | 外壳 / 视图切换 |
| `TaskList.tsx` | — | 任务列表、创建、删除 |
| `TaskDetail.tsx` | 463 | 详情：阶段进度、文案编辑、确认、重跑、守门报告展示 |
| `Pipeline.tsx` | 141 | 流水线可视化（`STAGES` / `EDITABLE_STAGES` 硬编码在此） |
| `BrandManager.tsx` | — | 品牌档案 CRUD |
| `FeedbackPanel.tsx` | — | 反馈学习 |
| `ConfirmButton.tsx` | — | 应用内行内确认条（**替代原生 `confirm`**） |

已知体验层面的既有短板：

- **无可视化设计体系**：颜色 / 间距 / 圆角全是散落的字面量，没有 token 层。
  → **已解决**（方向 2，见 §14）：全部收敛进 `:root` token，组件与 CSS 禁止硬编码色值。
- **无深色模式**。→ **已解决**（方向 2，见 §14）：`data-theme` 切换 + localStorage 持久化 + 跟随系统 + 防 FOUC。
- **守门报告只做了基础展示**：结构化报告（逐变体字数、违禁词命中、AI 味 pattern、标题问题）数据齐全，但呈现形式偏「贴 JSON」，**没有把「哪里有问题、建议怎么改」做成可操作的 UI**。
- **文案编辑是纯 textarea**，没有「标题字数实时提示」「标签预览」「渠道规范对照」等辅助。
  → **已部分解决**（方向 2，见 §14）：标题 / 正文字数实时提示、标签可发布形态预览已上线；
  「渠道规范对照」仍待做。
- **阶段编辑回填的下游影响**不直观（编辑 research 会重跑下游，但界面上看不出会影响哪些阶段）。
  → **已解决**（方向 2，见 §14）：编辑中提示将重跑的下游、编辑后下游节点标「待重算」。
- **无移动端适配**。

---

## 9. 数据与持久化

- **两个库**：`contentforge.db`（业务：`tasks` / `stages` / `artifacts` / `content_feedback` / 品牌表）
  + `checkpoints.db`（LangGraph `checkpoints` / `writes`）。
- 迁移版本 **v1–v4**（v4 新增 `tasks.error`，失败原因落库并在界面展示）。
- **⚠️ 并发写库必须持有 WAL 保活锚点**：`app/db_anchor.py` 持一个**永不关闭**的连接。
  少了它，`db_sql`「每条语句开/关一次连接」会在 WAL 下与其它线程竞争旁路文件——
  **最后一个连接关闭时 SQLite 会 checkpoint 并删掉 `-wal`/`-shm`**，新连接重建失败即
  `attempt to write a readonly database` → 流水线跑到一半整任务失败（成本 0、阶段全停在「等待」）。
  **接入点：`db_sql.conn()` 与 `db.get_conn()` 都先 `hold()`；`main.py` 关停时 `release_all()`。
  新增任何 DB 连接路径都必须走这两处。**
- **⚠️ 锚点必须「真读一次库」否则空转**：只跑 `PRAGMA journal_mode=WAL` 的锚点**不持有 `-shm`**，
  毫无保护作用。必须加真实查询（`SELECT count(*) FROM sqlite_master`）。
- **⚠️ 「置 failed」不能裸写**：`runner._mark_failed()` **自身绝不抛异常**。
  看门狗只看 `running`（`pending` 是进程内等 slot 的合法排队态；
  真正的 `pending` 孤儿只在进程重启时出现，由 `recover_running_tasks` 处理）。

---

## 10. ⛔ 不可破坏的约定（改任何东西前先看这里）

1. **单一真源**：字数口径只有 `text_metrics`；成稿范围只有 `postprocess.draft_text()`
   （= 标题 + 正文 + 标签，**不含 `notes`**）；渠道参数只有 `channels.yaml`；
   成本公式只有 `nodes/_common.py`；规则资产只有 `guardrails/rules/*.yaml`；
   humanize 的提示词必须由 `render_patterns_for_prompt()` 从 `ai_tells_zh.yaml` 渲染；阶段列表只能来自流水线定义。
2. **守门层只修「非语义」的事**，违禁词与 AI 味只标记不改写。
3. **`needs_human` 只由守门层写**（它用 `list(...)` 整体替换，以便审校回路重跑时刷新而非累积）。
4. **⛔ 禁止原生 `confirm()` / `alert()` / `prompt()`**：破坏性操作一律用 `ConfirmButton.tsx`。
   原生模态框在嵌入式宿主里会**阻塞渲染进程却不显示**，用户只看到「点击后毫无反应」，
   网络面板干净、控制台无报错，完全无从排查（本项目真实踩过）。防回归测试：`src/no-native-dialogs.test.ts`。
5. **演示模式 FakeLLM 按 system 角色关键字路由**：**新增 Agent 节点必须补分支**，
   否则兜底返回 `{"error": ...}` 且该阶段**静默降级**（humanize 曾因此永远 `failed`）。
   契约测试 `test_fake_llm_answers_every_declared_stage_role` 守着这一点。
6. **禁用态必须有可见差异**（`styles.css` 曾只有 `.btn.primary:disabled`，
   ghost 按钮禁用后与可用时外观逐项完全相同 —— 又一个「看起来能点、点了没反应」的静默源）。
7. **内容契约只标记，不改判，不补内容**：契约缺口进 `needs_human`，但**绝不改变 `passed`**
   （`passed` 只表达硬合规：字数 / 违禁词 / 致命 AI 味）；核验者不生成缺失的 CTA、不替模型编卖点；
   **未声明 `content_contract` 的渠道完全不核验**（不做隐式推断）。
   契约的单一真源是 `channels.yaml`，提示词与核验器都必须从它派生
   （`tests/test_content_contract.py` 双向钉死，任一处漂移即失败）。
8. **分层标签（`feeds: hashtags`）是标签唯一来源**，且推导必须发生在**创作节点出口**
   ——`runner._write_chunk` 按**节点名**写阶段产出，守门节点回写状态通道**不会**更新
   `copywriting` 那行阶段记录，而前端编辑器读的正是它。
9. **渠道上下文只有一个来源**：`prompts._channel_brief()` / `fake_llm._demo_structure()`
   都从 `channels.yaml` **派生**，禁止在提示词或演示里另写一份渠道结构。
   同理 `content_structure` 的兜底也是 `channels.yaml` 的 `structure`。
   `competitor` **刻意不发渠道上下文**（竞品跨渠道竞争），不要「顺手统一」。
10. **审校清单与确定性层互补不重复**：`review_checklist`（判断力：公式/风格/配比）
   绝不能重新核验守门层/契约层已确定性判定的口径（字数、标签数量）。
   三重真源 `channels.yaml` ↔ `build_review_messages` ↔ `test_review_checklist.py` 双向钉死。

---

## 11. 本轮优化方向（已与项目负责人确认）

| 优先级 | 方向 | 具体诉求 |
|---|---|---|
| **1** | **内容质量，按渠道分开细化** | 小红书、公众号等**各自独立深化**，而不是共用一套通用逻辑 |
| 2 | **前端交互与视觉** | 交互流程、可读性、视觉呈现 |
| 3 | **架构** | 「也能有优化更好」——非强制，锦上添花 |

**进展**：优先级 1（内容质量）已通过切入点 A / B / C 全部落地（见 §13）。
优先级 2（前端）已落地五项：编辑器实时字数辅助、设计 token 层 + 深色模式 + 去头部 AI 渐变、
守门报告可操作化、阶段编辑下游影响可视化、标签可发布形态预览（见 §14）；
后续还有渠道规范对照 / 移动端适配。

---

## 12. 怎么验证你的改动（务必用真实数字，别只看「跑通了」）

```bash
# 后端全量（必须用项目自带 .venv；--basetemp 必须落在仓库内；必须设 NO_PROXY）
cd backend && NO_PROXY=127.0.0.1,localhost ../.venv/Scripts/python.exe \
  -m pytest -q --basetemp=../.pytest_tmp/run        # 545 项

# 评测套件（注意：evals/ 在仓库根，不在 backend 下）
cd .. && NO_PROXY=127.0.0.1,localhost ./.venv/Scripts/python.exe -m pytest evals -q   # 32 项

# 前端
cd frontend && npx tsc -b && npx vitest run      # 79 项 / 7 文件
```

**当前基线（改动前请先跑一遍确认拿到同样数字）**：
后端 545 passed / 0 failed（全量约 32s）；`evals/` 32 passed；前端 79 passed。

**评测与生产用同一把尺子**：`evals/metrics.py` **不自带渠道上限表**，
而是复用生产的 `count_variant` / `mode_from_channel` / `get_channel` / `draft_text`。
所以改动渠道参数或字数口径后，**评测套件会自动跟着变**——这正是期望行为。

> ⚠️ **方法论提醒**：`evals/` 里的样本是 **FakeLLM 产生的假文案**（body 只 40 余字），
> 对提示词类改动**天然不敏感**。想让评测真正衡量内容质量提升，必须补真实模型基线
> 或把样本换成人工标注的长文案。**「基线通过」不能当作质量提升的证据。**

---

## 13. 给专家的三个建议切入点

> **状态更新**：切入点 A、B、C **已全部实施并验证通过**（见下方 ✅ 段落）。

### ✅ 切入点 A（已实施）：把「渠道专属输出契约」补齐到小红书 / 公众号

**原建议**：只有 `product_page` 与 `weibo` 有专属 schema，小红书和公众号掉进通用分支（见 §4.3）。
为四个渠道各定义一套结构化输出字段，让 `channels.yaml` 的需求从「提示词里的一句话」
升级为「**有字段、可被守门层核验**」。

**实际落地**（把「加字段」做成了「闭环」）：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| 契约声明 | `templates/channels.yaml` 各渠道 `content_contract` | 五种 kind：`text` / `list` / `enum` / `tiered` / `headings` |
| 契约核验 | `guardrails/content_contract.py`（新文件，纯函数、零 token） | 逐项核验，产出 `ContractCheck` 列表 |
| 契约进报告 | `guardrails/report.py`（`VariantReport.contract` / `GuardrailReport.contract_gaps`） | 缺口同时进 `needs_human` 与统计 |
| 编排接入 | `guardrails/postprocess.py` | 每条变体核验一次；**不参与 `passed`** |
| 告知模型 | `agents/prompts.py`（小红书 / 公众号专属分支） | 与契约逐字段对应 |
| 标签单一来源 | `agents/nodes/__init__.py` 创作节点出口 | `tags_by_tier` → 可发布形态 `hashtags` |
| 前端呈现 | `TaskDetail.tsx` 变体契约区块 + 守门报告缺口计数 | 逐项 ✓ / 缺口标红 |
| 防漂移 | `tests/test_content_contract.py`（42 项） | 钉死「声明 ↔ 提示词 ↔ 核验」三重真源 |

**三条边界**（有测试守着，后人不要越界）：

1. 契约缺口只**标记**（进 `needs_human`），**绝不改变 `passed`**。
2. 核验者**绝不补内容**——不生成缺失的 CTA、不替模型编卖点。
3. **未声明契约的渠道完全不核验**，不做隐式推断。

**一个真实集成坑（务必记住）**：`runner._write_chunk` 按**节点名**写阶段产出。
守门节点回写 `copywriting` **状态通道**并不会更新 `copywriting` 那行**阶段记录**。
所以标签推导必须放在**创作节点出口**——否则「守门层核验通过、界面上标签却是空白」。

**验证基线**：后端 511 项 + 评测 32 项 + 前端 45 项全过；真实端到端跑通
（小红书演示任务：契约缺口 0、六项核验全 OK、`hashtags` 为带 `#` 的可发布形态）。

### ✅ 切入点 B（已实施）：把渠道上下文前移到 research / strategy

**原建议**：前 3 个阶段完全渠道无关（见 §4.2）。让策略阶段产出**渠道专属的内容结构**
（小红书要「前 3 秒钩子 + 场景 + 体验」；公众号要「观点 + 证据 + 收束」），
而不是一套通用策略让 copywriting 硬拧。

**实际落地**（核心是「真源不新增第二份渠道描述」）：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| 渠道上下文渲染 | `prompts._channel_brief()` | 从 `channels.yaml` 取 desc / tone / **structure** / max_chars / seo_keywords；**只挑选、不改写** |
| 注入 research | `build_research_messages` | 渠道块进提示词；`keywords` 口径绑定渠道检索习惯 |
| 注入 strategy | `build_strategy_messages` | 渠道块进提示词；输出新增 **`content_structure`**（渠道结构适配本任务后的有序骨架） |
| 骨架消费 | `build_copywriting_messages`（`_ordered_lines`） | 正文必须按 `content_structure` 顺序展开；策略没给时退回渠道标准 `structure` |
| 骨架核对 | `build_review_messages` | 把「正文是否按骨架展开」列为 checklist 项 |
| 前端可见 | `types.ts` 的 `StrategyOutput` + `TaskDetail.renderStrategy` | 骨架以有序列表展示 —— 看不见骨架就没法判断文案是否照计划写 |
| 演示同步 | `fake_llm._demo_structure` / `_demo_keywords` | **从 `channels.yaml` 现取**，改配置演示跟着变 |
| 防漂移 | `tests/test_channel_context.py`（24 项） | 渠道上下文真到达上游 + 真源派生 + 骨架真被消费 + 不推断未知渠道 |

**刻意不做的一件事**：`competitor`（竞品分析）**保持渠道无关**。竞品是跨渠道竞争的，
只从单一渠道视角拆解会被带偏；测试 `test_competitor_stage_is_deliberately_channel_agnostic`
专门写着「不要顺手统一」。

**`_ordered_lines` 的一个细节**：渠道 `structure` 是「A → B → C」箭头串，要拆成步骤；
但**数组里的一条**本身可能就是「痛点共鸣 → 引出产品」这样的完整一步，拆开反而把一步拆成两步。
所以只有**单字符串**才按箭头拆分（有测试守着）。

**验证基线**：后端 511 项 + 评测 32 项 + 前端 45 项全过；真实端到端跑两个任务——
小红书 `research.keywords` = 推荐/测评/怎么选/真实体验/避坑，公众号 = 行业观察/选购指南/
深度解析/行业趋势/方法论；两边 `strategy.content_structure` 各自 5 步且来自本渠道 `structure`。

### ✅ 切入点 C（已实施）：把「渠道规范符合度」纳入 review 的结构化核对项

**原建议**：审校虽是自由判断，但已拿到渠道 guide + 策略骨架 + 守门摘要作依据
（切入点 B 起 review 还多一条「正文是否按 `content_structure` 展开」的 checklist 要求）。
建议进一步产出**可核验的 checklist**，与守门层的确定性参数**互补而不重复**
（守门层管「字数是否超标」，review 管「公式是否用对」）。

**实际落地**（关键分寸：`review_checklist` 只放「判断力」项，绝不重复确定性层）：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| 清单声明 | `channels.yaml` 每渠道 `review_checklist` | 四渠道各 3-4 项判断力项（公式 / 风格 / 配比） |
| 渲染 | `build_review_messages` | 「必须逐项核对、每项都写进 checklist 输出」的硬要求 |
| 演示同步 | `fake_llm._demo_review_checklist` / `_dynamic_review` | 从 `channels.yaml` 现取，改配置跟着变 |
| 前端可见 | `types.ts` 的 `ReviewOutput` + `TaskDetail.renderReview` | checklist 逐项 ✓/✕ + 判断依据，取代原来截断到 60 字符的通用 JSON |
| 防漂移/防重复 | `tests/test_review_checklist.py`（6 项） | 清单真渲染 + 与确定性口径不重叠 + 未声明退回通用审校 |

**与 content_contract 的边界**（`channels.yaml` 头部注释 + 测试双重钉死）：
`content_contract` 管「**有没有**」（字段缺不缺、枚举合不合法、标题是否出现在正文）→ 确定性代码核验；
`review_checklist` 管「**好不好 / 对不对**」（公式用没用对、风格像不像、配比合不合理）→ 审校 Agent 判断。
因此清单里**绝不允许**出现「标题 18-20 字」「2-3 大标签 + 3-5 长尾」这类确定性已覆盖的项——
那是重复核验，两套口径必然打架。

**验证基线**：后端 520 项 + 评测 32 项 + 前端 59 项全过；真实端到端跑两个任务——
小红书 / 公众号的 `review.checklist` 都逐项带上了各自渠道的 4 项清单，且互不相同。

> **不要做的事**：不要新增 skill loader / 外部依赖；不要破坏 §10 的单一真源与分层约束；
> 不要把可枚举的规则塞进提示词（那正是本项目刻意避免的设计）。

---

## 14. 方向 2：前端交互与视觉（进行中）

### ✅ 编辑器实时字数辅助（已实施）

**要解决的问题**（§8 短板之一）：文案变体编辑器是纯 input / textarea，**没有任何实时反馈**——
标题写多少字、正文超没超上限，要等守门层跑完才知道，然后被打回再改。后端确定性数据
（字数口径、达标区间）没有反馈到编辑侧，形成「写了规则、核验了规则、但编辑时看不到规则」的断点。

**核心洞察**：这是「单一真源没传到位」——标题达标区间本来就在 `channels.yaml`
（`title_min_chars` / `title_max_chars`），`check_title()` 收到了却没回写进报告，前端拿不到。

**落地**：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| 区间回写 | `title_check.TitleCheck` + `report.TitleCheckModel` 加 `min_chars`/`max_chars` | 前端据此提示，**不硬编码渠道区间表** |
| 实时提示 | `TaskDetail` 变体编辑器 | 标题「N 字 · 达标 18–20 字」（越界琥珀色）、正文「N / max_chars 字」（超限红） |
| 口径对齐 | `charCount` 用 `Array.from(s).length` | 精确对齐后端 `raw`（`len(text)`），emoji 等代理对不算 2 |
| 测试 | `test_guardrails_title.py`（+2）+ `TaskDetail.test.tsx`（+5） | 区间回写 + 越界标红 + 未配区间不显示 + 编辑实时更新 |

**边界**：渠道未配置标题区间（微博 / 产品页，min==max==0）时不显示区间提示；
正文上限 `max_chars` 随报告返回。计数是**编辑辅助的近似**，最终判定仍以守门层为准。

**验证基线**：后端 520 项 + 评测 32 项 + 前端 59 项全过；真实端到端——
小红书守门报告 `title` 带出 `min=18 / max=20`，正文上限 `1200`。

### ✅ 设计 token 层 + 深色模式 + 去头部 AI 渐变（已实施）

**要解决的问题**（§8 短板之二、之三）：颜色 / 圆角 / 阴影全是散落的字面量，只有一套亮色主题；
头部是紫蓝渐变——典型的「AI 模板味」。

**落地**：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| token 单一来源 | `styles.css` `:root` | 中性 / 品牌 / 状态 / 圆角 / 阴影全部收敛；业务样式**禁止硬编码色值** |
| 深色模式 | `html[data-theme="dark"]` | 只覆盖 token，业务样式零改动；`color-scheme` 让原生控件跟随 |
| 主题切换 | `src/useTheme.ts` + `App.tsx` | localStorage `cf-theme` 优先 → 跟随系统；未手动选过时跟随系统实时变化；图标内联 SVG（非 emoji） |
| 防 FOUC | `index.html` 内联脚本 | React 挂载前设好 `data-theme`，深色用户无首帧白屏闪烁 |
| 头部去渐变 | `.app-header` | 卡片色 + 细边框 + 品牌色 logo 方块，克制排版 |
| 测试 | `App.test.tsx`（+4）+ `test-setup.ts` matchMedia 桩 | 跟随系统 / 手动优先 / 切换翻转 + localStorage 持久化 |

**验证基线**：前端 56 项全过 + `vite build` 成功；真实浏览器（headless Chrome + CDP）
截图验证亮 / 深两套主题渲染与头部新排版。

### ✅ 守门报告可操作化（已实施）

**要解决的问题**（§8 短板）：守门报告把 `needs_human` 平铺成字符串——违禁词只显示词、
AI 味根本不显示，看不到「命中位置在哪、怎么改」。

**落地**：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| AI 味中文分类名 | `ai_tells.py` + `report.py` | `AiTellHit` / `AiTellHitModel` 带出 `category_label`（从规则资产 `categories` 节取中文），前端不硬编码映射 |
| 按变体分组的问题清单 | `TaskDetail.renderGuardrail` 重写 | 从 `variant_reports` 结构化重建（`needs_human` 每一项都能重建，不再平铺字符串）；每条带 severity 徽章 + 命中位置摘录（`context`/`samples`）+ 建议 + 「定位」按钮 |
| 定位到编辑器 | 编辑器变体卡 + 输入框 id | 点击「定位」`scrollIntoView` 平滑滚动到标题 / 正文并高亮（`.locate-flash` 动画） |
| 契约缺口去重 | 守门报告只留通过比例 | 缺口逐项详情在编辑器 `VariantContract`（紧挨字段），避免同一 detail 两处重复 |
| severity 归一化 | 前端 `severityLevel` | 违禁词 `high` 与 AI 味 `fatal` 统一为「致命」（红）/「警告」（琥珀）/「提示」（灰） |
| 测试 | 后端 +1（`category_label` 回写）、前端 +3（违禁词 / AI 味渲染、定位） | |

**验证基线**：后端 520 项 + 评测 32 项 + 前端 59 项全过；真实浏览器截图（headless Chrome + CDP）
确认可操作清单渲染——红色致命徽章 + 中文分类名「语言与句式」+ 命中片段 + 建议 + 定位按钮，
亮 / 深两套主题正常。

**顺带修复截图暴露的既有视觉 bug**：审校 checklist 的 note 在窄卡片里被 flex 挤成
**竖排字**（`.contract-list li` 横向布局 + `strong { min-width: 132px }` 不收缩，
长检查项把右侧说明挤到一字宽），已改为纵向堆叠（label 上、说明下），宽窄卡片都可读。

### ✅ 阶段编辑下游影响可视化（已实施）

**要解决的问题**（§8 短板）：编辑上游阶段（research / competitor / strategy）后，
下游各阶段仍显示「已完成」的旧产出——它们会在确认时被重跑（runner 的
`update_pipeline_state(..., as_node=最早编辑阶段)` 从该阶段之后重新执行），
但界面上看不出「这些结果已经作废了」。

**落地**（纯前端，零后端改动；下游关系唯一真源 = `Pipeline.tsx` 的 `STAGES` 顺序）：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| 下游推导 | `Pipeline.downstreamStageKeys` / `downstreamStageLabels` | 某阶段之后的所有 is_stage 节点；末端阶段（review）返回空 |
| 编辑中提示 | `TaskDetail` 编辑区 `.edit-impact` | 「保存后将重新执行下游：文案创作 → 去 AI 味 → 质量守门 → 审校优化」；无下游则明说「末端阶段」 |
| 编辑后标记 | `Pipeline` 组件 `stale` prop | 合并所有 `status==='edited'` 阶段的下游为待重算集合；节点琥珀虚线 + 阶段名下「待重算」（琥珀加粗） |

**验证基线**：前端 68 项（+9：Pipeline 下游推导 5 项 + TaskDetail 编辑提示 / 待重算 4 项）
全过、tsc 无错；真实浏览器截图（headless Chrome + CDP，编辑 research 后打开 strategy 编辑框）
确认亮 / 深两套主题下「待重算」标记与下游影响提示渲染正常。

### ✅ 标签可发布形态预览（已实施）

**要解决的问题**（§8 短板）：用户在编辑器里改标签时看不到「发布到平台上长什么样」——
小红书要 `#话题`、微博要 `#话题#`，写错了平台不生效，要等守门层打回才知道。

**落地**（风格值由后端带出，前端只做纯格式渲染）：

| 关注点 | 落地位置 | 说明 |
|---|---|---|
| 风格真源 | `GuardrailReport.hashtag_style` | `postprocess` 从 `rules_from_channel(channel)["style"]` 取**缺省推断后的最终值**（hash / paired / none），渠道映射只存在于 `channels.yaml` 一处 |
| 预览渲染 | `Pipeline.publishTags` | 对齐后端 `normalize_tag`：先剥已有 `#＃` 再按风格补——已规范化的标签进预览不会变双井号；`none` / 空返回空串 |
| 编辑器 UI | `TaskDetail` 变体卡 `.tag-preview` | 标签输入框下方实时预览；`none` 渠道明说「本渠道不使用话题标签」；输入为空显示「（未填写标签）」 |

**验证基线**：后端 523 项（+3：`test_report_carries_hashtag_style` 等 3 项，钉死「报告带出
推断后的风格值」）+ 前端 77 项（+9：`publishTags` 契约 5 项 + 编辑器预览 4 项）全过、tsc 无错；
真实浏览器截图（headless Chrome + CDP，demo 建小红书任务）确认「发布形态：#话题 …」紫色预览行
亮 / 深两套主题渲染正常，已规范化标签不出现双井号。

### 竞品分析真实数据源（红狐 Hub，已落地）

竞品分析此前是纯 LLM 编造（无真实竞品实例可核验）。已接入红狐 Hub
（`redfox.hk` 数据 API，`REDFOX_API_KEY` 为用户级环境变量）：

- 新模块 `agents/competitor_source.py`：渠道 → 红狐端点映射**单一真源**
  （小红书笔记搜索 / 公众号文章搜索 / 微博账号搜索——红狐无微博内容搜索）；
  urllib 直连零新依赖，两级网络回退（先直连再系统代理）。
- `make_competitor_node(llm, competitor_source=None)`：抓真实证据注入
  `build_competitor_messages`（要求基于真实数据拆解、不得编造竞品），
  证据同时附到输出的 `evidence` 字段；`build_graph` 新增同名参数装配。
- 前端竞品分析卡专门渲染：真实竞品实例列表（平台·标题可点链接 / 作者 /
  互动数据「赞9335 互动1.6w」/「查看原文 ↗」），无数据时明说「模型内部知识」。
- 优雅降级：无 key / 未知渠道 / 失败一律空列表退回模型内部知识，绝不中断流水线。
- **验证**：后端 538（524+14）+ 前端 79（77+2）全过；端到端建「磁轴键盘」
  小红书任务，evidence 8 条真实笔记（李加加/ATK/iFeng 等 + 赞藏评 + 链接）；
  headless Chrome 真实截图亮/深两套渲染正常。

### 竞品分析指导文案口吻差异化（已落地）

**要解决的问题**：文案创作阶段**根本没读竞品分析结果** —— 竞品结论只被
strategy 消费且被压缩成一句 `tone_guidance`，于是无论什么主题，文案口吻
都由「渠道固定语调 + 一句话策略」决定，天然同质化（用户实测：「无论几次
重新生成或者做别的营销内容，口吻基本没变化」）。demo 模式下 FakeLLM 更是
对所有主题用同一套句式模板。

**落地**：

- `prompts._competitor_brief_for_copywriting()`：把竞品定位/卖点/内容策略 +
  `gaps` 差异化机会点 + 真实竞品标题样本渲染成「竞品文风与差异化」块，
  注入 `build_copywriting_messages`（strategy 之后、structure 之前）；
  system 与 user 双侧要求「口吻/切入角度/句式与竞品刻意区分，从 gaps 里挑
  竞品未占住的切入点」。无竞品分析时渲染为空串，行为不变（有测试守）。
- demo 模式 `_topic_category()`：主题归类（数码 → 硬核避坑参数党 /
  生活 → 情绪种草 / 默认 → 理性反焦虑）切换 FakeLLM 口吻模板 ——
  换主题能明显感知口吻差异；依旧不编造参数，只给判断框架。
- **验证**：后端 541（538+3）全过；端到端建「磁轴键盘」（数码🎧）与
  「智能保温杯」（生活🌿）两任务，正文口吻完全不同（泼冷水避坑 vs 情绪种草）；
  真实浏览器截图渲染正常。

### 竞品关键词核心词提炼（真实模式启用，10-11）

**要解决的问题**：真实模式跑通后实测发现，竞品数据源用**完整营销主题**
（「磁轴键盘新品上市推广」）直接搜红狐会返回 0 条真实笔记——营销长句不是搜索词。

**落地**：`competitor_source._extract_core_keyword()` 按常见营销动作词/后缀截断出
核心品类词（「磁轴键盘新品上市推广」→「磁轴键盘」），`fetch` 按「核心词 → 原主题 →
品牌」多级回退，谁先抓到非空用谁。纯规则、不调模型，符合「确定性代码优先」。

**验证**：后端 545（541+4：核心词提取 2 + 多级回退 2）全过；真实模式端到端
「磁轴键盘新品上市推广」任务 evidence 抓到 8 条真实笔记，文案 3 个变体口吻各异
且回应竞品 gap（可调触发键程/手感不肉/反价格虚高/FPS+码字双场景）。

### 待做（按优先级）

1. **渠道规范对照**：把 review_checklist 与守门参数在编辑时以对照表呈现
2. **移动端适配**
