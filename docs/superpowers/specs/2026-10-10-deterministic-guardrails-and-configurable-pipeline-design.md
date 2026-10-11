# 确定性守门层与可配置节点链设计

## 目标

给流水线补上一层**零成本、可复现、可单测的硬约束执行层**，并把节点链从代码里抽出来变成
配置。具体解决三件事：

1. 目前流水线的所有硬约束（字数、违禁词、标签格式、emoji、标题长度）**没有任何确定性拦截**
   —— 合规完全依赖 review 节点的模型软判断，而唯一硬校验写在 `evals/metrics.py` 里，只在评测时跑。
2. 各渠道的成文规范（小红书/微博/公众号）散落在 `channels.yaml` 的自由文本里，模型「知道但要靠自觉遵守」。
3. 节点顺序写死在 `graph.py`，加一个节点要同时改图、runner、测试五处。

## 现状与边界

- `backend/app/templates/channels.yaml` 已注册 4 个渠道，每个都有 `max_chars` 与 `copywriting_guide`。
- 流水线为 `research → competitor → strategy → copywriting → humanize → guardrail
  → review → human_confirm`（interrupt），
  LangGraph **0.6.11 已固定**，不得升级到 1.x（静态边 API 会变）。
  P3 起在 `copywriting` 与 `review` 之间插入确定性守门阶段 `guardrail`；
  P4 起在 `copywriting` 与 `guardrail` 之间插入去 AI 味阶段 `humanize`（仅公众号）。
- `evals/metrics.py` 曾有一套**与 `channels.yaml` 不一致**的字数口径：把
  `title + body + hashtags + notes` 一起算，而 `channels.yaml` 的 `max_chars` 只约束 body。
  本设计前四轮不改它（评测是另一条链路，混进来会让 P1~P4 的验证失去对照），
  P5 一次性统一并重跑基线。
- `channels.yaml` 的渠道规范原本只有自由文本（`copywriting_guide`）与两个布尔
  （`require_emoji` / `require_hashtags`），守门层靠**缺省推断**取值。
  推断值有测试盯着**推断规则本身**，但没人盯着**推断结果是否符合平台规范** ——
  P5 把参数显式化，就是补上这个缺口（实测已抓到一处：小红书标签被推成微博的成对写法）。
- **不新增 skill loader、不引入外部依赖、不改 API 契约。** P1/P2 完全不碰前端；
  P3 起因为**流水线多了一个阶段**，前端必须同步（见「四、入链方式」）。
- 去 AI 味的默认范围**只覆盖公众号渠道**，不加「每任务开关」，因此本轮**不需要 DB 迁移**。
- 本设计内化三个已在工作区沉淀为 skills 的方法论（小红书标题六维评分卡、微博确定性写作引擎、
  中文 AI 味清单），做法是**把规则落成项目内的代码与 YAML 资产**，而不是运行时加载 skill。

## 设计

### 一、确定性守门层（`backend/app/guardrails/`）

按「可否完全枚举」切分：**能枚举且要求 100% 复现的规则 → 代码；需要判断力的 → 模型。**

| 模块 | 职责 | 为什么必须确定性 |
|---|---|---|
| `text_metrics.py` | 字数口径**单一真源** | 全项目只能有一套算法，否则必然漂移 |
| `length_policy.py` | 五级降档：穷举取不超限的最长可行解 | 模型算不准字符数 |
| `compliance.py` | 违禁词/广告法词表扫描（长词优先、序数豁免、英文边界） | 漏一个就是合规事故 |
| `hashtags.py` | 标签规范化（paired/hash/none + 数量兜底） | 格式错误会导致话题不生效 |
| `emoji_policy.py` | 渠道级 emoji 策略（remove/cap3/keep） | 机械操作 |
| `ai_tells.py` | 中文 AI 味**确定性预扫描** | 词表/句式/结构指标可判定 |
| `title_check.py` | 标题六维加权定档 | 纯算术不该交给模型 |
| `postprocess.py` | 编排：归一化 → 检出 → 报告；成稿扫描范围 `draft_text()` 的唯一真源 | 唯一的对外入口 |
| `report.py` | Pydantic 报告模型 | 直接进 state / DB / 前端 |

**降档优先级**（对齐微博引擎的真实行为，而非「改动大小」）：

```
保要点条数 → 保细节详略 → 砍结语 → 去 emoji
```

`downgrade_body()` 对「要点数 × 细节三档 × 结语 × emoji」**穷举**所有组合，取不超限的最长解；
组合数 > 200 时退化为逐级贪心，避免超长文本打满 CPU。**若存在可行解，返回结果一定不超限**；
被裁掉的要点通过 `dropped` 与 `full_text` 留痕，供长文版或人工参考。

**自动修 vs 只标记**的分界线——只做「删信息以外、不改语义」的动作：

- 自动修：emoji 策略、标签规范化、字数降档
- 只标记：违禁词命中、AI 味命中、标题问题、钩子被硬裁

违禁词**绝不自动替换**：替换脚本写进词表当建议，改不改由人定。

### 二、中文 AI 味资产（`rules/ai_tells_zh.yaml`）

humanizer 资产的 24 种模式结构可迁移，但**词表是英文的**（"serves as a testament"、"pivotal"），
对中文营销文案毫无用处。因此本文件是**中文重建版**：模式结构保留，词表全部换成中文语料，
并把可确定性判定的部分（破折号数量、否定对举正则、段落句数）做成 `metric` 规则。

两级使用：

- **守门层**（本层）：零成本扫一遍，fatal 直接挡住 `passed`。
- **humanize Agent 节点**（P4）：语义改写。它的 prompt 由 `render_patterns_for_prompt()`
  从**同一份 YAML** 渲染 —— 单一真源，避免「守门层扫的词」与「模型被告知要改的词」两处漂移。

fatal 级只有三条否定对举句式（`不是…而是` / `与其说…不如说` / `并非…而是`）；
英文规范里的「弯引号」规则**未照搬**（中文正文本来就用全角引号），改为检测
「中文段落里混入半角标点」并降为 info。

### 三、可配置节点链（P2，行为保持）

按项目自己已规划的 Task 2.4 落地，不另造机制：

- 新增 `backend/app/agents/pipeline_definition.py` + `backend/app/agents/pipelines/marketing.yaml`
- `build_graph(pipeline_id="marketing")` 读定义；**省略参数时行为与现在完全一致**
- 新增测试专用 `seo_rewrite` 定义，证明「加定义不用改 `runner.py`」
- LangGraph 0.6.11 是静态边，所以所有节点照常 `add_node`，
  由**查配置的路由函数**跳过被禁用的节点——单图不变，interrupt 行为不变。

**分层职责**（关键，避免循环依赖）：

| 层 | 职责 | 约束 |
|---|---|---|
| `pipeline_definition.py` | 纯数据：解析 + 校验 YAML | **不 import 任何节点实现** |
| `graph.py`（组合根） | 把定义里的 `factory` / `router` 名字解析成实现；并由定义派生**阶段契约**（`definition_stage_keys` / `definition_editable_stages` / `definition_editable_stage_set`） | 唯一 import 节点的地方 |
| `nodes/`（包） | 节点实现；`STAGE_KEYS` / `STAGE_LABELS` 从定义派生 | 单向依赖 `pipeline_definition` |
| `runner.py` / `api/routes.py` | 阶段集合、回填范围、可编辑阶段**全部取自图所挂定义** | 定义缺失时回落常量 / fail closed |

**节点类型（`kind`）与契约**：

| kind | 用途 | 必需契约 | 默认是阶段 |
|---|---|---|---|
| `agent` | LLM 节点（消耗 token） | `factory` + `prompt_key` + `state_key` + `outputs` | 是 |
| `step` | **确定性步骤**（不调模型，同输入必同输出） | `factory` + `state_key` + `outputs`；**写了 `prompt_key` 会报错** | 是 |
| `human_gate` | 人工闸门（interrupt） | `factory` + `state_key` | 否 |

判据只有一条：**需要判断力的交给 `agent`，可穷举/可复现的交给 `step`**。
把守门层标成 `step` 不是命名偏好——它决定了成本统计是否多算一份、是否被要求写提示词契约、
以及「同输入必同输出」这个可测试性保证是否成立。

**`editable`（P4 补）**：节点声明的 `editable: bool | None`，`None` 表示按 kind 推导
（Agent 阶段可编辑），`false` 表示显式关闭。判据是「**产出即交付物**」而不是「是不是 Agent」——
humanize 是 Agent，但它的产出是「改写处置报告」，真正生效的是它回写的 `copywriting`，
开放编辑入口只会得到一个存了但不会回填的**静默失效编辑**。
`editable: true` 出现在非「Agent 阶段」上会在加载期报错（声明 true 却不满足前提 = 理解错了字段）。

**「配置错误必须失败」是硬要求**：所有校验在**加载期**抛
`PipelineDefinitionError`。配置错误若被容忍，症状会是「某个 Agent 悄悄不跑了」——
最难排查的一类故障。校验清单：未知节点 / 未知 factory / 未知 router / 缺 factory /
agent 缺 `prompt_key` / step 带 `prompt_key` / 缺 `state_key` / `state_key ∉ outputs` /
阶段改名 / 非 Agent 阶段被列为可编辑 / 重复 key / 未知 kind / 入口缺失或重复 /
死配置不可达 / 输入无产出者 / 闸门缺失或 kind 错 / 条件边缺 branches / 边缺 to。

**状态键的单一真源**：节点工厂以 `state_key=` 形参接收定义里的 `contract.state_key`
（缺省值 = 改造前的硬编码值，所以 `make_research_node(fake)` 这类直接调用行为不变），
组合根负责把 `spec.state_key` 传进去 → 代码里不再重复抄一份状态键。

**但 `key == state_key` 这条约束**（阶段节点）**故意保留**，理由是：
阶段身份同时用于 ① `DB stages.stage_key` ② 前端流水线 key ③ `stage_status` 键，
而**提示词构造器按状态通道名读上下文**（`build_competitor_messages` 里写死
`state.get("research")`）。放宽它并不会解锁「重命名阶段」，只会把
「改个名字后竞品分析静默收不到调研结果」变成合法配置。
要真正支持改名，须先让提示词构造器也按定义取状态键——那是独立任务，不在本轮范围。
需要换展示名时用 `label`（`seo_rewrite` 就是这么做的）。

**重试次数不写死在 YAML**：审校回路的轮数上限仍由 `Settings.max_revision_rounds`
掌管（测试会 monkeypatch 它），YAML 里的 `max_rounds_source` 只**声明出处**。
节点级的 `max_retries` 则真实生效，默认 0 = 零包装、零行为差异。

### 四、入链方式（P3，已落地）

守门层作为**独立节点**接在 `copywriting` 之后、`review` 之前，而不是塞进 copywriting 节点内部。
三条理由都要落到实处，否则独立节点只是多一次状态搬运：

| 理由 | 落地方式 |
|---|---|
| 节点级失败可单独重试 | `run_guardrail` 是纯函数，重试零成本零 token；`kind: step` 与 LLM 的重试语义互不污染 |
| 报告可单独存档 | 报告写入 `guardrail` 状态键并作为**独立阶段产出**落库（`stages.output`），前端单独渲染 |
| `review` 能读到结构化结果 | `build_review_messages` 注入 `_guardrail_digest()` 摘要，并明确「字数与词表命中以此为准，不要自行重新估算」——否则两套口径必然打架 |

**数据流与不变式**：

```
copywriting ──► guardrail ──► review ──(不通过)──► copywriting
                   │
                   ├─ guardrail         : GuardrailReport.model_dump()
                   ├─ copywriting       : 归一化后的变体（回写）
                   └─ needs_human       : 只标记、不静默改写的事项
```

- **回写 `copywriting`**：下游（review / 人工确认 / 工件）看到的必须是守门层**实际检查过**的那一份，
  否则报告与交付物对不上。有专门断言钉住这一点。
- **零 token**：全链路 6 个阶段、**5 次模型调用**（图级断言）。
- **`needs_human` 一路送到 `human_confirm` 的 interrupt payload**：
  「标记而不静默改写」的约定如果在最后一站丢掉，用户看不到就只能算没标记。
- **可选性**：守门层由定义显式启用；`seo_rewrite` 未启用 → 有测试断言其链路与改造前一致。

**顺带解开的耦合**：`api/routes.py` 原本用模块常量 `STAGE_KEYS` 预建阶段行、
用硬编码 `EDITABLE_STAGES` 判断可编辑性——换流水线就会错。现已改为读图所挂定义，
且「可编辑 = **产出即交付物**的阶段」（P3 的表述是「Agent 阶段」，
P4 引入 humanize 后修正为现在这条判据，见第三节 `editable`）；
拿不到定义时 **fail closed**（谁都不许改）。

### 五、humanize 节点（P4，已落地）

只在 `channel_id == wechat` 生效。**混合闭环**：

```
确定性预扫 → Agent 改写 → 确定性复扫 → 验收判定 → 采纳 / 保留原文
```

- **预扫 / 复扫**复用守门层的 `scan_ai_tells`（同一份 `ai_tells_zh.yaml`），
  「守门层扫的词」与「模型被告知要改的词」不会漂移；
- **验收判定是纯函数**（`evaluate_rewrite`）：AI 味命中总数必须**严格下降**、
  不得引入新的致命句式、长度不得塌陷（不少于原文 50%）。任一不满足 → **保留原文**；
- **失败永远降级为原文**：模型报错 / 返回非 JSON / 返回空正文，一律保留原文并把原因写进报告，
  **任务继续**。否则「去 AI 味」会变成「把交付物搞丢」；
- **只改写 `body`**：`notes` 与渠道专属字段原样保留；**回写 `copywriting`**，
  下游看到的必须是这次实际采纳过的正文。

**位置：`copywriting → humanize → guardrail → review`（接在守门层之前）。**

> 守门层报告必须描述**最终交付物**（第四节立的不变式）。若把改写放在守门层之后，
> 报告就会描述一份已经被改写掉的文字，用户按报告核对交付物时对不上。
> 放在前面还有额外好处：改写引入的违禁词 / 超长，守门层照样兜得住
> （有测试断言「改写稿本身超长时降档照旧生效」）。

**四种处置**（互相区分，不混为一谈）：

| decision | 含义 | 保留原文 |
|---|---|---|
| `accepted` | 命中数严格下降、无新致命句式、长度未塌陷 | 否 |
| `rejected` | **有结果但没过验收** | 是 |
| `skipped` | 无需改写（无 warning/fatal 命中，或超出单次改写预算） | 是 |
| `failed` | **没有得到可用结果**（异常 / 非 JSON / 空正文） | 是 |

**成本**：只有 fatal/warning 级命中才触发改写（info 级是排版提示，不花调用）；
单次执行上限 `MAX_REWRITES_PER_RUN = 3`。非公众号链路**一次模型调用都不加**。

### 六、渠道规则资产与评测口径（P5）

#### 6.1 渠道参数显式化

`channels.yaml` 里从此有**两类字段、两种消费者**，这是本轮最重要的结构性区分：

| | 字段 | 消费者 | 性质 |
|---|---|---|---|
| 风格提示 | `tone` / `require_emoji` / `require_hashtags` / `copywriting_guide` | **模型**（`_dump(channel)` 整份渲进创作提示词） | 需要判断力，工具不校验 |
| 守门参数 | `emoji_policy` / `hashtag_style` / `hashtag_min` / `hashtag_max` / `title_min_chars` / `title_max_chars` / `char_count_mode` | **`app.guardrails`** | 可枚举，要求 100% 复现 |

守门层的读取规则是「显式优先、缺省推断」，所以**不写也能跑**——正因为能跑，
「没写」这件事此前不会以任何方式暴露。P5 让四个渠道全部显式声明，并加**资产一致性测试**：
每个渠道七个字段齐全、取值合法、`require_hashtags == (hashtag_style != none)`、
`require_emoji == (emoji_policy != remove)`、用标签的渠道必须有上限。

**`hashtag_min` 的语义界定**（避免声明值与实现不符）：`normalize_tags` 在数量不足时
**只兜底补 1 个**，补不到 `min_count`。所以它的语义是「该渠道必须有话题标签」，
而不是「必须有多少个」。代码层只表达硬底线（取 1），
「小红书 5-8 个 / 微博 2-4 个」这类**风格数量交回提示词**——
`copywriting_guide.tags_rule` 与渠道专属的 `output_instruction` 本来就在提示词里。

#### 6.2 评测口径统一

`evals/metrics.py` 不再自带任何渠道参数，改为从生产口径取数：

| 评测需要 | 取自 |
|---|---|
| 字数 | `guardrails.text_metrics.count_variant()` + `mode_from_channel()` |
| 渠道上限 | `templates.get_channel()` → `channels.yaml` 的 `max_chars` |
| 计分范围 | `guardrails.postprocess.draft_text()`（标题 + 正文 + 标签） |

为此把「成稿扫描范围」这条边界也收敛成单一真源：`postprocess._draft_text`
改名为公开的 `draft_text` 并导出。此前它有两份实现——守门层一份（不含 notes），
`evals` 一份（含 notes）——**评测因此比生产更严**：模型在 notes 里写
「本篇刻意避开了『最好』」会被判成命中违禁词。

代价是 `evals/metrics.py` 现在**必须**依赖 backend 包（模块内自带 `sys.path` 引导，
与 `run_eval.py` 同一套做法）。这是「口径单一真源」的必然代价，写进模块文档。
`evals/schema.py` 不受影响（仍不依赖 backend，只管样本定义与校验）。

## 测试设计

新增 8 个测试文件（P1），全部是**纯函数单测，不碰图/API/前端、不调模型**：

| 文件 | 覆盖 |
|---|---|
| `test_guardrails_metrics.py` | 字数口径、字段分离 |
| `test_guardrails_length.py` | 断句、裁剪不超限、降档、`full_text` 保留 |
| `test_guardrails_compliance.py` | 词表质量闸门、序数豁免、英文边界、长词优先、双 scope |
| `test_guardrails_hashtags.py` | 三种风格、去重、截断、主题兜底 |
| `test_guardrails_emoji.py` | 识别、ZWJ 残渣、三种策略 |
| `test_guardrails_ai_tells.py` | 规则资产质量闸门、fatal 句式、指标、prompt 渲染一致性 |
| `test_guardrails_title.py` | 定档边界、六维打分、分隔符只提示不打分 |
| `test_guardrails_postprocess.py` | 编排集成：自动修修对了、只标记的没被偷改 |

**P3 新增测试**：

| 文件 | 覆盖 |
|---|---|
| `test_guardrail_node.py` | 节点级：干净文案零改动、降档成功不误报「需人工」、**违禁词只标记且原文一字不变**、素材/成稿双 scope、品牌 `taboos` 词表化、`max_chars` 缺省不清空正文、空变体给理由、`state_key` 可配置、真实渠道 emoji/标签生效；图级：**6 阶段 / 5 次模型调用**、审校提示词含守门结论、打回重跑后报告刷新、`needs_human` 进 interrupt payload、报告可 JSON 序列化 |
| `test_guardrails_compliance.py`（增补） | `split_user_words`：分隔符、单字丢弃、长句整条丢弃、去重、脏输入、可直接喂给扫描器 |
| `test_pipeline_definition.py`（增补） | `kind: step` 的契约校验、守门层是 step 而非 agent、可编辑阶段排除确定性步骤、`resolve_factories` 覆盖全部节点、守门层在定义中的位置 |

**前端契约测试**（`Pipeline.test.tsx`）：阶段顺序字面量断言（含 `humanize` 与 `guardrail`）、
`EDITABLE_STAGES` **同时排除**守门层与去 AI 味、`skipped` 与 `completed` 的展示文案不同、
`parseGuardrailReport` / `parseHumanizeReport` 的取数与缺省。

**P4 新增测试**：

| 文件 | 覆盖 |
|---|---|
| `test_humanize_node.py`（41 项） | 纯函数：验收判定 8 项（严格下降 / 新致命句式 / 长度塌陷 / 空结果 / info 不触发）；提示词：与扫描器共用同一份资产、每条 pattern 恰好出现一次、列出命中样本、禁止新增事实与删减；节点级：非公众号跳过零 token、干净文案零调用、info 不花调用、采纳 / 拒绝 / 失败 / 无需四种处置、只替换 body、多变体独立、成本闸门、成本累加、空变体、`state_key` 可配置、报告可序列化；图级：公众号 7 阶段 6 次调用、非公众号 7 阶段 5 次调用、**守门报告描述改写后的文案**、改写稿超长仍被降档、改写失败任务仍到人工确认、interrupt payload 带报告、定义位置、`editable=False` |
| `test_pipeline_definition.py`（增补 6 项） | `editable` 契约：默认可编辑、`editable: false` 把 Agent 阶段移出编辑面、显式关闭后不能再列为 `upstream_editable_stages`、`editable: true` 出现在 step / 非阶段上必须报错、`editable` 非布尔必须报错 |

**规则资产质量闸门**（防止 YAML 悄悄漂移，这些断言本身就是契约）：

- 词表每条都必须带 ≥6 字的替换建议，禁止「禁用」了事
- 每条 AI 味规则必须带 ≥8 字的改写建议，且引用的 metric 必须在注册表里
- 每个 pattern id 在 humanize prompt 里必须恰好出现一次
- 六维权重之和必须为 1

**P5 新增测试**：

| 文件 | 覆盖 |
|---|---|
| `backend/tests/test_channel_rules.py`（31 项） | **资产完整性**：七个守门字段齐全、取值合法、`require_*` 与显式策略方向一致、不用标签的渠道不配区间、用标签的必须有上限、标题区间必须有上限且有序、`max_chars > 0`。**显式值真的被读到**：`rules_from_channel` / `policy_from_channel` / `mode_from_channel` 回读等于 YAML 值；小红书单井号 vs 微博成对；上限截断；下限兜底补 1 个且进 `auto_fixed`；小红书 18-20 字标题区间生效（越界进 `needs_human`）；微博/产品页如实报「跳过长度合规判定」；两渠道之间不串味 |
| `evals/test_metrics.py`（6 → 15 项） | 上限来自 `channels.yaml`（断言 `CHANNEL_MAX_CHARS` 已不存在）；**只有 body 计上限**（标题/标签/notes 各 400 字仍通过）；超限报出 body 字数；上限随渠道变；`char_count_mode` 取自渠道（同一正文 raw 超限、no_whitespace 通过）；notes 不参与禁用词/硬约束/品牌名扫描；标题与标签**仍**参与；评测上限与守门报告 `max_chars` 一致；未知渠道报错而非退回默认值 |

## 失败处理

- **降档无解**（连钩子都放不下）：硬裁到上限，`fitted=False`，写入 `needs_human`，
  `passed=False`。宁可吵醒人，也不静默交付超限文案。
- **渠道没配 `max_chars`**：跳过降档。**不能把 0 传给 `downgrade_body`** ——
  它的语义是「预算为 0，什么都放不下」，会把正文清空。
- **humanize 改写失败/更差**：保留原文，记录原因，任务继续。
- **规则 YAML 语法错误**：`load_*` 在导入期抛错，由测试捕获；AI 味规则为空时显式
  `RuntimeError`，不静默放行。

## 验收标准

- P1：8 个新测试文件全绿（159 项）；**现有 93 项测试不受影响**（守门层只新增文件，不改图/API）。

  > **实测（沙箱环境）**：全量 252 项 → 248 通过 / 4 失败。4 项失败**全部**在 `tests/test_api.py`：
  >
  > - 根因一（当时判断）：`sqlite3.OperationalError: attempt to write a readonly database`、
  >   `Cannot operate on a closed database` —— 当时判断为「沙箱的文件写入限制 + aiosqlite 拆解竞争」。
  > - 根因二（时序）：`AssertionError: 任务 1 未在 1X 秒内…`、`httpx.ReadTimeout`。
  > - **失败集合在两次运行间会变化**（单跑失败 `test_task_full_flow`、`test_rerun_returns_immediately`；
  >   全量跑失败 `test_task_sse_and_stats`、`test_confirm_returns_immediately`）→ 当时据此判定
  >   「非确定性，属环境性 flaky，而非回归」。
  > - 判定依据：`tests/test_api.py` 及其依赖链（`app.main` / `app.api` …）**均不 import 守门层**，
  >   已用全目录检索确认 `guardrails` 仅出现在 `app/guardrails/` 自身内部。
  >   不 import 就不可能影响既有行为。
  > - 对照：排除 `test_api.py` 的其余 242 项（83 项既有 + 159 项新增）**全部通过，用时 13.9 秒**。
  >
  > **⚠️ 后记（已证伪）**：根因一的判断**是错的**。「失败集合随运行变化」不是「环境噪声」的证据，
  > 而是**竞态**的典型特征——恰恰说明有并发缺陷。真凶是 WAL 旁路文件竞态：
  > `db_sql` 每条语句开关一次连接，最后一个连接关闭时 SQLite 会删掉 `-wal`/`-shm`，
  > 并发线程重建失败即抛 `attempt to write a readonly database`。
  > 修复后 `tests/test_api.py` **连续 3 次 10/10 全过**。
  > 详见计划文档「故障修复：WAL 旁路文件竞态」一节。
  > （判定依据本身没问题：不 import 守门层确实说明**不是守门层引入的**；
  > 错的是从「不是本次改动引入」跳到「所以是环境问题」——它其实是**既有 bug**。）

- P2：`build_graph()` 新旧调用方式**行为完全一致**；新增 `seo_rewrite` 定义不需要改 `runner.py`。

  > **实测**：排除 `test_api.py` 的 296 项**全部通过**（13.1 秒），
  > 其中 93 项既有 + 159 项守门层 + 54 项流水线定义。
  > 行为保持由三重证据支撑：
  > ① 差分测试：省略 `pipeline_id` 与显式传 `"marketing"` 的运行结果逐字段相同；
  > ② 5 个硬断言 5 阶段集合的既有测试**未被修改且全部通过**
  > （`test_graph.py` / `test_nodes.py` / `test_demo_mode.py` / `test_reliability.py` / `test_api.py`）；
  > ③ `STAGE_KEYS` / `STAGE_LABELS` 改为从定义派生后，测试里的**字面量**断言仍成立。
- P3：新增守门节点后，未启用它的定义仍走原链路；interrupt 仍在 `human_confirm`。

  > **实测**：排除 `test_api.py` 的 **334 项全部通过**（15.5 秒）= 93 既有 + 159 守门层
  > + 63 流水线定义 + 29 守门节点（含 8 项 `split_user_words` 在内为 159+8=167 守门层相关）。
  > 前端 `tsc -b` 无错、`vitest run` 19/19 通过。
  > 「零 token」由「6 个阶段 / 5 次模型调用」的断言钉住；
  > 「只标记不改写」由「违禁词命中后正文一字不变」的断言钉住。
  >
  > **本阶段改动到的既有测试**（4 处，均为**预期改动**）：阶段集合从 5 变 6 ——
  > `test_api.py:109`、`test_graph.py:82`、`test_demo_mode.py:274`、
  > `test_pipeline_definition.py`（节点数 6→7、边数 7→8、版本 1→2）。
  > `test_nodes.py`、`test_reliability.py` **未被改动且继续通过**
  > （前者直接调工厂、后者只查 `review` 行）。

- P4：humanize 仅对公众号生效；复扫发现更差时**原文不丢**。

  > **实测**：排除 `tests/test_api.py` 的 **389 项全部通过**（16.9 秒）
  > = 83 既有 + 167 守门层 + 69 流水线定义 + 29 守门节点 + **41 去 AI 味**
  > （`test_api.py` 另有 10 项 → **总收集 399 项**）。
  > 前端 `tsc -b` 无错、`vitest run` **24/24 通过**。
  >
  > 关键不变式都由断言钉住：
  > ① **报告描述最终文案**——公众号任务下 `copywriting` 是改写稿、
  >   守门层 `ai_tell_stats.fatal == 0` 且 `passed is True`；
  > ② **失败永远保留原文**——模型抛异常 / 返回非 JSON / 返回空正文三种情况下
  >   `copywriting[0]["body"]` 与原文逐字节相同，且流水线仍到达人工确认点；
  > ③ **零 token**——非公众号链路仍是 7 阶段 / **5 次模型调用**，公众号 6 次；
  > ④ **改写不碰别的字段**——`notes` 与渠道专属字段原样保留。
  >
  > **本阶段改动到的既有测试**（均为**预期改动**）：阶段集合 6 → 7 ——
  > `test_api.py:109`、`test_graph.py:81`、`test_demo_mode.py:272`、
  > `test_guardrail_node.py`（阶段集合 + 位置断言改为以 humanize 为守门上游）、
  > `test_pipeline_definition.py`（节点 7→8、边 8→9、版本 2→3、阶段与温度字面量）。
  > `test_nodes.py`、`test_reliability.py` **未被改动且继续通过**。
  >
  > **~~`test_api.py` 复测证据（环境性 flaky，非回归）~~**（⚠️ **结论已证伪**，见下）：
  > 整文件跑两次均为 **2 failed / 8 passed**（`test_confirm_returns_immediately`、
  > `test_concurrent_tasks_finish_under_semaphore`）；
  > 但**把这两个用例单独跑**则 `test_confirm_returns_immediately` **通过**、
  > 只剩 `test_concurrent_tasks_finish_under_semaphore` 失败，根因直指
  > `sqlite3.ProgrammingError: Cannot operate on a closed database` 与
  > `aiosqlite ... attempt to write a readonly database` ——
  > 即**沙箱写入限制 + aiosqlite 拆解竞争**，与「并行/顺序」相关而与本次改动无关。
  > 对照：本阶段改动的 `test_task_full_flow`（阶段集合断言）**每次都通过**。
  >
  > **⚠️ 后记**：这里的**观察全对，解释全错**。「与并行/顺序相关」「单跑能过」
  > 正是竞态的标志，而不是环境噪声的标志。真凶是 WAL 旁路文件竞态，
  > 修复后 `tests/test_api.py` **10/10 连续通过**。
  > 详见计划文档「故障修复：WAL 旁路文件竞态」。
- P5：`evals/metrics.py` 与 `channels.yaml` 口径一致，且**改动前后基线差异有明确记录**
  （不是「基线通过了」）。

  > **实测**：排除 `tests/test_api.py` 的 **420 项全部通过**（18.2 秒）
  > = 83 既有 + 167 守门层 + 69 流水线定义 + 29 守门节点 + 41 去 AI 味 + **31 渠道规则**
  > （`test_api.py` 另有 10 项 → **总收集 430 项**）。
  > 评测套件 `evals/`：**32 项通过**（`test_metrics` 6 → 15、`test_dataset` 13、
  > `test_regression` 2、`test_run_eval` 2）。
  > 回归闸门实跑：`run_eval --provider fake` → `check_regression.py` 输出 `{"passed": true}`。
  >
  > **口径变化的量化（显式重跑，含「无差异」的诚实结论）**：
  > 方法不是「重跑一遍看通过没有」，而是拿**旧基线里已存的 30 条变体在新口径下重新打分**，
  > 逐项对比 `passed` 与 `reasons` —— 这样与 provider 无关，测的就是口径本身。
  > **结果：0 处差异**；三项 pass rate 改动前后都是 1.0；重生成基线后
  > `git diff` 只有 `generated_at` 一行变化。
  >
  > **这个「0」恰恰说明基线测不到这次改动**：fake fixture 的 body 只有 40 余字
  > （离 280 上限极远）、notes 不含禁用词，两条被修正的边界一个都没被触发。
  > 所以本轮**不把「基线通过」当作口径正确的证据**，证据是新增的 15 项口径用例
  > （见「测试设计」的 P5 表）。要让基线覆盖该维度，需要真实 provider 基线
  > 或专门的越界样本集 —— 记为**已知缺口**（本环境无 DeepSeek Key，未做）。
  >
  > **~~`test_api.py` 复测证据（环境性 flaky，非回归）~~**（⚠️ **结论已证伪**，见下）：
  > 仍是 **2 failed / 8 passed**（`test_confirm_returns_immediately`、
  > `test_concurrent_tasks_finish_under_semaphore`），与 P4 记录的失败集合**完全一致**；
  > 改动过的 `test_task_full_flow` 每次都通过。
  > 另记录一项新观察：`tests/test_feedback.py::test_feedback_api_flow` 在全量跑时
  > 偶发 `sqlite3.OperationalError`（当时判断为同类的沙箱写入限制），**单独跑必过**、重跑全量也过
  > —— 失败集合每次不同，当时判定「符合『环境性 flaky』的特征」。
  >
  > **⚠️ 后记**：`test_feedback_api_flow` 与 `test_api.py` 的失败**同源**，都是
  > WAL 旁路文件竞态。修复后两者都不再出现。
  > 教训：**反复出现、且根因已定位到具体异常类型（`readonly database`）的失败，
  > 不该被「失败集合每次不同」这个现象说服成环境噪声**——集合会变正是竞态的定义。

- P6（用户实测反馈引入）：并发写库不再产生 `attempt to write a readonly database`；
  任务失败必须**有原因、有终态**，不允许停留在 `pending`。

  > **实测**：同款并发场景（模拟前端 0.5s 轮询 + 8 个并发任务，并发上限 3）
  > **8/8 全部到达 `waiting_human`，0 异常**（修复前同场景必现失败）。
  > `tests/test_api.py` **连续 3 次 10/10 全过**（修复前恒定 2 失败）。
  > 后端排除 `test_api.py` **435 项全过**；`evals/` 32 项全过；前端 `tsc -b` + `vitest` 24 项全过。
  > 总收集 **445 项**。
  >
  > 新增测试 **12 项**：
  > - `tests/test_db_wal.py`（4）：锚点机制**确定性**回归（有锚点时 `-wal`/`-shm`
  >   在连接开关中存续；释放后回收行为恢复）、同步层单语句后旁路文件仍在、
  >   8 线程并发冒烟 0 异常、`hold()` 幂等与释放语义。
  > - `tests/test_reliability.py`（3 → 10）：`_mark_failed` 在状态写入失败时**不抛异常**、
  >   流水线首写失败时任务落 `failed` 且带原因、看门狗/恢复把原因写进 `tasks.error`、
  >   看门狗**不误杀** `pending`（合法排队态）、启动恢复处理 `pending` 孤儿。
  > - `tests/test_db_migration.py`：v4 迁移（`tasks.error` 默认空串）。
  >
  > 两处既有断言按预期更新：`test_db_migration.py` / `test_repository.py` 的迁移版本集合
  > **改为从 `db.MIGRATIONS` 派生**（新增迁移不再需要回来改测试，同时各自补上
  > 「v4 的 `error` 列就绪」的具体断言）。
  > `tests/test_reliability.py` 补了自带的建库 fixture——此前它**单跑必挂**
  > （`no such table: tasks`），只能靠别的模块先初始化、进程内共享同一个 data 目录。
