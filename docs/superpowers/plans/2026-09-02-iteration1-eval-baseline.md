# ContentForge 迭代 1（验证阶段）实施计划

> 状态：设计已获用户确认，待执行
> 决策记录：`dec-a741d9691228b4dc`（迭代 1 为验证阶段，达标后进入条件式后续阶段）、`dec-e3aaaf35d8e7e64a`（FakeLLM 打通全链路 + DeepSeek 出正式基线）

## 背景

GPT5.6 给出的优化路线图（`docs/OPTIMIZATION_ROADMAP.md`）诊断准确，但原计划 4 里程碑 17 任务范围过大。经评估，采用裁剪方案：

- **迭代 1（本阶段）**：评测闭环 + CI 回归门槛 + README 首屏，先验证可行性
- **迭代 2（条件式后续）**：LLM/Search Provider 协议抽象 + 阶段版本化 schema
- **迭代 3（条件式后续）**：生产护栏 + 脱敏日志 + 优雅停机 + 持久化任务事件表（SQLite 版）
- **不实现（写入设计文档）**：Redis/PostgreSQL 多实例、Celery、OpenTelemetry、插件市场、真实模型消融、CODEOWNERS、release job

## 第一阶段完成门槛（用户定义）

只有全部满足，才进入迭代 2：

1. 30 条评测样本全部可重复运行
2. 规则评分器有测试
3. CI 能阻止明显回归
4. 至少 10 条样本有人工复核
5. README 新用户能在 5 分钟内启动 Demo
6. 没有因为评测代码破坏现有 77 项后端测试和 14 项前端测试

## 目录结构

```
evals/
├── dataset.jsonl            # 30 条虚构评测样本（提交）
├── schema.py                # EvalCase 定义 + 加载校验
├── metrics.py               # 确定性评分器（纯函数）
├── run_eval.py              # 评测 runner（fake / deepseek）
├── check_regression.py      # 回归门槛检查
├── thresholds.json          # 阈值（提交）
├── test_dataset.py          # 数据集测试
├── test_metrics.py          # 评分器测试
├── test_regression.py       # 门槛测试
└── results/
    ├── baseline-fake.json       # FakeLLM 基线（提交，可复现）
    └── baseline-deepseek.json   # DeepSeek 基线（提交，不含 key）
docs/
├── evaluation.md                # 评测方法 + 基线报告
└── evaluation-human-review.md   # 10 条人工复核表
.github/workflows/ci.yml         # 接入回归门槛
README.md                        # 首屏增强
```

## 1. EvalCase 字段（schema.py）

```
case_id / topic / brand_name / target_audience / channel_id /
extra_requirements / forbidden_terms / hard_constraints / reference_notes
```

校验规则：case_id 唯一、channel_id 存在（xiaohongshu/wechat/weibo/product_page）、topic 非空、内容不含 `sk-` 等疑似密钥。

## 2. 30 条样本分布（全部虚构品牌）

| 渠道 | 条数 | 覆盖点 |
|---|---|---|
| xiaohongshu | 9 | 禁用词、调性、长度 1200 |
| wechat | 9 | 长文结构、来源引用、CTA |
| weibo | 6 | 280 字硬约束、话题标签 |
| product_page | 6 | 卖点列表、品牌词、转化 CTA |

样本中约 1/3 带禁用词约束（如「第一」「国家级」），1/3 带品牌调性要求，1/3 带竞品场景，全部无真实客户数据。

## 3. 评分器（metrics.py，纯函数）

不依赖 FastAPI / LangGraph / 网络 / 数据库：

- `score_schema`：变体结构 / 标题 / 正文 / 标签可解析
- `score_constraints`：禁用词、渠道长度、硬约束
- `score_brand_alignment`：品牌名 / 调性关键词出现
- `aggregate`：总分 + 逐条机器可读失败原因

## 4. runner（run_eval.py）

```bash
python evals/run_eval.py --dataset evals/dataset.jsonl --provider fake|deepseek --output ... --seed 42
```

- 每条结果含：`input_hash / case_id / provider / latency / cost / metrics / error`
- 单条失败不中断批处理；错误分类（llm_error / schema_error / timeout）
- DeepSeek 模式读 `backend/.env` 的 key，key 永不入库

## 5. 回归门槛（CI 自动拦截）

初始阈值：

- schema 通过率 ≥ 0.98
- 硬约束满足率 ≥ 0.90
- 平均成本 ≤ 基线 150%
- 平均延迟 ≤ 基线 200%

CI 新增步骤：FakeLLM 跑 30 条 → `check_regression.py` 对比 `thresholds.json`，不通过即 CI 失败（CI 不碰 DeepSeek）。

## 6. 人工复核（用户参与点）

DeepSeek 正式基线跑完后，挑 10 条跨渠道样本生成 `docs/evaluation-human-review.md`：每条含输入、输出原文、机器评分、打分表（可用性 1-5 / 品牌符合 / CTA 是否到位）+ 备注栏。用户填写后结论回填到 `docs/evaluation.md`。

## 7. README 首屏改造（5 分钟 Demo 门槛）

- 一句话定位 + CI 徽章
- 三步快速启动（FakeLLM 零 key）：`uvicorn` → `npm run dev` → `bash scripts/e2e-smoke.sh`
- 「真实模型 vs 演示模式」差异表
- 「已知限制」区块（SQLite 单机、DeepSeek 默认 provider、未做多实例）
- 链接 `docs/evaluation.md`（评测证据）

## 8. 验收门槛映射

| 门槛 | 落地 |
|---|---|
| 30 条可重复运行 | runner 固定 seed + CI 每次跑 |
| 评分器有测试 | `test_metrics.py` |
| CI 阻止回归 | `check_regression.py` 接入 ci.yml |
| 10 条人工复核 | `evaluation-human-review.md`（用户填写） |
| README 5 分钟 Demo | 三步启动 + smoke 脚本 |
| 不破坏 77+14 测试 | 每个 commit 全量回归 |

## 执行顺序

1. dataset（schema.py + dataset.jsonl + test_dataset.py）
2. metrics（metrics.py + test_metrics.py）
3. runner FakeLLM 打通（run_eval.py + baseline-fake.json）
4. CI 回归门槛（thresholds.json + check_regression.py + test_regression.py + ci.yml）
5. DeepSeek 正式基线（baseline-deepseek.json）
6. 人工复核表（evaluation-human-review.md）+ 报告（evaluation.md）
7. README 首屏增强

每个任务独立 commit，任务完成后运行全量回归（后端 pytest + 前端 vitest + tsc/build）。

## 执行规则

- 工作目录：`D:\何泽斌\Documents\cursor项目\contentforge`
- 后端统一使用 `.venv\Scripts\python.exe`；本机 Uvicorn 测试前设置 `NO_PROXY=127.0.0.1,localhost`
- 真实 API Key、客户数据和生产数据库不得进入 Git；评测集只使用虚构品牌
- CI 只用 FakeLLM，不调用 DeepSeek 或外部搜索
