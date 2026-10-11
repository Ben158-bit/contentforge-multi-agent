/** 与后端 API 对应的类型定义。 */

export type TaskStatus = 'pending' | 'running' | 'waiting_human' | 'completed' | 'failed'

export type StageKey =
  | 'research'
  | 'competitor'
  | 'strategy'
  | 'copywriting'
  | 'humanize' // 去 AI 味改写（后端 kind: agent，仅公众号真正调用模型）
  | 'guardrail' // 确定性守门（后端 kind: step，不调模型）
  | 'review'

export interface Channel {
  id: string
  name: string
  desc: string
  tone: string
  max_chars: number
}

export interface Stage {
  id: number
  stage_key: StageKey
  // skipped：阶段跑了但判定「本渠道不适用」（如非公众号的去 AI 味）——
  // 与 completed 区分开，避免把「什么都没做」显示成「已完成」
  status: 'pending' | 'running' | 'completed' | 'edited' | 'failed' | 'skipped'
  output: Record<string, unknown> | unknown[] | null
  feedback: string
  revision_round: number
  cost: number
  latency: number
}

export interface Artifact {
  id: number
  stage_key: string
  variant_index: number
  content: unknown
  status: 'draft' | 'approved'
}

/**
 * 内容契约的单条核验结果（后端 guardrails/content_contract.py）。
 *
 * `label` 是**后端从 channels.yaml 带过来的** —— 前端刻意不自己维护一份
 * 「哪个渠道要哪些字段」的映射表，否则后端加了一条契约，前端还显示旧的。
 * 契约把渠道方法论从「提示词里的一句话」变成「可核验的字段」。
 */
export interface ContractCheck {
  field: string
  label: string
  kind: string
  /** false = 缺口（缺字段 / 枚举越界 / 数量不符 / 小标题没出现在正文里） */
  ok: boolean
  detail: string
}

/**
 * 违禁词命中（后端 guardrails/compliance.py 的 WordHit）。
 *
 * `context` 是命中位置（原文摘录）、`suggestion` 是替换建议、`basis` 是判定依据
 * —— 三者合起来构成「哪里有问题、怎么改、为什么」的可操作信息。
 */
export interface WordHit {
  scope: string
  category: string
  word: string
  severity: string
  context?: string
  suggestion: string
  basis?: string
}

/**
 * AI 味命中（后端 guardrails/ai_tells.py 的 AiTellHit）。
 *
 * `category_label` 是中文分类名（如「语言与句式」），由后端从规则资产的
 * categories 节带过来 —— 前端不自己维护 category id → 中文 的映射。
 * `samples` 是命中原文片段，`suggestion` 是改写建议。
 */
export interface AiTellHit {
  pattern_id: string
  category: string
  category_label?: string
  severity: string
  count: number
  suggestion?: string
  samples?: string[]
}

/**
 * 标题体检结果（与后端 guardrails/report.py 的 TitleCheckModel 同构）。
 *
 * `min_chars` / `max_chars` 是**渠道标题达标区间**，由后端从 `channels.yaml`
 * 带过来 —— 前端编辑器据此做实时字数提示，不自己硬编码渠道区间表。
 * 二者均为 0 表示该渠道未配置长度区间（如微博、产品页），此时不显示区间提示。
 * `forbidden_hits` / `ai_tell_hits` 是标题里的违禁词 / AI 味命中。
 */
export interface TitleCheck {
  title: string
  length: number
  length_ok: boolean
  min_chars?: number
  max_chars?: number
  grade: string
  grade_note?: string
  forbidden_hits?: WordHit[]
  ai_tell_hits?: AiTellHit[]
}

/** 守门层单条变体的体检结果（与后端 guardrails/report.py 同构）。 */
export interface VariantReport {
  index: number
  body_chars: number
  max_chars: number
  over_limit: boolean
  hard_cut: boolean
  downgraded: boolean
  downgrade_level: number
  downgrade_notes: string
  dropped: string[]
  hashtags_before: string[]
  hashtags_after: string[]
  hashtag_notes: string[]
  emoji_before: number
  emoji_after: number
  emoji_notes: string[]
  word_hits: WordHit[]
  ai_tells: AiTellHit[]
  title: TitleCheck | null
  /** 内容契约核验结果；渠道未声明契约时为空数组（= 不核验，不是通过） */
  contract: ContractCheck[]
}

/**
 * 确定性守门层报告（后端 stages.output.guardrail）。
 *
 * `passed` 只看成稿硬约束；`source_hits` 是输入素材自带的命中，仅展示。
 * `needs_human` 是**必须人工处理**的事项——工具只标记、不静默改写。
 */
export interface GuardrailReport {
  channel_id: string
  max_chars: number
  /** 渠道话题标签风格（后端从 channels.yaml 带回，经缺省推断的最终值）。 */
  hashtag_style?: string
  variant_reports: VariantReport[]
  draft_hits: WordHit[]
  source_hits: WordHit[]
  passed: boolean
  auto_fixed: string[]
  needs_human: string[]
  /** 内容契约缺口汇总。**只标记，不影响 passed** —— passed 表达硬合规 */
  contract_gaps: string[]
  stats: Record<string, number>
  ai_tell_stats: Record<string, number>
}

/** 去 AI 味（humanize）单条变体的处置记录（与后端 HumanizeVariantReport 同构）。 */
export interface HumanizeVariantReport {
  index: number
  /** accepted=采纳改写 / rejected=未过验收保留原文 / skipped=无需改写 / failed=调用失败保留原文 */
  decision: 'accepted' | 'rejected' | 'skipped' | 'failed'
  reason: string
  ai_tells_before: number
  ai_tells_after: number
  fatal_before: number
  fatal_after: number
  body_chars_before: number
  body_chars_after: number
  changes: string[]
}

/**
 * 去 AI 味报告（后端 stages.output.humanize）。
 *
 * `enabled=false` 表示该渠道未启用（不消耗 token）；`decision` 里的
 * `rejected` / `failed` 都意味着**交付的是原文** —— 降级而不是丢弃。
 */
export interface HumanizeReport {
  enabled: boolean
  channel_id: string
  reason: string
  variant_reports: HumanizeVariantReport[]
  stats: Record<string, number>
}

/**
 * 策略阶段产出（后端 stages.output.strategy）。
 *
 * `content_structure` 是**该渠道原生内容骨架**：策略阶段依据
 * `channels.yaml` 的 `structure` 适配本任务后给出的有序步骤，
 * 下游 copywriting 必须按它展开正文，review 要核对是否被遵守。
 * 展示它是必要的 —— 人工看不到骨架，就没法判断文案是不是照着计划写的。
 */
export interface StrategyOutput {
  target_audience?: string
  core_value_proposition?: string
  key_messages?: string[]
  tone_guidance?: string
  content_angles?: string[]
  content_structure?: string[]
  channel_strategy?: string
  /** 审校打回时模型补充的字段，展示时忽略 */
  [key: string]: unknown
}

/**
 * 审校阶段产出（后端 stages.output.review）。
 *
 * `checklist` 是审校专家**逐项核对**的结果：每项含 item（检查项）、passed（是否通过）、
 * note（判断依据）。「渠道规范符合度清单」的项来自 `channels.yaml` 的
 * `review_checklist`（判断力项：公式用没用对、风格像不像），
 * 与守门层的确定性口径（字数 / 违禁词 / AI 味）互补不重复。
 */
export interface ReviewChecklistItem {
  item: string
  passed: boolean
  note?: string
}

export interface ReviewOutput {
  passed?: boolean
  feedback?: string
  checklist?: ReviewChecklistItem[]
  [key: string]: unknown
}

/**
 * 竞品分析阶段产出（后端 stages.output.competitor）。
 *
 * `evidence` 是竞品数据源（红狐 Hub）抓到的**真实竞品实例**（小红书笔记 /
 * 公众号文章 / 微博账号），附真实互动数据与链接 —— 竞品分析据此拆解，前端据此
 * 可溯源。未配置数据源 / 抓取失败时该字段缺省，竞品分析退回模型内部知识。
 */
export interface CompetitorEvidence {
  channel: string
  kind: string
  title: string
  summary?: string
  author?: string
  metrics?: {
    likes?: number
    collects?: number
    comments?: number
    shares?: number
    reads?: number
    interactions?: number
    fans?: number
  }
  link?: string
  published_at?: string
  verified?: boolean
}

export interface CompetitorItem {
  name?: string
  positioning?: string
  selling_points?: string[]
  content_strategy?: string
  strengths?: string[]
  weaknesses?: string[]
}

export interface CompetitorOutput {
  competitors?: CompetitorItem[]
  gaps?: string[]
  evidence?: CompetitorEvidence[]
  [key: string]: unknown
}

export interface Task {
  id: number
  topic: string
  brand_name: string
  target_audience: string
  channel_id: string
  extra_requirements: string
  status: TaskStatus
  /** 失败原因（仅 status === 'failed' 时有值；由后端写入任务行） */
  error?: string
  total_cost: number
  total_latency: number
  created_at: string
  updated_at: string
  stages?: Stage[]
  artifacts?: Artifact[]
}

export interface CopyVariant {
  title: string
  body: string
  hashtags?: string[]
  notes?: string
  // ---- 渠道专属字段（各渠道内容契约的核验对象，见后端 channels.yaml）----
  // 小红书
  hook_type?: string
  scenario?: string
  experience_detail?: string
  tags_by_tier?: { hot?: string[]; longtail?: string[] }
  // 公众号
  outline?: string[]
  section_headings?: string[]
  key_takeaway?: string
  evidence_refs?: string[]
  // 微博
  reasoning_chain?: string
  proof_used?: string[]
  risk_flags?: string[]
  // 通用
  cta?: string
  // 电商产品页
  selling_points?: {
    label: string
    feature: string
    advantage: string
    benefit: string
    proof: string
  }[]
  specifications?: { name: string; value: string }[]
  trust_badges?: string[]
}

export interface Stats {
  task_count: number
  completed_count: number
  total_cost: number
  total_latency: number
  avg_cost: number
  avg_latency: number
}

export interface Brand {
  id: number
  name: string
  tone: string
  core_claims: string
  audience: string
  taboos: string
  notes: string
  pref_count?: number
  preferences?: { id: number; rule_text: string; source_task: number | null; created_at: string }[]
}

export interface ContentFeedback {
  content_id: number
  views: number
  conversions: number
  score: number
  is_simulated: number
  note: string
  created_at: string
}

export interface FeedbackRule {
  id: number
  rule_text: string
  strength: number
  created_at: string
}
