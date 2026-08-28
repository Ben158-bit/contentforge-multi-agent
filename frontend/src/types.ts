/** 与后端 API 对应的类型定义。 */

export type TaskStatus = 'pending' | 'running' | 'waiting_human' | 'completed' | 'failed'

export type StageKey = 'research' | 'competitor' | 'strategy' | 'copywriting' | 'review'

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
  status: 'pending' | 'running' | 'completed' | 'edited' | 'failed'
  output: Record<string, unknown> | null
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

export interface Task {
  id: number
  topic: string
  brand_name: string
  target_audience: string
  channel_id: string
  extra_requirements: string
  status: TaskStatus
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
