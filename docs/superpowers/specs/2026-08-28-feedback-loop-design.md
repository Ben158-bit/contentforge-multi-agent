# 效果闭环自学习（B）设计文档

> 日期：2026-08-28 ｜ 项目：ContentForge ｜ 状态：已批准，待实施
> 关联：`docs/demo.md`（记忆层）、`backend/app/memory.py`（纠错学习）、`backend/app/agents/`（5 节点图）

## 一、背景与目标

ContentForge 当前是**开环**系统：任务 → research→competitor→strategy→copywriting→review→human_confirm → 出内容 → 结束。唯一的「学习」依赖用户人工修改触发（difflib 提取规则），信号源窄、主观、低频，且**系统无法证明自己在进步**。

本设计将系统升级为**闭环**：内容产出后回填效果数据（真实或模拟）→ 效果分析 Agent 提炼「什么内容有效」的规律 → 下一次生成自动注入 → 效果可量化对比。这是营销内容 SaaS（Anyword / Phrasee 类）的核心优化引擎。

### 成功标准
1. 从「生成 → 回填 → 学习 → 注入 → 再生成」全链路可演示（含模拟数据一键闭环）
2. 能给出量化对比：学习前/后内容平均效果分变化（前端曲线）
3. 现有 69 后端测试保持全绿，新增测试 ≥ 8 个
4. 学习失败静默降级，不影响主生成流程

## 二、现状与差距（基线）

- 生成链：5 个串行节点，`AgentState` 显式状态，`brand_context` 注入（system+user 双注入）
- 记忆层：`brands` / `brand_preferences` 表，`source` 概念尚未引入；`learn_from_confirmation` 由人工修改触发
- 前端：React 工作台，任务详情页已有编辑/确认交互

## 三、架构总览

```
生成（现有 5 节点图，注入增强）──→ 产出内容（带 content_id）
        ▲                                  │
        │ ③ 规律注入（复用 brand_context）  ② 效果回填（手动 / 模拟）
   策略/创作节点 ◀── 效果分析 Agent（feedback.py 新增）
                          │ ① 程序化特征提取 + LLM 提炼
                          ▼
              brand_preferences（新增 source='feedback' + strength）
```

新增 3 个模块，不动现有 5 节点图结构：
1. `backend/app/feedback.py`：效果分析 Agent（特征提取 + 规律提炼 + 注入排序）
2. SQLite 迁移 v3：`content_feedback` 表 + `brand_preferences` 加字段
3. 前端：回填表单 / 模拟按钮 / 规律面板 / 效果对比曲线

## 四、数据模型（SQLite 版本化迁移 v3）

```sql
CREATE TABLE IF NOT EXISTS content_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL,          -- 关联任务产出（task_id）
    channel TEXT NOT NULL DEFAULT 'general',
    views INTEGER NOT NULL DEFAULT 0,
    conversions INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,        -- 0-5 综合效果分（可手动覆盖）
    is_simulated INTEGER NOT NULL DEFAULT 0,  -- 1=模拟数据（演示用，不污染真实统计）
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_content ON content_feedback(content_id);
```

`brand_preferences` 增加两列（迁移 v3 幂等）：
- `source TEXT NOT NULL DEFAULT 'manual'`（`manual`=纠错学习 / `feedback`=效果学习）
- `strength REAL NOT NULL DEFAULT 1.0`（-2~+2，正=有效规律，负=避坑规律；决定注入优先级）

`content_feedback` 与任务（tasks 表）通过 `content_id` 关联；同一 `content_id` 重复回填=更新不新增（幂等）。

## 五、效果分析 Agent（backend/app/feedback.py）

**核心原则（吸取记忆层 P1 教训）：程序化特征提取先行，LLM 只做规律归纳，禁止臆造。**

### 5.1 程序化特征提取（不依赖 LLM）
对产出内容做结构化特征提取：
- 标题：长度、含数字、含问句（`？`）、含情绪词、含 emoji、含「如何/为什么/盘点」
- 正文：段落数、要点数（`<li>`/bullet）、含数据引用、含来源（sources 字段非空）、语气标记（感叹号密度）

### 5.2 规律提炼（LLM 归纳，输入仅限真实数据）
- 输入：历史 `content_feedback` 中**高分内容与低分内容的特征统计对照表**（如「含数字标题的高分占比 80% vs 低分 30%」）+ 品牌上下文
- 输出：2-3 条规律，形如 `{text, strength, source:'feedback'}`；`strength` 由程序化计算（特征在高/低分组出现率差异的映射，-2~+2），LLM 不生成数值
- 失败降级：LLM 调用失败或输出非法 → 跳过本轮学习，不影响主流程

### 5.3 防规则爆炸
- 注入时按 `strength` 绝对值排序取 top-k（k=5）
- 同特征规律自动合并：新规律命中已有同特征规则 → 加权更新 `strength` 与 `text`，不新增行
- 前端可手动删除规律（防呆）

## 六、生成时注入（改动最小）

复用 `get_brand_context` 管道：在现有注入内容后追加 `source='feedback'` 的高 strength 规律段落（`system_section` 内新增小节「效果学习规律」）。无重复代码，策略/创作节点对注入内容透明。

## 七、前端（React 工作台）

1. **效果回填区**（任务详情页）：views / conversions / score 表单 + 保存（POST）
2. **一键模拟**：`POST /api/tasks/{id}/feedback/simulate`——基于种子随机生成 views/conversions/score，**且模拟数据与内容特征相关**（如含数字标题的内容 score 倾向更高），使演示时规律提炼可见
3. **规律面板**：「效果学习规律」列表（正/负、strength、来源），可删除
4. **效果对比曲线**：展示各任务 score 时间序列（学习前/后对比），面试演示用

## 八、API 设计

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tasks/{id}/feedback` | 手动回填效果（幂等 upsert） |
| POST | `/api/tasks/{id}/feedback/simulate` | 生成模拟效果数据（种子随机+特征相关） |
| GET | `/api/tasks/{id}/feedback` | 读取效果数据 |
| GET | `/api/brands/{id}/feedback-rules` | 效果学习规律列表（正/负+strength） |
| DELETE | `/api/brands/{id}/feedback-rules/{rule_id}` | 删除规律 |

鉴权：复用现有 Bearer Token / 限流中间件。

## 九、错误处理与测试

- 回填幂等；学习失败静默降级（对齐记忆层）；模拟数据标记 `is_simulated=1`，不进入真实统计/规律提炼基线（防演示数据污染）
- 测试计划（新增 ≥8）：
  - `test_feedback.py`：特征提取（固定内容断言特征集）、规律提炼（固定特征对照表断言输出规律与 strength）、注入排序（strength 排序 top-k）、同特征合并加权、回填 upsert 幂等、模拟数据特征相关性、API 鉴权、迁移 v3 幂等
- 现有 69 测试保持全绿

## 十、面试讲点

「生成→效果回填→规律学习→定向注入」数据闭环；程序化特征提取 + LLM 归纳的分工（防臆造）；strength 权重与规则收敛；与小说项目（DeterminFlow）的区别：它有跨章节状态记忆但**无效果反馈回路**，这是本系统的差异化亮点。

## 十一、商业化路线图（仅记录，本轮不实现）

以下能力是「变现」方向，**不在本次实施范围**，作为后续阶段的候选：

1. **内容表现预测评分**：生成时用历史反馈训练轻量评分模型/规则打分器，输出「预计效果分」——对应 Anyword 的预测评分卖点
2. **批量生产 + 效果报表 SaaS**：多任务批产 + 效果看板（订阅制收费点）
3. **真实渠道对接**：公众号/小红书发布 API 自动回流真实数据（需账号资质，接入后按效果收费/订阅）
4. **按效果计费**：企业按「有效内容数」付费——闭环机制的直接变现形态

**边界声明**：本轮实施**不包含**上述 4 项；设计预留了数据模型（`content_feedback.channel` 字段、`strength` 权重）以便未来平滑接入。

## 十二、范围边界（本轮不做）

- ❌ 变现能力（见第十一章，仅记录）
- ❌ 真实发布渠道 API 对接
- ❌ 改动现有 5 节点图的串行结构（并行写手群/仲裁为另一方向，另行评估）
- ❌ 真实 token 计量（内容效果与成本联动的分析，后续可加）
