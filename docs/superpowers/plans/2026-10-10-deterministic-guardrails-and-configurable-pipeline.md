# 确定性守门层与可配置节点链实施计划

> 本计划把「内化三个专家技能包」拆成可独立验收的五个阶段。每个阶段先写测试再实现；每阶段完成后单独提交。
> 设计依据见 `docs/superpowers/specs/2026-10-10-deterministic-guardrails-and-configurable-pipeline-design.md`。

**目标：** 给 ContentForge 补上**零成本、可复现、可单测的硬约束执行层**，并把节点链配置化，
让渠道规范从「模型自觉遵守」变成「代码强制保证」。

**架构策略：** 保留现有 LangGraph 0.6.11 + FastAPI + SQLite 路径不变。
按「可否完全枚举」切分职责：能枚举且要求 100% 复现的规则落成**代码 + YAML 资产**；
需要判断力的（去 AI 味改写）做成**一个 Agent 节点**。**不新增 skill loader、不引入外部依赖。**

**技术栈：** Python 3.10、LangGraph 0.6.11（固定，不得升 1.x）、Pydantic v2、PyYAML、pytest。

---

## 执行规则

- 工作目录：`D:\何泽斌\Documents\cursor项目\contentforge`。
- 后端统一使用 `..\.venv\Scripts\python.exe`（Python 3.10.6）。
- **必须显式传 `--basetemp` 到仓库内**，否则 pytest 的临时目录会命中本机 `/tmp` 重定向与系统清理策略，
  导致 `tmp_path` 相关的测试报 `FileExistsError` 或输出被截断。
- **必须设置 `NO_PROXY=127.0.0.1,localhost`**，否则本机 HTTP 调用会走代理并挂死。
- `tests/test_api.py` 单独运行约 150 秒（超过前台默认超时），且含时序敏感用例；
  改动 API/调度相关代码时**不要与其它 pytest 进程并行运行**，CPU 争用会放大其不稳定性。

~~~powershell
Set-Location 'D:\何泽斌\Documents\cursor项目\contentforge\backend'
$env:NO_PROXY='127.0.0.1,localhost'
$stamp = Get-Date -Format 'HHmmss'
..\.venv\Scripts\python.exe -m pytest -q --tb=short --basetemp="..\.pytest_tmp\r$stamp"
~~~

## 里程碑 1：P1 确定性守门层

### Task 1.1：字数口径单一真源

**文件：** 创建 `backend/app/guardrails/text_metrics.py`。

- [x] 定义 `count_chars(text, mode)`，`mode` 取 `raw` / `no_whitespace`；未知 mode 抛 `ValueError`。
- [x] 定义 `count_variant(variant, mode)` 返回 `{body, title, hashtags, notes, published, total}`。
- [x] **明确契约：`max_chars` 只约束 `body`**，`published = body + title + hashtags`，`total` 另含 `notes`。
- [x] `mode_from_channel(channel)` 从渠道配置读口径，缺省/非法值回落 `raw`。

### Task 1.2：五级降档

**文件：** 创建 `backend/app/guardrails/length_policy.py`。

- [x] `clip(value, limit)` 保证**返回值长度 ≤ limit**；优先在标点处断句（断点标点保留），
      无断点时硬裁并剥掉句尾虚词。
- [x] `build_segments(body)` 把正文拆成 `(钩子, 要点列表, 结语)`；钩子 `protect=True`；
      句子数 ≥3 时末句才视为可砍的结语。
- [x] `_detail_levels()` 生成三档细节：完整 / 短细节 / 只留论点（含「：」时以冒号前为论点）。
- [x] `downgrade_body()` 对「要点数 × 细节档 × 结语 × emoji」**穷举**取不超限的最长解；
      组合数 > `MAX_COMBOS(200)` 时退化为逐级贪心。
- [x] 优先级对齐微博引擎真实行为：**保要点条数 → 保细节详略 → 砍结语 → 去 emoji**。
- [x] 存在可行解时结果**一定不超限**；无解时硬裁并置 `fitted=False`。
- [x] 通过 `dropped` 与 `full_text` 保留被裁内容，供长文版/人工参考。

### Task 1.3：违禁词与广告法词表

**文件：** 创建 `backend/app/guardrails/rules/forbidden_words.yaml`、`backend/app/guardrails/compliance.py`。

- [x] 内化微博引擎的 **51 条**绝对化用语，每条带**可执行的替换脚本**（禁止「禁用」了事）。
- [x] 增加医疗 / 金融 / 功效 / 平台禁止四类条目。
- [x] 实现**长词优先**匹配 + `mask` 去重（「最高级」只命中一次，不额外命中「最高」）。
- [x] 实现**序数豁免**：`第一` 后面跟 `点/条/步/次/章/节/课/讲/部/集/期/年/月/日/周/批/版/幕/轮/场/线/手`
      时不命中（∴「第一次来北京」不违规，「行业第一」违规）。
- [x] 实现**英文边界守卫**：ASCII 词必须两侧非字母数字，避免 `ass` 命中 `Glasswing`。
- [x] 区分 `scope="draft"`（成稿）与 `scope="source"`（输入素材）；素材自带违禁词不计入成稿违规。
- [x] `user_banned` 用户自定义词独立成类；已在词表里的不重复计数。

### Task 1.4：标签 / emoji / 标题 / AI 味

**文件：** 创建 `hashtags.py`、`emoji_policy.py`、`title_check.py`、
`rules/ai_tells_zh.yaml`、`ai_tells.py`、`rules/title_rules.yaml`。

- [x] 标签三风格 `paired` / `hash` / `none`，去空、补井号、去重（大小写不敏感）、按上限截断、不足时主题兜底。
- [x] emoji 三策略 `remove` / `cap3` / `keep`；未知策略按「不动」处理（宁可不改，不可误删）。
- [x] **把 ZWJ（U+200D）也算作 emoji**：否则 `strip_emoji` 会留下不可见的 U+200D 残渣，
      既污染正文又白占字符数。
- [x] **AI 味资产中文重建**：humanizer 的 24 模式结构保留，词表全部换为中文语料。
- [x] 三条 fatal 级否定对举句式：`不是…而是` / `与其说…不如说` / `并非…而是`。
- [x] 七个结构指标：`em_dash_count`、`max_sentences_per_para`、`parallel_triple`、
      `bold_density`、`bold_list_density`、`emoji_in_heading`、`halfwidth_punct`。
- [x] 英文规范的「弯引号」规则**不照搬**（中文本来用全角引号），改为检测中文里混入半角标点并降为 info。
- [x] `render_patterns_for_prompt()` 从**同一份 YAML** 渲染 humanize prompt，消除两处漂移。
- [x] 标题六维权重（`benefit 0.25` / `structure 0.20` / `emotion 0.20` / `keyword_match 0.15` /
      `scarcity 0.15` / `compliance 0.05`）+ S/A/B/C 定档；分隔符**只写提示不参与打分**。

### Task 1.5：编排与报告

**文件：** 创建 `backend/app/guardrails/postprocess.py`、`report.py`、`__init__.py`。

- [x] `normalize_variant()` 只做「删信息以外、不改语义」的动作：emoji 策略、标签规范化、字数降档。
- [x] **违禁词替换与 AI 味改写一律只标记**，绝不静默改写。
- [x] `_draft_text()` 扫描范围 = 标题 + 正文 + 标签，**排除 `notes`**（那是模型自述，不对外发布）。
- [x] `run_guardrail()` 返回 `(归一化变体, GuardrailReport)`；原变体不被就地修改。
- [x] **渠道没配 `max_chars` 时跳过降档** —— 不能把 `0` 传给 `downgrade_body`
      （其语义是「预算为 0，什么都放不下」，会把正文清空）。
- [x] `hard_cut` 字段：钩子本身超限、只能硬裁时置位，进 `needs_human` 并令 `passed=False`。
- [x] `over_limit` 保留为**安全断言**：降档保证不超限，故它恒为 `False`；
      一旦为 `True` 说明降档逻辑有 bug —— 保留是为了不静默失败。
- [x] 报告用 Pydantic 定义，可直接 `model_dump()` 进 state / DB / 前端。

### Task 1.6：纯函数单测（P1 验收）

**文件：** 创建 `backend/tests/test_guardrails_*.py` 共 8 个文件。

- [x] `test_guardrails_metrics.py` / `_length.py` / `_compliance.py` / `_hashtags.py`
- [x] `test_guardrails_emoji.py` / `_ai_tells.py` / `_title.py`
- [x] `test_guardrails_postprocess.py` —— 编排集成（对外唯一入口，必须有覆盖）
- [x] 加入**规则资产质量闸门**：词表每条替换建议 ≥6 字；AI 味每条建议 ≥8 字且 metric 必须在注册表；
      每个 pattern id 在 prompt 里恰好出现一次；六维权重和为 1。
- [x] 回归测试：`clip()` 在 1..len(text) 全部宽度下都不得超限（曾因断句窗口写成 `text[:limit+4]` 而超限）。
- [x] 运行全量：**新增 159 项全绿，现有 93 项不受影响**（守门层只新增文件，不改图/API/前端）。
      已核实：`backend/app/` 下除 `app/guardrails/` 自身外，**没有任何模块 import 守门层**
      —— 不 import 就不可能影响既有行为。
- [x] 全量实测 252 项：248 通过，4 项失败**全部**在 `tests/test_api.py`（时序敏感，与本次改动无关）。

### Task 1.7：文档

**文件：** 创建 `docs/superpowers/specs/2026-10-10-...-design.md`、`docs/superpowers/plans/2026-10-10-....md`；修改 `.gitignore`。

- [x] spec 按项目六段式：目标 / 现状与边界 / 设计 / 测试设计 / 失败处理 / 验收标准。
- [x] `.gitignore` 增加 `.pytest_tmp/` 与 `_cf_*.log`。

## 里程碑 2：P2 可配置 PipelineDefinition（行为保持）

### Task 2.1：抽出流程定义

**文件：** 创建 `backend/app/agents/pipeline_definition.py`、`backend/app/agents/pipelines/marketing.yaml`、
`backend/tests/test_pipeline_definition.py`、`backend/tests/fixtures/pipelines/seo_rewrite.yaml`；
修改 `graph.py`、`runner.py`、`nodes.py`。

- [x] `PipelineDefinition` 含 **pipeline_id / version / ordered nodes / 边的条件分支 /
  human_gate / 入口 / 成功与失败目标 / 重试次数 / 上下行契约**。
- [x] 每个节点声明 `contract`：`factory`（实现）、`prompt_key`（prompt 契约）、
  `state_key`（写入的主状态键）；`inputs` / `outputs` 声明状态契约。
- [x] `marketing.yaml` 逐字复刻原硬编码图：6 个逻辑节点（5 阶段 + 1 人工闸门）、
  7 条边、温度 0.5/0.5/0.5/0.8/0.3、`max_retries: 0`。
- [x] `build_graph(llm, db_path, web_search, pipeline_id="marketing", *, pipelines_dir=None)`
  —— 省略参数时行为与改造前**逐字一致**（已用差分测试验证）。
- [x] `nodes.py` 的 `STAGE_KEYS` / `STAGE_LABELS` 改为**从定义派生**（消灭第二份抄写）。
      测试里保留**字面量**断言作为对外契约的证据。
- [x] `temperature` 由定义驱动：节点工厂新增 `temperature: float | None = None`，
      未传时回落到内置默认值 → 旧调用方（如 `test_nodes.py`）行为不变。
- [x] `max_retries` **真实生效**（`_with_retries` 包装），不为 0 时才包装 → 默认零行为差异。
      只重试「抛异常」的节点；内容质量判断不在此处，避免成本统计失真。
- [x] `graph.py` 是**组合根**：`NODE_BUILDERS` / `ROUTERS` 把定义里的名字解析成实现。
      `pipeline_definition` 保持纯数据，**不 import 任何节点实现**（避免循环依赖）。
- [x] 定义挂到图上（`graph._pipeline_definition`，沿用既有 `_saver_cm` 的做法），
      `runner` 据此决定阶段集合与可编辑上游 → **新增定义无需改 runner.py**。
- [x] `seo_rewrite` 测试专用定义（`tests/fixtures/pipelines/`）验证：
      阶段集合 `{research, copywriting}` ⊂ marketing 的 5 阶段、可独立构建并跑通。
- [x] **配置错误必须失败**（54 项测试覆盖）：未知节点 / 未知 factory / 未知 router /
      缺 factory / 缺 prompt_key / 缺 state_key / state_key ∉ outputs / 阶段改名 /
      重复 key / 未知 kind / 入口缺失或重复 / 死配置不可达 / 输入无产出者 /
      闸门缺失或 kind 错 / 条件边缺 branches / 边缺 to / 可编辑阶段非阶段。

> **本阶段发现的两个隐式耦合（当时选择显式化而非绕过）**
>
> 1. **节点实现把状态键硬编码在代码里**（`make_research_node` 返回 `{"research": ...}`），
>    而 `stage_status`、DB 阶段行、前端流水线都以该键为准。因此**阶段节点不能改名**。
>    已用 `key == state_key` 校验把这条约束变成加载期的明确报错，
>    而不是「改个名字后 DB 阶段行与前端静默错位」。
> 2. **`api/routes.py` 建任务时用模块常量 `STAGE_KEYS` 预建阶段行**，
>    而不是读图所挂的定义。marketing 下两者相同所以无差异，
>    但换流水线就会预建错行。该文件**不在 Task 2.4 的文件清单里**，故本阶段不动，
>    记入 P3 —— 已在 **Task 3.0 修复**。

## 里程碑 3：P3 守门节点入链

### Task 3.0：解开 P2 遗留的两处耦合 ✅

**文件：** `nodes/`（`nodes.py` 转为包）、`graph.py`、`runner.py`、`api/routes.py`。

- [x] **状态键改由定义提供**：节点工厂新增 `state_key=` 形参（缺省值 = 原硬编码值，
      所以 `make_research_node(fake)` 这类直接调用行为不变），`graph.py` 的
      `NODE_BUILDERS` 把 `spec.state_key` 传进去 → **YAML 成为单一真源**。
- [x] `routes.py` 的「预建阶段行」与「可编辑阶段」改为读图所挂定义
      （`definition_stage_keys` / `definition_editable_stage_set`）——P2 记录的 bug 已修。
      顺带删掉 `EDITABLE_STAGES` 硬编码常量与未使用的 `STAGE_LABELS` 导入。
- [x] **但没有放宽 `key == state_key`**：见下方「为什么没有解锁阶段重命名」。

> **为什么没有解锁阶段重命名（对原计划的修正）**
>
> 原计划写的是「节点从定义读 `state_key` 后即可重命名阶段」。落地时发现这个推论不成立：
> **提示词构造器按状态通道名读上下文**（`build_competitor_messages` 里写死
> `state.get("research")`、`build_copywriting_messages` 里写死 `state.get("strategy")`）。
> 所以放宽约束的真实后果是：把 `state_key` 改成 `keyword_research` 之后，
> 竞品分析**静默收不到调研结果**——正是这条校验要防的那类故障。
>
> 于是取舍是：**让状态键从硬编码变成配置驱动（去掉代码与 YAML 的重复事实），
> 同时保留相等约束并把理由写进报错信息**。要真正支持改名，得先让提示词构造器
> 也按定义取状态键（那是独立任务，不在本轮范围）。
> 需要换展示名时用 `label`——`seo_rewrite` 就是这么做的（key `research` / label 「关键词调研」）。

### Task 3.1：`guardrail` 节点 ✅

**文件：** 新建 `backend/app/agents/nodes/guardrail.py`（`nodes.py` → `nodes/` 包）；
修改 `graph.py`、`nodes/__init__.py`、`pipeline_definition.py`、`state.py`、`prompts.py`、
`pipelines/marketing.yaml`、`api/routes.py`、前端 3 个文件。

- [x] 守门层作为**独立节点**接在 `copywriting` 之后、`review` 之前。
- [x] 新增节点类型 **`kind: step`**（确定性步骤）：有 `factory`、**不许**写 `prompt_key`、
      默认是阶段。这样「不调模型的守门层」与「LLM 节点」在配置层面就是两种东西，
      `step` 写了 `prompt_key` 会被加载期拒绝（说明作者理解错了）。
- [x] 从 state 取 `channel_id` / 调研关键词 / 品牌 `taboos` / 任务简报，调 `run_guardrail()`。
- [x] 报告写入 `guardrail` 状态键并作为阶段产出落库；归一化后的变体**回写** `copywriting`
      （下游看到的必须是守门层实际检查过的那一份）。
- [x] `needs_human` 透传：`state["needs_human"]` + `human_confirm` 的 interrupt payload。
- [x] `build_review_messages` 注入守门层摘要（`_guardrail_digest`）——
      否则「放在 review 之前是为了让 review 读到结构化结果」只是句空话。
- [x] **守门层不消耗 token**：全链路 6 个阶段、5 次模型调用（已在图级测试中断言）。
- [x] 更新硬断言阶段集合的 4 处测试（`test_api.py` / `test_graph.py` /
      `test_demo_mode.py` / `test_pipeline_definition.py`）。
      `test_nodes.py`、`test_reliability.py` 无需改（前者直接调工厂、后者只查 review 行）。
- [x] `marketing.yaml` 显式启用；`seo_rewrite` 不启用 → 有测试断言
      **未启用守门层的定义链路不变**（守门是可选节点）。
- [x] 前端跟上：`STAGES` 加「质量守门」、`StageKey` 加 `guardrail`、守门报告专属渲染、
      守门阶段不出「编辑」按钮（工具结论不开放手改）。

> **额外发现并顺手修掉的**：`split_user_words()` 之前不存在——品牌 `taboos` 是**自由文本**
> （可能是 `"最,第一,根治"`，也可能是 `"不夸大宣传、不承诺具体疗效"` 这样的**规则句子**）。
> 直接当词表用会误报，整句丢弃才安全。已按「宁可漏检、不可误报」实现并测试
> （单字丢弃、超长整条丢弃、去重、可直接喂给扫描器）。

### Task 3.2：P3 验收 ✅

- [x] 新增 `tests/test_guardrail_node.py`（29 项）+ 合规词表解析 8 项 +
      定义层 9 项；**无 LLM 成本**由「6 阶段 / 5 次模型调用」的图级断言钉住。
- [x] 排除 `tests/test_api.py` 的 **334 项全过**（15.5s）。
- [x] 前端 `tsc -b` 无错、`vitest run` **19 项全过**（含阶段契约与报告解析）。
- [x] 报告的「标记而不改写」有专门断言：违禁词命中后**正文一个字都不变**。
- [x] 降档后 `body ≤ max_chars` 且 `downgraded=True`，同时**不**误判成「需人工」。

## 里程碑 4：P4 humanize 去 AI 味节点 ✅

### Task 4.0：把 LLM 管道抽成 `nodes/_common.py` ✅

**文件：** 新建 `backend/app/agents/nodes/_common.py`；改 `nodes/__init__.py`。

- [x] humanize 一次执行会**多次调用模型**（每个需要改写的变体一次），累加逻辑必须复用
      `COST_INPUT_PER_M` / `COST_OUTPUT_PER_M` —— 否则出现第二套成本口径，
      与「字数口径只能有一套」是同一条纪律。
- [x] 抽成独立模块而不是留在 `__init__.py`：包 `__init__` 要 import 子节点，
      子节点若反过来 import `__init__` 就成了环（且 ImportError 只在
      「`__init__` 尚未执行到该名字」时出现，极难排查）。
- [x] `nodes/__init__.py` 重新导出（含 `_safe_json` 等下划线别名）——
      `evals/run_eval.py` 导入的就是这些名字，**不能破**。
- [x] 新增 `accumulate_all()`：多次调用一次性累加，用同一单价常量与同一取整时机
      （先求和再 round），所以结果与逐次调用完全一致。

### Task 4.1：`humanize` Agent 节点 ✅

**文件：** 新建 `backend/app/agents/nodes/humanize.py`；修改 `prompts.py`、
`pipeline_definition.py`、`pipelines/marketing.yaml`、`graph.py`、`state.py`；
前端 4 个文件。

- [x] （P3 已铺好的前提）`nodes/` 已是包，新节点直接放 `nodes/humanize.py` 即可。
      提示词构造器仍留在 `prompts.py`（它是**模块不是包**；拆成 `prompts/` 包会牵动
      所有导入点，等真需要再动）。
- [x] **默认范围只覆盖公众号**（`channel_id == wechat`），不加「每任务开关」→ **本轮不需要 DB 迁移**。
- [x] prompt 由 `render_patterns_for_prompt()` 从 `ai_tells_zh.yaml` 渲染 —— 与守门层共用单一真源。
- [x] 实现混合循环：**确定性预扫 → Agent 改写 → 确定性复扫 → 验收判定**。
- [x] 验收失败**永远保留原文**（降级而非丢弃）；否则「去 AI 味」会变成「把好文案改坏」。
- [x] 复扫结果与原文对比写入报告，让降级原因可追溯。
- [x] **改写只替换 `body`**：`notes` 与渠道专属字段（如微博 `hook_type`、
      产品页 `selling_points`）原样保留 —— 改写不该顺手吞掉别的字段。
- [x] **回写 `copywriting`**：采纳 → 改写稿；拒绝/失败 → 原文。
      下游（守门层 / 审校 / 人工确认）看到的必须是这一份。

**位置：接在 `copywriting` 与 `guardrail` 之间（而不是守门层之后）。**

> **为什么**：P3 立的不变式是「守门报告描述的必须是**最终交付的那份文案**」。
> 若把改写放在守门层之后，报告就会描述一份已经被改写掉的文字，用户按报告核对
> 交付物时对不上。放在前面还有额外好处：改写引入的违禁词/超长，守门层照样兜得住
> （有专门测试：改写稿本身超长时，守门层的降档照旧生效）。

**四种处置（互相区分，不混为一谈）：**

| decision | 含义 | 是否保留原文 |
|---|---|---|
| `accepted` | 命中数严格下降、无新致命句式、长度未塌陷 | 否（用改写稿） |
| `rejected` | **有结果但没过验收**（未减少 / 引入新致命 / 长度塌陷） | 是 |
| `skipped` | 无需改写（无 warning/fatal 命中，或超出单次改写预算） | 是 |
| `failed` | **没有得到可用结果**（调用异常 / 非 JSON / 返回空正文） | 是 |

> `rejected` 与 `failed` 必须分开：前者说明「模型改了但改得不够好」，
> 后者说明「模型这次没给东西」。两者的排查方向完全不同。

**顺带补的显式契约：`editable`。**

> P3 写的规则是「可编辑 = Agent 阶段」。humanize 是 Agent，但它的产出是**处置报告**，
> 真正生效的是它回写的 `copywriting`。**若按老规则它会自动变成可编辑** —— 而编辑
> 一个不参与回填的状态键，正是这个项目最讨厌的「**静默失效的编辑入口**」。
>
> 所以把判据改准确：**可编辑 = 产出即交付物的阶段**。`NodeSpec` 新增
> `editable: bool | None`（`None` = 按 kind 推导，保持既有行为；`false` = 显式关闭），
> 并提供 `is_editable` 属性；`editable: true` 出现在非「Agent 阶段」上会在**加载期报错**
> （声明 true 却不满足前提 = 作者理解错了字段含义）。

- [x] **不改写没问题的文案**：只有 fatal/warning 级命中才触发改写；info 级
      （半角标点、粗体密度、emoji 标题）是排版提示，为一个标点花一次模型调用不划算。
- [x] **成本闸门**：`MAX_REWRITES_PER_RUN = 3`，超出预算的变体原样保留并记 `skipped`。
- [x] **`needs_human` 不由 humanize 写**：守门层在每个变体处置上都用
      `list(report.needs_human)` **整体替换**（因为审校回路会重跑，需要刷新而不是累积），
      所以 humanize 写进去的会被覆盖。它改为把降级原因写进自己的报告，
      并把该报告加进 `human_confirm` 的 interrupt payload —— 「保留原文」这件事要看得见。

### Task 4.3：补上演示模式对新阶段的覆盖 ✅

**背景（收尾启动时实测发现）**：`FakeLLMClient` 是按 **system 角色关键字**路由的，
它只认识旧的 5 个角色。新增 humanize 节点后，它的 system 角色
「中文文案润色编辑」落到兜底分支，返回 `{"error": "无法识别的阶段"}` ——
humanize 拿到空正文，**按设计降级成 `failed` 并保留原文**。

问题不在于它报了错，而在于**它什么都没报**：演示模式照跑不误、任务照样到达人工确认，
只有那一阶段的报告是 `failed`。这正是本项目最忌讳的静默降级，而且**没有任何测试会变红**。

- [x] `fake_llm.py` 新增 `_dynamic_humanize()`：从 humanize 用户消息里
      用 `<<<正文开始 … 正文结束>>>` 边界取出待改写正文，做定点消除。
- [x] **正则不复制**：替换表只登记 `pattern_id -> 替换文本`
      （`negative_parallel` / `not_x_but_y`），正则**运行时从 `ai_tells_zh.yaml` 现取**
      （`load_ai_tell_rules()`）。规则资产改了写法，演示改写自动跟上 ——
      避免演示 fixture 与守门层出现「两套特征表」。
- [x] `roles` 元组加入「中文文案润色编辑」，让路由像其它阶段一样优先读 system 文本。
- [x] 新增 **3 项**测试（`tests/test_demo_mode.py`，17 → **20 项**）：
      - `test_fake_llm_answers_every_declared_stage_role`：**契约测试**。
        用 `inspect.getsource(prompts)` + 正则解析出 `_sys("…")` 里**声明过的所有角色**，
        逐个喂给 `FakeLLMClient`，断言响应里没有 `error` 字段。
        以后再新增 Agent 节点，只要忘了加分支，这个测试立刻变红。
      - `test_fake_llm_humanize_rewrite_reduces_ai_tells`：演示改写必须**真的**
        让 AI 味命中总数下降，且主题保留、长度不塌陷、`changes` 非空。
      - `test_demo_pipeline_humanize_is_accepted_for_wechat`：公众号链路全流程跑完，
        `stats.accepted == 1`、`failed == 0`、写回正文的 AI 味低于改写前。
- [x] 实测（重启服务后真跑，非测试桩）：

  | 渠道 | 阶段状态 | 处置 | AI 味 | fatal | 字数 | 守门 |
  |---|---|---|---|---|---|---|
  | 公众号 | `completed` | `accepted` | 3 → 2 | 1 → 0 | 287 → 279 | passed |
  | 小红书 | `skipped` | — | — | — | — | — |

  小红书的分支给的是 `enabled: false` + 明确 reason
  （「去 AI 味改写仅对公众号（wechat）启用，当前渠道为 xiaohongshu」），
  而不是含混的空报告。

### Task 4.2：P4 验收 ✅

- [x] 新增 `tests/test_humanize_node.py`（**41 项**）：纯函数验收判定 8 项、
      提示词单一真源 4 项、节点级四种处置 + 成本闸门 + 报告序列化 21 项、
      图级位置/范围/不变式 8 项。
- [x] `test_pipeline_definition.py` 增补 6 项 `editable` 契约校验。
- [x] 排除 `tests/test_api.py` 的 **389 项全过**（16.9s）。
- [x] 前端 `tsc -b` 无错、`vitest run` **24 项全过**（含阶段顺序、`skipped` 展示、
      `parseHumanizeReport`）。
- [x] **「报告描述最终文案」有专门断言**：公众号任务下 `copywriting` 是改写稿、
      守门层 `ai_tell_stats.fatal == 0`、`passed is True`。
- [x] **「失败永远保留原文」有专门断言**：模型抛异常 / 返回非 JSON / 返回空正文
      三种情况下，`copywriting[0]["body"]` 与原文**逐字节相同**，且流水线仍能到达人工确认点。
- [x] **零 token 有专门断言**：非公众号链路仍是 **5 次模型调用**（7 个阶段），
      公众号链路是 6 次。
- [x] **本阶段改动到的既有测试（均为预期改动）**：阶段集合 6 → 7 ——
      `test_api.py:109`、`test_graph.py:81`、`test_demo_mode.py:272`、
      `test_guardrail_node.py`（阶段集合 + 位置断言）、
      `test_pipeline_definition.py`（节点 7→8、边 8→9、版本 2→3、阶段/温度字面量）。
      `test_nodes.py`、`test_reliability.py` **未被改动且继续通过**。

## 里程碑 5：P5 渠道规则资产与评测口径统一 ✅

### Task 5.1：渠道结构化字段 ✅

**文件：** 修改 `backend/app/templates/channels.yaml`。

- [x] 四个渠道（含 `product_page`）全部显式声明 7 个守门参数：
      `emoji_policy` / `hashtag_style` / `hashtag_min` / `hashtag_max` / `title_min_chars` /
      `title_max_chars` / `char_count_mode`。
      顺带在文件头写清**两类字段、两种消费者**：`require_*` / `copywriting_guide` / `tone`
      是给**模型**的风格提示（整份配置会被 `_dump(channel)` 渲进创作提示词）；
      这 7 个是给**工具**的确定性参数。
- [x] 守门层的 `policy_from_channel` / `rules_from_channel` / `mode_from_channel`
      已支持「显式优先、缺省推断」，因此这一步**不需要改守门层代码**（唯一例外见下）。

#### 显式化过程中修掉的两处口径问题

1. **小红书标签写法：`paired` → `hash`（行为变更，属缺陷修正）**
   缺省推断是「`require_hashtags` 为真 → `paired`」，于是小红书拿到了**微博的成对写法**
   `#话题#`。而 `hashtags.py` 的模块文档一直写着「`hash` —— `#话题`（小红书：单个井号）」——
   即**实现推断与模块自身声明的平台规范相反**。这是「把规则藏在自由文本里」的典型后果：
   推断值有测试盯着（`test_guardrails_hashtags.py` 覆盖的是推断规则本身），
   但它是否符合平台规范，没有任何东西盯着。

2. **`hashtag_min` 的语义被显式写清楚：它是「必须有标签」，不是「必须有多少个」**
   `normalize_tags` 在数量不足时**只兜底补 1 个**，补不到 `min_count`。
   所以给小红书写 `hashtag_min: 5`（照抄 guide 的「2-3 + 3-5」）会造成
   **声明值与实现语义不符**。最终取值 `1`：代码层只表达硬底线，
   「5-8 个」这种风格数量交回提示词（`copywriting_guide.tags_rule` 本来就在提示词里）。

#### 取值依据（每条都能追到来源）

| 渠道 | emoji_policy | hashtag_style | min | max | title 区间 | 依据 |
|---|---|---|---|---|---|---|
| xiaohongshu | cap3 | hash | 1 | 8 | 18-20 | guide 标题「18-20 字」；guide 标签「2-3 + 3-5」→ 上限 8；平台标题上限 20 字 |
| wechat | remove | none | 0 | 0 | 0-64 | 公众平台图文标题上限 64 字 |
| weibo | cap3 | paired | 1 | 4 | 0-0 | 创作提示词「附 2-4 个相关话题标签」；微博话题必须成对才生效 |
| product_page | remove | none | 0 | 0 | 0-0 | guide 只给标题公式不给字数，显式放弃判定 |

- [x] **行为变更清单**（诚实记录，不是「行为保持」）：
      - 小红书：标签由 `#话题#` 变 `#话题`；空标签时会兜底补 1 个；标题 18-20 字开始判定。
      - 微博：标签上限 4（超出截断）；空标签时兜底补 1 个。
      - 公众号：标题上限 64 字开始判定。
      - 其余（emoji cap3 / remove、`char_count_mode` 全 `raw`、`max_chars`）**与推断值一致**。
- [x] **连带修复的既有测试（1 项）**：
      `test_guardrail_node.py::test_guardrail_survives_the_review_loop_and_reruns`
      断言第二轮 `needs_human == []`，而它的合成标题「智能保温杯怎么选」只有 8 字，
      被新生效的标题区间如实标了「标题长度不合规」——那是**正确行为而非残留**。
      修法是给该用例换成落在区间内的 19 字标题（`TITLE_XHS_IN_RANGE`）。

### Task 5.1：P5 验收 ✅

- [x] 新增 `tests/test_channel_rules.py`（**31 项**），四段：
      资产完整性 8 项、显式值真的驱动守门层 11 项、标题区间 5 项、渠道间不串味 1 项、
      其余为参数化展开。
      核心用例：`test_xiaohongshu_hashtags_are_single_hash_not_paired`（缺陷回归）、
      `test_hashtag_min_backfills_one_tag_from_the_topic`（兜底语义）、
      `test_channels_without_a_title_range_skip_the_check`（「不判定」要说清楚）、
      `test_explicit_values_flow_through_the_channel_helpers`（**配置写了要真被读到**）。
- [x] 排除 `tests/test_api.py` 的 **420 项全过**；`tests/test_api.py` 仍是那 2 项环境性 flaky。

### Task 5.2：统一评测字数口径 ✅

**文件：** 修改 `evals/metrics.py`、`evals/test_metrics.py`、`evals/schema.py`，
以及 `backend/app/guardrails/postprocess.py` / `__init__.py`。

- [x] 删掉 `CHANNEL_MAX_CHARS`（评测侧第二套上限表）。
- [x] `evals/metrics.py` 改为从生产口径取数：
      `count_variant()` + `mode_from_channel()`（字数）、`get_channel()` 读 `channels.yaml`
      （渠道上限）、`draft_text()`（计分范围）。
      模块内自带 `sys.path` 引导（与 `run_eval.py` 同一套做法）——**这是它必须依赖 backend 的理由**。
- [x] **顺带消灭了第二处漂移**：计分范围。
      旧实现把 `notes` 也算进违禁词 / 硬约束 / 品牌名扫描，而守门层明确把
      「成稿扫描范围」定义为「标题 + 正文 + 标签，不含 notes（模型自述，不对外发布）」。
      于是 `evals` 比**生产更严**：模型在 notes 里写「本篇刻意避开了『最好』」会被判违规。
      现在 `draft_text()` 是这条边界的唯一真源 —— 为此把它从 `postprocess._draft_text`
      改名为公开的 `draft_text` 并导出（同步改 `test_guardrails_postprocess.py` 的引用）。
- [x] `evals/schema.py` 的约定说明同步改口径（否则文档与代码打架）。
- [x] `text_metrics.py` 的模块文档从「存在两套口径」改为「第二套已删除」。

#### 口径变化对既有分数的影响（实测，不是「通过了」）

方法：拿**旧基线 `evals/results/baseline-fake.json` 里已存的 30 条变体**，
用新口径重新打分，逐项对比 `passed` 与 `reasons`。

**结果：0 处差异。** 三项 pass rate 改动前后都是 1.0。
重跑基线后 `git diff` 显示**只有 `generated_at` 变了**（1 行）。

**但这个「0」不能当成「口径改对了」的证据**，原因很具体：fake fixture 产出的
body 只有 40 余字（离 280 上限极远），`title` / `hashtags` / `notes` 也都是短样本，
两条被修正的边界（body-only 上限、notes 不计分）**在 fake 数据上一个都没被触发**。
换句话说：**基线对这种口径变化天然不敏感**。

真正证明口径改对的是新增的 15 项 `evals/test_metrics.py` 用例，例如：

- `test_only_the_body_counts_toward_the_channel_limit`：标题/标签/各 400 字仍判通过；
- `test_body_over_the_yaml_limit_is_reported_with_the_body_length`：正文超限被报出；
- `test_notes_are_not_scanned_for_forbidden_terms`：notes 里的禁用词不再算违规；
- `test_notes_do_not_satisfy_hard_constraints`：反过来，硬约束只在 notes 里也不算命中；
- `test_count_mode_is_taken_from_the_channel`：同一正文在 `raw` 下超限、`no_whitespace` 下通过；
- `test_metrics_and_production_guardrail_use_the_same_limit`：`report.max_chars == channel["max_chars"]`；
- `test_unknown_channel_fails_loudly_instead_of_falling_back`：渠道不存在就报错。

- [x] 评测套件 `evals/`：**32 项全过**（原 23 项）。
- [x] 回归闸门实跑：
      `run_eval --provider fake` 生成候选 → `check_regression.py` 输出 `{"passed": true}`。

> **已知缺口（需真实 Key 才能补）**：仓库里只有 `baseline-fake.json`。
> 用 `--provider deepseek` 起一份真实基线，才能知道这两处口径修正对**模型输出**的实际影响。
> 本次未做，因为环境没有 DeepSeek Key；不把它算作已验证。

---

## 收尾：P1–P5 全量验证与服务启动 ✅

**测试基线（本机 Python 3.10.6 + 项目 `.venv`）**

| 套件 | 命令 | 结果 |
|---|---|---|
| 后端（排除 `test_api.py`） | `../.venv/Scripts/python.exe -m pytest --ignore=tests/test_api.py` | **423 passed**（≈17s） |
| 评测 `evals/` | `python -m pytest`（`evals/` 目录内） | **32 passed** |
| 前端类型检查 | `npx tsc -b` | 无错 |
| 前端测试 | `npx vitest run` | **24 passed**（3 文件） |
| 端到端冒烟 | 真起服务 → 建任务 → 轮询到 `waiting_human` | 通过（两渠道） |

**收集总数 433 项** = 83 既有 + 167 守门层 + 69 流水线定义 + 29 守门节点 +
41 去 AI 味 + 31 渠道规则 + **20 演示模式** + 10 `test_api`。

> ⚠️ **本节下面这段「环境性 flaky」的结论已被证伪，见文末「故障修复」一节。**
> 当时把 `test_api.py` 的 2 项失败归因于「沙箱写入限制 / aiosqlite 拆解竞争」，
> 实际它们与用户线上遇到的失败**是同一个 bug**：WAL 旁路文件竞态。
> 教训：反复出现的「偶发」在最该被当成线索的时候，被我当成了噪声。
> 保留原文以记录误判过程。

**~~已知的环境性 flaky（与代码无关，已反复复现）~~**

- ~~`tests/test_api.py` 恒定有 2 项失败，根因是沙箱写入限制：
  `sqlite3.ProgrammingError: Cannot operate on a closed database` 与
  `aiosqlite … attempt to write a readonly database`。单独跑可通过。~~
- ~~`tests/test_feedback.py::test_feedback_api_flow` 约 1/3 概率在全量跑时抛
  `sqlite3.OperationalError`；单独跑必过，重跑全量也过。~~

**一点环境坑（值得记住）**：后端 pytest **必须**用项目自带的 `.venv`。
系统 `Python310` 里**没有** `langgraph` 与 `aiosqlite`，用它跑会导致 10 个测试文件**收集失败**。
`evals/` 与技能脚本仍用系统 `Python310`（那边只需要标准库 + pydantic）。

---

## 故障修复：WAL 旁路文件竞态（用户实测反馈，已修复）✅

### 现象

用户在浏览器里新建任务，任务**立即失败**：状态「失败」、**成本 ¥0.0000、耗时 0.0s**、
7 个阶段全部停在「等待」。后端日志：

```
sqlite3.OperationalError: attempt to write a readonly database
  runner.py:141 run_task_pipeline → _write_chunk
  runner.py:60  _write_chunk → _upsert_stage
  db_sql.py:52  upsert_stage → exec
  db_sql.py:28  exec → c.execute(sql, params)
```

### 排查：排除了「权限」这条最直觉的路径

| 检查 | 结果 |
|---|---|
| 数据库文件只读属性 | 无（`attrib` 只有 `A` 归档） |
| data 目录可写 | 可写 |
| 独立进程直写 | **成功** |
| `PRAGMA journal_mode` | `wal` |

所以不是权限。而「独立进程能写、服务里偶尔写不了」把矛头指向**并发**。

### 复现

模拟前端高频轮询（`/api/tasks` `/api/stats` `/api/brands` `/api/channels`）+ 连续创建任务：
8 个任务里 task 26 复现失败。日志显示失败点有两处，且**同一时刻其它任务全部成功**：

- task 25 挂在**第一句** `_update_task(status="running")`；
- task 26 挂在写阶段结果时。

→ 只在**多个流水线线程并发写**时触发。

### 根因（对照实验坐实）

WAL 模式下 SQLite 的行为是：**当最后一个连接关闭时，checkpoint 并删除
`-wal` / `-shm`**。而 `db_sql.exec()` 是**每条语句开一次连接、写完就关**；
本项目又有两条并发路径写同一个库——API 层 aiosqlite（事件循环线程）与
流水线里的同步 sqlite3（`asyncio.to_thread` 的多个工作线程）。
于是「A 线程关掉最后一个连接、删走旁路文件」与「B 线程新建连接、要重建旁路文件」
撞在一起，SQLite 返回 `SQLITE_READONLY`。

隔离实验（同一份库副本，8 线程各自「开连接→写→关连接」，跑 8 秒）：

| 条件 | `readonly database` 次数 | 结束后目录 |
|---|---|---|
| 无锚点 | **4** | 只剩 `contentforge.db`（旁路文件被删） |
| 持有一个常开锚点 | **0** | `-wal` / `-shm` 全程存在 |

### 修复一：`app/db_anchor.py` —— 进程级 WAL 保活锚点

进程存活期间持有一个**永不关闭**的连接。只要它活着，就永远不存在「最后一个连接关闭」，
旁路文件不会被删，竞态窗口从根上消失——对 aiosqlite 与同步 sqlite3 两条路径同时生效。

> **⚠️ 踩到的第二个坑（差点让修复失效）**：只跑 `PRAGMA journal_mode=WAL` 的锚点是**空转**的。
> PRAGMA 不会让 SQLite 打开 `-shm`，所以锚点既不持有旁路文件、也拦不住别的连接删除它们。
> 必须**真正读一次库**（`SELECT count(*) FROM sqlite_master`，sqlite_master 恒存在）。
> 实测：空库上只跑 PRAGMA，一次「开→写→关」后旁路文件仍被回收；
> 加上这条 SELECT 后，旁路文件在后续开关连接中始终存活。
> 这个坑是靠 `tests/test_db_wal.py` 在实现完修复后**仍然失败**才暴露出来的。

接入点：`db_sql.conn()` 与 `db.get_conn()` 都会先 `hold()`（任何连接路径都覆盖），
`main.py` 关停时 `release_all()`。

### 修复二：任务不再静默卡死（`runner.py`）

原代码有个结构性缺陷：

```python
def run_task_pipeline(...):
    _update_task(task_id, status="running")   # ← 在 try 之外
    try: ...
    except Exception:
        _update_task(task_id, status="failed")  # ← 这次写也可能失败
```

两次写入都可能失败：第一次失败异常直接冒到 `_run_background`（只记日志），
第二次失败则让任务**永久停在 `pending`**（看门狗只看 `running`）——
这正是 task 25 的表现。

- 「置 running」移进 `try`；三处调用点（`run_task_pipeline` / `confirm_pipeline` ×2）统一。
- 新增 `_mark_failed(task_id, exc)`：**自身绝不抛异常**（写不进去至少让日志说清楚），
  并把原因写进 `tasks.error`。
- 启动恢复覆盖 `pending`：进程在「建任务」与「开始执行」之间挂掉时，
  checkpoint 里没有状态，恢复成 `waiting_human` 是错的 → 置 `failed` 并说明原因。
- **看门狗仍然只看 `running`**：`pending` 是进程内等 `_task_slots` 的**合法**排队状态，
  并发上限 3 时排队十几分钟不算异常，看门狗误杀会损失正常任务。

### 修复三：失败原因可见（迁移 v4 + 界面）

此前失败只有日志、`tasks` 表没有原因字段，前端只能显示一个「失败」。

- 迁移 **v4**：`ALTER TABLE tasks ADD COLUMN error TEXT NOT NULL DEFAULT ''`。
- `repository.py` 用 `SELECT *`，新列自动进入 API 响应。
- `TaskDetail.tsx` 在 `status === 'failed'` 时展示原因；`types.ts` 加 `Task.error`；
  新增 `.banner-failed` 样式（与「等待人工确认」的黄色横幅区分）。
- 重跑时 `error` 会被清空，避免显示过期原因。

### 修复后的实测

| 验证 | 结果 |
|---|---|
| 同样的并发场景（**轮询更快**：0.5s 一次 + 8 个并发任务） | **8/8 全部 `waiting_human`，0 异常** |
| `tests/test_api.py` | **10 passed，连续 3 次全过** |
| 后端（排除 `test_api.py`） | **435 passed** |
| 后端全量（含 `test_api.py`） | **445 passed / 0 failed**（首次全绿） |
| `evals/` 套件 | **32 passed** |
| 前端 | `tsc -b` 无错、`vitest run` **27 passed**（+3 失败横幅用例） |
| 启动恢复 | 自动抓到之前卡 `pending` 的 task 25，置 failed 并写入原因 |

**新增测试 15 项**：`tests/test_db_wal.py` 4 项、`tests/test_reliability.py` 3 → 10 项、
`tests/test_db_migration.py` +1（v4）、前端 `TaskDetail.test.tsx` 3 项。

**`test_api.py` 那 2 项「环境性 flaky」同时消失了** —— 说明它们从来不是环境问题。
这是本次最值得记住的一条：**把反复出现、根因明确的失败归为「环境噪声」，
代价是让真实 bug 在线上以用户可见的方式重演。**



## 故障修复：「重新生成」点击无反应（原生 confirm 不可靠）✅

**用户反馈**：「重新生成键怎么用不了，点击后无反应」。

### 排查：排除法先把范围压到一个分支

前端 `TaskDetail.rerun()` 的代码路径只有三种走向：

| 走向 | 界面表现 |
|---|---|
| `window.confirm(...)` 返回 `false` | **静默 `return`，界面零变化** |
| 请求失败 | 出现红色 `error-box` |
| 请求成功 | 状态/成本刷新，按钮变禁用 |

「点击后无反应」只可能是第一条。同时后端接口**直测完全正常**
（`POST /api/tasks/31/rerun` → `200`、21ms、任务重新跑起来），
所以问题不在服务端。

### 实测复现（CDP 驱动真实 Chrome，非猜测）

用 Node 内置 `WebSocket` 直连 Chrome DevTools Protocol，走完整用户路径
（任务列表 → 进详情 → 点「重新生成」）：

```
按钮状态: { disabled: false, opacity: "1", cursor: "pointer" }   ← 看起来完全正常
点击返回: TIMEOUT(8000ms): Runtime.evaluate                        ← 渲染进程被阻塞
原生 dialog 事件: [javascriptDialogOpening(confirm), Closed]
/rerun 请求数: 0                                                   ← 一个请求都没发出去
点击后页面状态: { statusBadge: "待人工确认", banners: [仅"等待人工确认"] }
```

**症状精确复现**：按钮看起来能点，点击后确认框阻塞整个渲染进程，
而用户在内置浏览器预览面板（sandbox iframe / Electron 内嵌视图）里
**看不到那个原生模态框**，页面又已被卡住 —— 于是表现为「点了没反应」，
且网络面板干净、控制台无报错，用户没有任何排查线索。

> 顺带查证：WorkBuddy 的预览 iframe **带 `allow-modals`**，
> 所以根因不是「sandbox 屏蔽了对话框」，而是**原生模态框在嵌入式宿主里不可靠**
> （不可见 / 被遮挡，但阻塞照旧）。

### 修复：一律改用应用内确认

新增 `frontend/src/components/ConfirmButton.tsx` —— 点击后**原地展开确认条**
（`问题 + [确认] + [取消]`），不依赖任何宿主能力：

- 可见、可控、可测（vitest 直接点）；
- `Esc` 可退出确认态（误触后必须能退回来，否则按钮等于失效）；
- 确认条在根元素 `stopPropagation` —— 表格里「删除」按钮嵌在可点击的 `<tr>` 内，
  否则点删除会顺带把详情页打开。

三处原生 `confirm` 全部替换：`TaskDetail`（重新生成）、`TaskList`（删除任务）、
`BrandManager`（删除品牌）。

### 附带发现：禁用的 ghost 按钮外观**完全不变**

`styles.css` 里只定义过 `.btn.primary:disabled`，没有 `.btn.ghost:disabled`。
实测逐项比对（CDP 读 `getComputedStyle`）：

| | opacity | cursor | border-color | color | background |
|---|---|---|---|---|---|
| 可用 | 1 | pointer | rgb(229,231,238) | rgb(31,36,48) | rgba(0,0,0,0) |
| 禁用 | 1 | pointer | rgb(229,231,238) | rgb(31,36,48) | rgba(0,0,0,0) |

**完全一致**。而 `TaskDetail` 的「重新生成」是全项目**唯一**一个 `btn ghost` + `disabled`
的组合（任务运行中会禁用）—— 又一个「看起来能点、点了毫无反应」的静默源。
已补 `.btn:disabled` 通用样式，并把 hover 规则收紧为 `:not(:disabled)`，
否则禁用按钮 hover 时仍会变色，继续伪装成可点。

### 修复后实测（同一 CDP 脚本）

| | 修复前 | 修复后 |
|---|---|---|
| 点击调用 | `Runtime.evaluate` 超时 8s+ | 立即返回 `clicked` |
| 原生 dialog 事件 | 2（confirm 开/关） | **0** |
| 界面反馈 | 无 | 行内确认条「重跑将覆盖当前产出」+[确定重跑]/[取消] |
| `/rerun` 请求 | **0** | **1**（HTTP **200**） |

列表页「删除」同样验证：出现确认条、`stillOnListPage: true`（**没有**误触打开详情）、
点「取消」后 `DELETE` 请求数为 **0**、任务仍在列表。

### 防回归

- `ConfirmButton.test.tsx` 7 项：展开/确定/取消/Esc/禁用/不冒泡。
- `TaskDetail.test.tsx` +4 项：先确认后请求、取消不请求、运行中禁用。
- `no-native-dialogs.test.ts`（**元测试**）：递归扫描 `src/` 全部 `.ts/.tsx`
  （剥掉注释后再匹配，且保留换行以免行号错位），断言不再出现
  `confirm( / alert( / prompt(` —— 防止有人再把原生对话框加回来。

### 验证

| 验证 | 结果 |
|---|---|
| 后端全量（含 `test_api.py`） | **445 passed / 0 failed** |
| 前端 | `tsc -b` 无错、`vitest run` **39 passed**（原 24） |
| 浏览器端到端（CDP 真实点击） | 详情页重跑、列表页删除，两条链路均通过 |




