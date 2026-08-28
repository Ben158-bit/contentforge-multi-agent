# ContentForge：记忆层 + 缺陷全面改进 设计文档

日期：2026-08-28
状态：已批准（用户 2026-08-28 确认）
范围：两个子项目 —— ① 记忆层（品牌档案 + 用户纠错学习）② 缺陷全面改进（P2/P3）

---

## 子项目 1：记忆层（品牌档案 + 纠错学习）

### 目标

让多 Agent 流水线拥有**跨任务记忆**：
- **显式记忆（B）**：用户配置的品牌档案（调性/卖点/受众/禁忌），所有任务自动注入
- **隐式记忆（A）**：用户在 human-in-the-loop 确认环节的文案编辑，经用户勾选后沉淀为品牌偏好规则，后续任务自动应用

### 数据模型（schema v1 → v2）

```sql
CREATE TABLE brands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,          -- 品牌名
    tone        TEXT NOT NULL DEFAULT '',      -- 品牌调性
    core_claims TEXT NOT NULL DEFAULT '',      -- 核心卖点/主张
    audience    TEXT NOT NULL DEFAULT '',      -- 目标受众
    taboos      TEXT NOT NULL DEFAULT '',      -- 禁忌词/表达
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE brand_preferences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id    INTEGER NOT NULL REFERENCES brands(id),
    rule_text   TEXT NOT NULL,                 -- 偏好规则（LLM 从编辑差异中提取）
    source_task INTEGER,                       -- 来源任务 id
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_pref_brand ON brand_preferences(brand_id);
```

迁移机制：`schema_migrations` 表已有；新增 `migrate_v2()`（幂等：检查当前版本 → 建两表 → 更新版本）。`init_db()` 改为按版本顺序执行迁移函数列表（v1 建表 + v2 品牌表），替换当前的 `SCHEMA_VERSION` 单一判断（修复"迁移机制名存实亡"缺陷）。

### 品牌档案 API（B）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/brands` | 创建品牌（name 唯一，409 冲突） |
| GET | `/api/brands` | 品牌列表（含偏好条数） |
| GET | `/api/brands/{id}` | 详情（含偏好规则列表） |
| PUT | `/api/brands/{id}` | 更新档案 |
| DELETE | `/api/brands/{id}` | 删除（级联删偏好） |

`TaskCreate` 增加可选 `brand_id`：任务与品牌关联（`tasks.brand_id` 列，可空）。

### 纠错学习流程（A）

触发条件（用户确认）：`ConfirmRequest` 增加 `learn: bool` 与 `brand_id: int`。
仅当 `learn=true` 且存在 `brand_id` 时执行，且**仅在用户提交的 variants 与 AI 原版有差异时**才学习（无差异不耗 LLM）：

```
confirm 请求（learn=true, brand_id=N）
  ├─ 读取 AI 原版 variants（checkpoint state["copywriting"]）
  ├─ 对比用户提交 variants：有差异？
  │     ├─ 否 → 跳过学习
  │     └─ 是 → LLM 提取规则
  │           prompt: 给出 AI 原版与用户定稿，要求输出
  │           {"rules": ["不超过2条的、可直接执行的品牌偏好"]}
  │           提取失败/超时 → 静默降级（记日志，不阻塞 confirm）
  ├─ 每条规则 INSERT brand_preferences
  └─ confirm 流程继续（后台化不变）
```

学习在 confirm 后台任务中执行（不阻塞请求）。

### 注入（记忆消费）

`copywriting` 节点（`prompts.build_copywriting_messages`）：
- 任务关联品牌 → 注入品牌档案（调性/核心主张/受众/禁忌）
- 注入该品牌最近 5 条偏好规则，格式：
  ```
  该品牌已沉淀的内容偏好（必须遵守）：
  - rule_text
  ```

无品牌/无偏好的任务：prompt 与现状完全一致（零影响，向后兼容）。

### 前端

1. **品牌管理页**（新路由/视图）：品牌列表 + 创建/编辑表单 + 删除；详情展示已学偏好规则
2. **任务表单**：品牌下拉（可选）
3. **确认区**：当用户编辑过文案时显示「把本次修改记入品牌偏好」勾选框 + 品牌下拉；确认后提交 learn/brand_id

### 测试

- repository：brands CRUD、preferences 关联/级联删除、迁移 v2 幂等
- 学习逻辑：有编辑+勾选 → 提取并入库；无编辑 → 不调用 LLM；提取失败 → 降级不阻塞；无 brand_id → 跳过
- 注入：关联品牌任务 prompt 含档案与偏好；无品牌任务 prompt 不变（现有测试零破坏）
- API：brands CRUD + 401/404/409、任务带 brand_id

---

## 子项目 2：缺陷全面改进（P2/P3）

### 后端

| 缺陷 | 改进 |
|---|---|
| 仓储层 async/sync 重复实现（repository vs runner） | 抽取单一 SQL 层 `app/db_sql.py`（同步 sqlite3 实现），repository 的 async 函数改为薄包装（`asyncio.to_thread` 或直接同步调用）；消除重复 SQL |
| 迁移机制名存实亡 | 见子项目 1：版本化迁移函数列表（v1、v2…），`init_db` 顺序执行 |
| `validate_runtime` 死代码 | lifespan 中调用（缺失 DeepSeek Key 且非演示模式时提前报错） |
| 无结构化日志 | logging JSON formatter（ts/level/logger/message/request_id），`request_id` 中间件（X-Request-ID）贯穿日志与错误响应头 |
| CORS 过松（`*` methods/headers + credentials） | 收紧为显式方法集（GET/POST/PUT/DELETE/OPTIONS）与允许头（Content-Type/Authorization/X-Request-ID） |
| 请求体无大小限制 | 纯 ASGI 中间件按 Content-Length 拒绝 >1MB 请求（413） |
| `_safe_json` 只解析 `{...}` 对象 | 兼容数组/裸字符串输出（按节点 schema 兜底） |
| `graph._saver_cm` 永不关闭 | lifespan 退出时调用 `__exit__` 关闭 SqliteSaver 连接 |
| 时间戳本地时区 | DB 存储改 UTC ISO8601（`strftime('%Y-%m-%dT%H:%M:%fZ','now')`），API 输出 UTC，前端本地化展示 |
| `/health` 只查 graph 对象 | 增加 DB 连通性检查（`SELECT 1`），返回 `{"status","graph_ready","db_ok"}` |

### 前端 / 工程化

| 缺陷 | 改进 |
|---|---|
| 前端零测试 | Vitest + React Testing Library：`parseVariants`/状态映射/品牌表单用例 |
| 可访问性缺失 | 任务表行键盘可聚焦（`tabIndex` + Enter 触发）、状态变化 `aria-live` |
| 缺 CI | GitHub Actions：`pytest`（后端）+ `tsc --noEmit` + `npm run build`（前端） |
| 缺 LICENSE | MIT |

---

## 验收标准（汇总）

1. 记忆层：品牌档案 CRUD 全通；勾选学习后规则入库并在下次任务注入；未勾选/无差异/无品牌时零影响；全部现有测试保持绿色
2. 缺陷改进：后端与前端测试全绿；`validate_runtime` 生效；日志含 request_id；CORS/请求体限制生效（有测试）；`_saver_cm` 关闭；时间戳 UTC；health 探活 DB；CI workflow 文件就绪（构建执行受 Docker/GitHub 环境限制，语法与本地等效命令验证）
3. 全量 `pytest tests/` + `tsc --noEmit` + `npm run build` 通过

## 实施顺序

1. 子项目 1（记忆层）：迁移 v2 → repository → 学习服务 → API → 注入 → 前端 → 测试
2. 子项目 2（缺陷改进）：后端逐项 → 前端/CI → 全量回归
