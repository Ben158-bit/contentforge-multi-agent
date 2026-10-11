import type { CopyVariant, GuardrailReport, HumanizeReport, Stage, Task } from '../types'

/** 流水线阶段定义（对应后端流水线定义里 is_stage: true 的节点顺序）。 */
export const STAGES: { key: Stage['stage_key']; label: string; desc: string }[] = [
  { key: 'research', label: '市场调研', desc: '行业趋势 · 受众洞察 · 痛点关键词' },
  { key: 'competitor', label: '竞品分析', desc: '竞品定位 · 卖点拆解 · 机会点' },
  { key: 'strategy', label: '策略制定', desc: '目标受众 · 核心主张 · 内容角度' },
  { key: 'copywriting', label: '文案创作', desc: '多版本文案生成（可编辑）' },
  { key: 'humanize', label: '去 AI 味', desc: '改写套话句式 · 仅公众号启用' },
  { key: 'guardrail', label: '质量守门', desc: '字数 · 违禁词 · 标签 · AI 味（确定性核验）' },
  { key: 'review', label: '审校优化', desc: '对照策略检查 · 不通过自动打回' },
]

/**
 * 可人工编辑的阶段：产出即交付物的阶段。
 * 工具产出（守门报告）与「产出是处置报告、真正改的是 copywriting」的去 AI 味
 * 都不开放手改 —— 后者的编辑根本不会回填，是个静默失效的入口。
 */
export const EDITABLE_STAGES: Stage['stage_key'][] = STAGES
  .map((s) => s.key)
  .filter((key) => key !== 'guardrail' && key !== 'humanize')

/**
 * 某阶段的**下游阶段**（按流水线执行顺序，其后所有 is_stage 节点）。
 *
 * 这是「编辑阶段 → 下游影响可视化」的唯一真源：编辑一个上游阶段后，
 * 后端在确认时会把执行位置重定位到该阶段之后、重跑其下游（见 runner 的
 * `update_pipeline_state(..., as_node=earliest)`），因此下游当前产出会作废。
 * 前端据此告诉用户「改这里会连带重算哪些阶段」。
 */
export function downstreamStageKeys(key: Stage['stage_key']): Stage['stage_key'][] {
  const idx = STAGES.findIndex((s) => s.key === key)
  if (idx < 0) return []
  return STAGES.slice(idx + 1).map((s) => s.key)
}

/** 某阶段下游阶段的中文名（用于「保存后将重新执行下游：X → Y → Z」提示）。 */
export function downstreamStageLabels(key: Stage['stage_key']): string[] {
  const idx = STAGES.findIndex((s) => s.key === key)
  if (idx < 0) return []
  return STAGES.slice(idx + 1).map((s) => s.label)
}

/** 阶段状态的中文说明（流水线卡片里显示在阶段名下方）。 */
export function stageStateLabel(state: string, desc: string): string {
  if (state === 'completed') return '已完成'
  if (state === 'edited') return '已人工编辑'
  if (state === 'skipped') return '已跳过（本渠道不适用）'
  if (state === 'failed') return '失败'
  if (state === 'running') return '执行中'
  return desc
}

export function statusLabel(status: Task['status']): string {
  const map: Record<string, string> = {
    pending: '排队中',
    running: '执行中',
    waiting_human: '待人工确认',
    completed: '已完成',
    failed: '失败',
  }
  return map[status] ?? status
}

/** 把后端返回的 UTC 时间字符串（YYYY-MM-DD HH:MM:SS）转换为本地时间展示。 */
export function formatTime(utcStr: string): string {
  if (!utcStr) return ''
  const d = new Date(utcStr.replace(' ', 'T') + 'Z')
  if (Number.isNaN(d.getTime())) return utcStr
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 任务状态徽章。 */
export function StatusBadge({ status }: { status: Task['status'] }) {
  return <span className={`badge badge-${status}`}>{statusLabel(status)}</span>
}

/** 流水线进度可视化：各阶段状态灯 + 连接线（阶段来自 STAGES）。 */
export function Pipeline({
  stages,
  status,
  stale,
}: {
  stages: Stage[]
  status: Task['status']
  /** 上游被编辑后、产出已作废待重算的下游阶段（节点用「待重算」标出）。 */
  stale?: Set<Stage['stage_key']>
}) {
  const stageMap = new Map(stages.map((s) => [s.stage_key, s]))

  return (
    <div className="pipeline">
      {STAGES.map((s, i) => {
        const stage = stageMap.get(s.key)
        const state = stage?.status ?? 'pending'
        // skipped 也视为「这一步不必再等」，否则流水线上会出现一个断点
        const settled = state === 'completed' || state === 'skipped'
        const isStale = stale?.has(s.key) ?? false
        return (
          <div key={s.key} className="pipeline-step">
            <div className={`pipeline-node node-${state}${isStale ? ' node-stale' : ''}`}>
              <span className="pipeline-index">{i + 1}</span>
            </div>
            <div className="pipeline-label">
              <strong>{s.label}</strong>
              <small className={isStale ? 'stale-hint' : undefined}>
                {isStale ? '待重算' : stageStateLabel(state, s.desc)}
              </small>
            </div>
            {i < STAGES.length - 1 && (
              <div className={`pipeline-line line-${settled ? 'done' : 'idle'}`} />
            )}
          </div>
        )
      })}
      <div className={`pipeline-node node-${status === 'completed' ? 'completed' : 'idle'}`}>
        <span className="pipeline-index">✓</span>
      </div>
    </div>
  )
}

/** 把后端工件（JSON 字符串或对象）解析为文案变体。 */
export function parseVariants(task: Task): CopyVariant[] {
  const raw = task.artifacts?.filter((a) => a.stage_key === 'copywriting') ?? []
  if (raw.length > 0) {
    return raw.map((a) => {
      const c = a.content
      if (typeof c === 'string') {
        try {
          return JSON.parse(c) as CopyVariant
        } catch {
          return { title: `变体 ${a.variant_index + 1}`, body: c }
        }
      }
      return c as CopyVariant
    })
  }
  // 兜底：从阶段 output 读取
  const copyStage = task.stages?.find((s) => s.stage_key === 'copywriting')
  const output = copyStage?.output
  if (Array.isArray(output)) return output as CopyVariant[]
  if (!output || typeof output !== 'object') return []
  const variants = output.variants
  return Array.isArray(variants) ? (variants as CopyVariant[]) : []
}

/** 从任务里取出守门层报告；没有该阶段（流水线未启用守门）时返回 null。 */
export function parseGuardrailReport(task: Task): GuardrailReport | null {
  const stage = task.stages?.find((s) => s.stage_key === 'guardrail')
  const output = stage?.output
  if (!output || typeof output !== 'object' || Array.isArray(output)) return null
  return output as unknown as GuardrailReport
}

/** 从任务里取出「去 AI 味」报告；没有该阶段时返回 null。 */
export function parseHumanizeReport(task: Task): HumanizeReport | null {
  const stage = task.stages?.find((s) => s.stage_key === 'humanize')
  const output = stage?.output
  if (!output || typeof output !== 'object' || Array.isArray(output)) return null
  return output as unknown as HumanizeReport
}

/** 单条变体的处置说明（给用户看的一句话）。 */
export function humanizeDecisionLabel(decision: string): string {
  const map: Record<string, string> = {
    accepted: '已采纳改写',
    rejected: '改写未通过验收，保留原文',
    skipped: '无需改写',
    failed: '改写失败，保留原文',
  }
  return map[decision] ?? decision
}

/** 去掉标签首尾的井号（全角＃也算），对齐后端 ``normalize_tag`` 的 ``strip(_HASH_CHARS)``。 */
function stripHashes(s: string): string {
  return s.replace(/^[#＃]+/, '').replace(/[#＃]+$/, '').trim()
}

/**
 * 把原始标签按渠道风格渲染成「可发布形态」。
 *
 * ``style`` 由后端从 ``channels.yaml`` 带回（``paired``=#话题# / ``hash``=#话题 /
 * ``none``=不使用），前端只做**纯格式渲染**，不自己维护「哪个渠道用哪种样式」的
 * 映射表（与契约 label、标题区间、AI 味中文名同一条铁律）。空串 / none 返回空串。
 */
export function publishTags(tags: string[], style: string): string {
  if (!style || style === 'none') return ''
  const cleaned = tags.map(stripHashes).filter(Boolean)
  if (cleaned.length === 0) return ''
  if (style === 'paired') return cleaned.map((t) => `#${t}#`).join(' ')
  if (style === 'hash') return cleaned.map((t) => `#${t}`).join(' ')
  return cleaned.join(' ')
}
