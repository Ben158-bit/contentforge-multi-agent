# 简历亮点总结

> 用于「AI 应用 / LLM 应用开发」岗位的作品集叙述。可直接改写进简历项目经历。

## 一句话定位

**ContentForge —— 基于 LangGraph 的多 Agent 营销内容生产工作台**：5 个角色化 Agent
（调研/竞品/策略/创作/审校）协作完成从市场分析到渠道文案的完整生产链路，全程
human-in-the-loop 人工确认，实时成本与进度可观测。

## 技术栈

`Python` · `LangGraph` · `FastAPI` · `SQLite` · `SSE` · `React + TypeScript + Vite` · `DeepSeek API` · `Docker`

## 简历条目（STAR 风格，可直接使用）

**多 Agent 营销内容生产工作台（个人项目）** | Python / LangGraph / FastAPI / React

- **背景**：通用 Agent 框架众多但缺垂直落地，营销内容生产链路长、质量不可控、成本不透明
- **行动**：
  - 基于 **LangGraph 图状态机** 编排 5 个 Agent（市场调研→竞品分析→策略制定→文案创作→审校优化），
    审校不通过自动**打回创作**并注入上一轮反馈重写（最大迭代轮数限制防死循环）
  - 实现 **human-in-the-loop**：每阶段产出可编辑确认，`interrupt` 机制在人工确认点暂停图、
    SQLite checkpoint 持久化支持任务断点恢复与重跑
  - 构建**实时可观测性**：SSE 推送阶段进度，按 DeepSeek 计价实时统计每次 LLM 调用的
    token 成本与耗时（任务级 + 聚合统计）
  - FastAPI REST + React 单页应用（流水线可视化、文案编辑、确认定稿）；内置 **FakeLLM 演示模式**
    无 API Key 即可体验完整流程；Docker 一键部署
- **结果**：26 项自动化测试全绿（图编排/仓储/API/SSE 集成）；端到端演示 1-2 分钟跑通完整链路

## 面试高频问题与回答要点

| 问题 | 回答要点 |
|---|---|
| 为什么用 LangGraph 而不是 CrewAI/AutoGen？ | 需要精确控制流程（审校循环、条件分支、人工中断点）与可调试性；图状态机 + checkpoint 最匹配生产需求；CrewAI 编排偏黑盒 |
| human-in-the-loop 怎么实现的？ | LangGraph `interrupt()` 在确认节点暂停图执行，前端编辑后 `Command(resume=...)` 恢复；SQLite checkpoint 保存断点，服务重启也能继续 |
| 多 Agent 之间如何协作/传递信息？ | 共享状态通道（TypedDict + reducer），每个节点只读写自己关注的字段；阶段间传递结构化 JSON（非自由文本），下游只依赖上游 schema |
| 如何保证输出质量？ | 审校 Agent 对照策略清单逐项检查（卖点覆盖/渠道适配/关键词），不通过打回重写并携带具体反馈；渠道模板参数化（字数/语调/结构/SEO） |
| 成本如何控制与统计？ | 每次调用记录 prompt/completion tokens，按模型计价折算；任务级累计 + /api/stats 聚合；审校迭代轮数上限防成本失控 |
| 为什么选 LangGraph 0.6.x？ | 实测 1.x 在 Python 3.10 下 async 执行路径 `interrupt` 存在上下文缺陷，0.6.x 同步路径稳定；团队选型时应做版本验证（体现工程严谨性） |
| 遇到的最大技术挑战？ | interrupt 在异步路径的 contextvar 丢失（`Called get_config outside of a runnable context`），通过组合实验定位到版本/执行路径问题，改为同步执行 + 线程池隔离，并把版本固定进 requirements 注释 |
| 如何扩展成更多 Agent？ | 加节点 + 状态字段 + 路由规则即可；渠道模板/提示词与代码解耦（YAML 配置），新增渠道零代码 |

## 技术深度的可展示点（加分项）

1. **审校-创作闭环**：反馈注入创作 prompt、轮数上限、checkpoint 中的迭代历史
2. **可观测性**：不是事后日志，而是实时 SSE + 结构化成本统计（面试中可主动展示）
3. **工程严谨性**：版本选型有实测依据（附注释）；26 项测试含真实 HTTP 服务器 SSE 集成测试
4. **演示友好**：FakeLLM 模式让面试官 1 分钟看到完整效果，无需配置 API Key

## 后续可扩展方向（面试中展示思考）

- 记忆层（Graphiti 风格知识图谱）让 Agent 跨任务积累品牌知识
- Agent 评测集：沉淀 20+ 用例做 prompt 回归测试（当前最大痛点）
- 更多渠道模板 + 定时任务编排（Cron + 多任务并发）
