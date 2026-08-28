import type { CopyVariant, Stage, Task } from '../types'

/** 流水线阶段定义（与后端 STAGE_KEYS 对应）。 */
export const STAGES: { key: Stage['stage_key']; label: string; desc: string }[] = [
  { key: 'research', label: '市场调研', desc: '行业趋势 · 受众洞察 · 痛点关键词' },
  { key: 'competitor', label: '竞品分析', desc: '竞品定位 · 卖点拆解 · 机会点' },
  { key: 'strategy', label: '策略制定', desc: '目标受众 · 核心主张 · 内容角度' },
  { key: 'copywriting', label: '文案创作', desc: '多版本文案生成（可编辑）' },
  { key: 'review', label: '审校优化', desc: '对照策略检查 · 不通过自动打回' },
]

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

/** 流水线进度可视化：5 个阶段状态灯 + 连接线。 */
export function Pipeline({ stages, status }: { stages: Stage[]; status: Task['status'] }) {
  const stageMap = new Map(stages.map((s) => [s.stage_key, s]))

  return (
    <div className="pipeline">
      {STAGES.map((s, i) => {
        const stage = stageMap.get(s.key)
        const state = stage?.status ?? 'pending'
        return (
          <div key={s.key} className="pipeline-step">
            <div className={`pipeline-node node-${state}`}>
              <span className="pipeline-index">{i + 1}</span>
            </div>
            <div className="pipeline-label">
              <strong>{s.label}</strong>
              <small>{state === 'completed' ? '已完成' : state === 'edited' ? '已人工编辑' : s.desc}</small>
            </div>
            {i < STAGES.length - 1 && (
              <div className={`pipeline-line line-${state === 'completed' ? 'done' : 'idle'}`} />
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
  const variants = copyStage?.output?.variants
  return Array.isArray(variants) ? (variants as CopyVariant[]) : []
}
