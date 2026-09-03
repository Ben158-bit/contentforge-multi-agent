# ContentForge 优化实施计划

> 本计划把优化路线图拆成可独立验收的任务。每个任务先写测试或验收脚本，再实现代码；完成一个任务后单独提交。

**目标：** 将 ContentForge 从可运行的营销内容 MVP 提升为具有可复现评测、低耦合扩展、可观测生产运行和专业 GitHub 展示的垂直应用平台。

**架构策略：** 保留现有 LangGraph + FastAPI + SQLite 单机路径，先补评测与稳定契约；再用 Provider 和 PipelineDefinition 隔离模型、搜索和节点编排；最后以持久化事件、可替换 dispatcher 和 Redis/PostgreSQL 扩展多实例。

**技术栈：** Python 3.10、FastAPI、LangGraph 0.6.11、Pydantic v2、SQLite/aiosqlite、React/Vite/TypeScript、pytest、Vitest、Docker、GitHub Actions、Redis。

---

## 执行规则

- 工作目录：D:\何泽斌\Documents\cursor项目\contentforge。
- 后端统一使用 .venv\Scripts\python.exe；本机 Uvicorn 测试前设置 NO_PROXY=127.0.0.1,localhost。
- 真实 API Key、客户数据和生产数据库不得进入 Git；评测集只使用虚构品牌。
- 每个 Task 完成后运行全量回归并单独提交。

~~~powershell
$env:NO_PROXY='127.0.0.1,localhost'
$env:no_proxy='127.0.0.1,localhost'
Set-Location 'D:\何泽斌\Documents\cursor项目\contentforge\backend'
..\.venv\Scripts\python.exe -m pytest -q
Set-Location '..\frontend'
npm test -- --run
npm run build
~~~

## 里程碑 0：基线和仓库保护

### Task 0.1：记录基线

**文件：** 创建 docs/evaluation-baseline.md。

- [ ] 运行后端 pytest、前端 Vitest 和生产构建。
- [ ] 记录测试数量、FakeLLM 单任务成本、延迟、schema 通过率、review 首次通过率和 commit SHA。
- [ ] 记录环境和 localhost 代理注意事项，不写未经测量的“生产就绪”结论。
- [ ] 执行 git diff --check，提交：git add docs/evaluation-baseline.md; git commit -m "docs: record evaluation baseline"。

### Task 0.2：仓库安全保护

**文件：** 创建 SECURITY.md、.github/ISSUE_TEMPLATE/bug_report.yml、.github/ISSUE_TEMPLATE/evaluation_report.yml、.github/pull_request_template.md；修改 .github/workflows/ci.yml。

- [ ] SECURITY 明确漏洞私下报告、禁止提交令牌、SQLite 仅支持单机模式。
- [ ] Issue/PR 模板要求复现步骤、环境、测试证据、迁移影响和 Prompt 评测影响。
- [ ] CI 用 git ls-files 检查 .env、数据库、.venv、node_modules 和 dist，命中即失败。
- [ ] 运行 CI 同等检查并提交：git add SECURITY.md .github; git commit -m "chore: add repository guardrails"。

## 里程碑 1：P0 评测与科学性

### Task 1.1：建立评测数据集

**文件：** 创建 evals/dataset.jsonl、evals/schema.py、evals/test_dataset.py。

- [ ] 定义 Pydantic EvalCase，字段为 case_id、topic、brand_name、target_audience、channel_id、extra_requirements、forbidden_terms、hard_constraints、reference_notes。
- [ ] 校验重复 ID、未知渠道、空主题和疑似密钥。
- [ ] 写入 30 条虚构样本，覆盖四渠道、禁用词、长度限制、品牌调性和竞品场景。
- [ ] 先运行 python -m pytest evals/test_dataset.py -q，确认非法样本失败、合法样本通过。
- [ ] 提交：git add evals; git commit -m "feat: add evaluation dataset"。

### Task 1.2：实现确定性评分器

**文件：** 创建 evals/metrics.py、evals/test_metrics.py。

- [ ] 先测试 schema、渠道长度、禁用词、必填字段、品牌词和聚合结果。
- [ ] 实现纯函数 score_schema、score_constraints、score_brand_alignment、aggregate，每个返回分数和机器可读失败原因。
- [ ] 评分器不得依赖 FastAPI、LangGraph、网络或数据库。
- [ ] 运行 python -m pytest evals/test_metrics.py backend/tests -q。
- [ ] 提交：git add evals; git commit -m "feat: add deterministic evaluation metrics"。

### Task 1.3：运行评测并生成报告

**文件：** 创建 evals/run_eval.py、evals/results/.gitkeep、docs/evaluation.md；修改 .gitignore。

- [ ] 测试 FakeLLM 无网络运行单条样本、每条样本都有结果、单条失败不终止批处理。
- [ ] 实现参数 --dataset、--provider fake、--output、--seed。
- [ ] 每条结果保存输入 hash、provider、Prompt 版本、延迟、成本、指标和错误分类。
- [ ] 运行 python evals/run_eval.py --dataset evals/dataset.jsonl --provider fake --output evals/results/baseline.json。
- [ ] 报告数据集组成、基线数值和 LLM-as-judge 局限。
- [ ] 提交：git add evals docs/evaluation.md .gitignore; git commit -m "feat: add reproducible evaluation runner"。

### Task 1.4：接入评测回归门槛

**文件：** 创建 evals/check_regression.py、evals/test_regression.py、evals/thresholds.json；修改 .github/workflows/ci.yml。

- [ ] 先测试阈值通过和失败路径。
- [ ] 初始阈值：schema >= 0.98、硬约束 >= 0.90、平均成本 <= 基线 150%、平均延迟 <= 基线 200%。
- [ ] CI 只用 FakeLLM，不调用 DeepSeek 或外部搜索。
- [ ] 运行 python -m pytest evals/test_regression.py -q 后提交：git commit -m "ci: enforce evaluation regression thresholds"。

### Task 1.5：完成消融实验

**文件：** 创建 evals/ablation.py、evals/test_ablation.py、docs/ablation-results.md。

- [ ] 支持 --disable-competitor、--disable-review、--disable-memory，默认行为不变。
- [ ] 用 FakeLLM call trace 测试仅禁用目标阶段，且下游输出仍符合 schema。
- [ ] 对无竞品、无审校、无记忆、完整流水线四组使用相同数据集、provider、seed 和 timeout。
- [ ] 报告质量、约束满足率、修改率、成本和延迟后提交：git commit -m "docs: publish ablation results"。

## 里程碑 2：P0 可扩展性

### Task 2.1：抽象 LLM Provider

**文件：** 创建 backend/app/providers/{__init__.py,protocol.py,deepseek.py,fake.py,failing.py}、backend/tests/test_providers.py；修改 backend/app/llm.py、fake_llm.py、config.py、agents/nodes.py。

- [ ] 先定义请求/响应类型，保留现有 LLMResult 字段。
- [ ] 测试 Fake 成功、Failing 超时/无效 JSON、DeepSeek 请求构造（mock 网络）和成本计算。
- [ ] 将 HTTP 逻辑移入 DeepSeekProvider；节点只依赖 LLMProvider。
- [ ] 保留 get_llm_client 兼容工厂，确保 build_graph(fake, ...) 不变。
- [ ] 运行 python -m pytest backend/tests/test_llm.py backend/tests/test_providers.py backend/tests/test_graph.py -q 后提交。

### Task 2.2：抽象 Search Provider

**文件：** 创建 backend/app/providers/search_protocol.py、bocha.py、noop_search.py、backend/tests/test_search_providers.py；修改 backend/app/tools/web_search.py、config.py、agents/nodes.py。

- [ ] 定义 SearchRequest、SearchResponse，包含 query、结果、来源和错误分类，不包含 API Key。
- [ ] 测试成功、超时、provider 错误、空结果和禁用搜索。
- [ ] 实现 Bocha 与 no-op 适配器，从图构建时注入。
- [ ] 运行后端全量测试并提交：git commit -m "refactor: isolate search providers"。

### Task 2.3：阶段输出版本化

**文件：** 创建 backend/app/contracts/{__init__.py,stages.py}、backend/tests/test_stage_contracts.py；修改 backend/app/agents/state.py、nodes.py、db.py、db_sql.py、repository.py。

- [ ] 定义 research、competitor、strategy、copywriting、review 模型，每个带 schema_version=1。
- [ ] 测试合法输出、malformed JSON、缺字段和旧数据转换。
- [ ] 通过现有迁移机制增加 pipeline_version、schema_version，迁移必须幂等且保留旧行。
- [ ] 在节点边界解析输出，失败返回可区分错误分类。
- [ ] 运行迁移、契约和全量测试后提交。

### Task 2.4：配置化 PipelineDefinition

**文件：** 创建 backend/app/agents/pipeline_definition.py、backend/app/agents/pipelines/marketing.yaml、backend/tests/test_pipeline_definition.py；修改 graph.py、runner.py、nodes.py。

- [ ] 定义 pipeline_id、version、ordered nodes、prompt key、输入/输出契约、human gate、重试次数和成功/失败目标。
- [ ] 测试营销流程六个逻辑阶段、显式 review-to-copywriting 回路、未知节点和缺契约失败。
- [ ] 第一阶段只迁移元数据，保留现有节点函数。
- [ ] 让 build_graph(pipeline_id="marketing") 读取定义，省略参数时保持默认行为。
- [ ] 增加测试专用 seo_rewrite 定义，证明新增定义无需修改 runner.py。
- [ ] 运行图测试和全量测试后提交。

## 里程碑 3：P1 生产部署

### Task 3.1：readiness、生产配置和脱敏日志

**文件：** 创建 backend/tests/test_readiness.py；修改 api/routes.py、config.py、logging_setup.py、main.py、tests/test_security.py。

- [ ] 测试 /api/health 是 liveness，新增 /api/readyz 仅在数据库可写且图就绪时返回 200。
- [ ] 增加 ENVIRONMENT=production；生产模式拒绝空 API_TOKEN、通配 CORS 和 FakeLLM。
- [ ] 测试 Authorization、API Key 和完整 Prompt 被替换为 [REDACTED]。
- [ ] 运行 readiness/security 测试并提交。

### Task 3.2：持久化任务事件

**文件：** 创建 backend/app/events.py、backend/tests/test_events.py；修改 db.py、db_sql.py、agents/runner.py、api/routes.py。

- [ ] 定义 event_id、task_id、序号、event_type、node_id、状态、payload 和时间戳，约束 (task_id, sequence) 唯一。
- [ ] 测试顺序插入、重复序号、任务隔离和模拟重启后读取。
- [ ] 增加幂等迁移与索引，旧任务无事件时仍可读取。
- [ ] runner 发出 task/stage started/completed/failed、waiting_human 和 task completed/failed 事件。
- [ ] SSE 按 last sequence 回放再轮询新行，保留当前事件名和心跳。
- [ ] 运行 API、SSE、事件和可靠性测试后提交。

### Task 3.3：可替换 dispatcher 和优雅停机

**文件：** 创建 backend/app/dispatcher.py、backend/tests/test_dispatcher.py；修改 api/routes.py、main.py、config.py。

- [ ] 定义 submit、submit_confirmation、submit_rerun 和 shutdown(timeout)。
- [ ] 测试并发上限、提交、停机取消和异常到 failed 状态转换。
- [ ] 用 InProcessDispatcher 封装现有 semaphore 与 runner，routes 不直接调用 asyncio.create_task。
- [ ] lifespan 停止接收新任务，等待有限时间，未完成任务标记为可恢复。
- [ ] 运行 dispatcher/API 测试后提交。

### Task 3.4：最小可观测性

**文件：** 创建 backend/app/observability.py、backend/tests/test_observability.py；修改 runner、routes、main、requirements、docker-compose。

- [ ] 指标只使用 pipeline_id、node_id、status、provider；禁止 topic、prompt、brand name 作为标签。
- [ ] 测试 telemetry 关闭时不改变任务行为，开启时记录耗时、token、成本、重试和成功/失败。
- [ ] 为 task/node/provider 创建 span，错误记录分类和重试次数。
- [ ] 暴露受保护的 /metrics 或内部 metrics 端口，并在 docs/architecture.md 记录指标。
- [ ] 运行全量测试后提交。

### Task 3.5：Docker、重启恢复和迁移回滚

**文件：** 创建 scripts/verify-docker.ps1、backend/tests/test_restart_recovery.py、docs/operations.md；修改 CI 和 compose。

- [ ] 测试 worker 中断、重启、只恢复一次和无重复 artifact。
- [ ] 脚本构建镜像、FakeLLM 启动 compose、轮询 /api/readyz、执行 smoke task 后清理；超时返回非零。
- [ ] CI 增加 Docker job，禁止真实 API Key。
- [ ] 文档写清备份/恢复、迁移顺序、日志、单机 RPO/RTO 和多实例依赖。
- [ ] 提交脚本、测试、文档和 CI。

## 里程碑 4：GitHub 展示和社区成熟度

### Task 4.1：README 首屏

**文件：** 创建 docs/images/ 的虚构 Demo 截图/GIF；修改 README.md、frontend/README.md、docs/demo.md。

- [ ] 用 FakeLLM 录制创建任务、审校打回、人工编辑、确认、反馈学习和统计。
- [ ] README 首屏放一句话定位、CI 徽章、截图/GIF、60 秒启动路径和测试证据。
- [ ] 明确 SQLite 单实例、DeepSeek 默认 provider、FakeLLM 差异和未完成的多实例能力。
- [ ] 校验链接和 git diff --check 后提交。

### Task 4.2：架构、安全、贡献和发布文档

**文件：** 创建 docs/architecture.md、CONTRIBUTING.md、CHANGELOG.md、.github/CODEOWNERS；修改 SECURITY.md。

- [ ] architecture 写 request flow、LangGraph 状态、SQLite 表、checkpoint/recovery、Provider、事件回放和部署边界。
- [ ] CONTRIBUTING 要求聚焦提交、前后端测试、Prompt 评测影响和无敏感数据。
- [ ] CHANGELOG 只记录已实现版本；CODEOWNERS 指定仓库 owner。
- [ ] 校验 Markdown 链接后提交。

### Task 4.3：发布门槛

**文件：** 创建 docs/release-checklist.md；修改 .github/workflows/ci.yml。

- [ ] 发布必须通过后端测试、前端测试、构建、评测回归、Docker smoke、密钥扫描、clean status 和 changelog 检查。
- [ ] release job 依赖所有测试/构建 job，任一失败都不发布 artifact。
- [ ] 用故意失败的评测 fixture 验证门槛会阻止发布，验证后删除 fixture。
- [ ] 提交：git add docs/release-checklist.md .github/workflows/ci.yml; git commit -m "ci: add release readiness gate"。

## 推荐执行节奏

- 第 1 周：0.1、0.2、1.1、1.2。
- 第 2 周：1.3、1.4、1.5，发布第一份评测报告。
- 第 3 周：2.1、2.2，保持现有 API 和 FakeLLM 兼容。
- 第 4 周：2.3、2.4，完成配置化流程扩展示例。
- 第 5 周：3.1、3.2，先事件表后 dispatcher。
- 第 6 周：3.3、3.4、3.5，完成 Docker 和重启恢复证据。
- 第 7 周：4.1、4.2、4.3，打磨公开仓库并创建第一个 Release。

## 最终验收

- [ ] 30 条以上评测集有基线和回归门槛。
- [ ] 新增流程节点不需要修改核心 runner。
- [ ] Provider 至少有 Fake、DeepSeek 和一个兼容实现。
- [ ] worker 重启、SSE 断线和重复消费有测试证据。
- [ ] 两个实例下任务、事件和限流状态一致。
- [ ] CI 覆盖测试、构建、评测、容器启动和发布门槛。
- [ ] README、架构、评测、运维、安全和贡献文档完整。
- [ ] git ls-files 不包含 .env、数据库、.venv、node_modules 或 dist。
- [ ] 所有公开质量/性能结论都有数据来源。

在这些门槛完成前，README 应使用准确定位：

> 具备可恢复编排、人工审核和反馈学习能力的营销内容生产系统 MVP。

