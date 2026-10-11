import { useCallback, useEffect, useRef, useState } from 'react'
import { api, subscribeTask } from '../api'
import type {
  Channel,
  CompetitorEvidence,
  CompetitorOutput,
  ContractCheck,
  CopyVariant,
  GuardrailReport,
  HumanizeReport,
  ReviewOutput,
  Stage,
  StrategyOutput,
  Task,
  VariantReport,
} from '../types'
import {
  humanizeDecisionLabel,
  parseVariants,
  Pipeline,
  StatusBadge,
  STAGES,
  EDITABLE_STAGES,
  formatTime,
  statusLabel,
  downstreamStageKeys,
  downstreamStageLabels,
  publishTags,
} from './Pipeline'
import FeedbackPanel from './FeedbackPanel'
import ConfirmButton from './ConfirmButton'

interface Props {
  taskId: number
}

/**
 * 去 AI 味报告渲染。
 *
 * 重点是把「**做了什么处置**」讲清楚：`rejected` / `failed` 都意味着
 * 交付的是原文 —— 这是产品承诺（降级而不是丢弃），用户必须看得见。
 */
function renderHumanize(report: HumanizeReport) {
  if (!report.enabled) {
    return (
      <p className="muted">
        本渠道未启用人味改写 · 仅公众号（wechat）启用
      </p>
    )
  }
  const stats = report.stats ?? {}
  const variants = report.variant_reports ?? []
  return (
    <div>
      <p className="muted">
        变体 {stats.variants ?? 0} 个 · 改写 {stats.rewrites ?? 0} 次 ·
        采纳 {stats.accepted ?? 0} / 保留原文 {(stats.rejected ?? 0) + (stats.failed ?? 0)}
      </p>
      {variants.length > 0 && (
        <ul className="kv-list">
          {variants.map((v) => (
            <li key={v.index}>
              <strong>
                变体{v.index + 1} · {humanizeDecisionLabel(v.decision)}
              </strong>
              <span>
                AI 味 {v.ai_tells_before} → {v.ai_tells_after} 处 · 正文{' '}
                {v.body_chars_before} → {v.body_chars_after} 字
                {v.changes?.length ? ` · ${v.changes.slice(0, 3).join('；')}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** 竞品证据的渠道中文名（前端不做渠道→数据源的映射，只做展示名）。 */
function channelLabel(channel: string): string {
  const map: Record<string, string> = {
    xiaohongshu: '小红书', wechat: '公众号', weibo: '微博', product_page: '电商产品页',
  }
  return map[channel] || channel
}

/** 互动数据格式化为「赞1.2w · 藏3k」这类紧凑形式（万以上用 w，千位下用原值）。 */
function describeMetrics(m?: CompetitorEvidence['metrics']): string {
  if (!m) return ''
  const fmt = (n?: number) =>
    n == null || n === 0 ? '' : n >= 10000 ? `${(n / 10000).toFixed(1).replace(/\.0$/, '')}w` : String(n)
  const parts: string[] = []
  const pairs: [keyof NonNullable<CompetitorEvidence['metrics']>, string][] = [
    ['likes', '赞'], ['collects', '藏'], ['comments', '评'], ['shares', '分享'],
    ['reads', '阅读'], ['interactions', '互动'], ['fans', '粉丝'],
  ]
  for (const [k, label] of pairs) {
    const v = fmt(m[k])
    if (v) parts.push(`${label}${v}`)
  }
  return parts.join(' ')
}

/**
 * 竞品分析阶段渲染。
 *
 * 重点是 `evidence`（数据源抓到的真实竞品实例）——竞品分析据此拆解，前端据此
 * 可溯源（真实链接 + 互动数据）。没有真实数据时明确说明「模型内部知识」，
 * 不假装有依据。竞品拆解结论与差异化机会点展示在真实实例之后。
 */
function renderCompetitor(out: CompetitorOutput) {
  const evidence = Array.isArray(out.evidence) ? out.evidence : []
  const competitors = Array.isArray(out.competitors) ? out.competitors : []
  const gaps = Array.isArray(out.gaps) ? out.gaps : []
  return (
    <div>
      {evidence.length > 0 ? (
        <div>
          <p className="muted">真实竞品实例 {evidence.length} 条（数据源抓取，点击可溯源）</p>
          <ul className="kv-list">
            {evidence.map((e, i) => (
              <li key={i}>
                <strong>
                  {channelLabel(e.channel)} · {e.title || '（无标题）'}
                  {e.verified ? ' ✓' : ''}
                </strong>
                <span>
                  {e.author ? `${e.author}` : ''}
                  {describeMetrics(e.metrics) ? ` · ${describeMetrics(e.metrics)}` : ''}
                </span>
                {e.link ? (
                  <a href={e.link} target="_blank" rel="noreferrer" className="source-link">
                    查看原文 ↗
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="muted">未配置真实竞品数据源 · 竞品为模型内部知识</p>
      )}
      {competitors.length > 0 ? (
        <div>
          <p className="muted">竞品拆解</p>
          <ul className="kv-list">
            {competitors.map((c, i) => (
              <li key={i}>
                <strong>{c.name || `竞品${i + 1}`}</strong>
                <span>
                  {c.positioning ? `${c.positioning}` : ''}
                  {c.selling_points?.length ? ` · 卖点：${c.selling_points.join('、')}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {gaps.length > 0 ? <p className="muted">机会点：{gaps.join('；')}</p> : null}
    </div>
  )
}

/**
 * 策略阶段渲染。
 *
 * 重点是 `content_structure`（渠道原生内容骨架）——它是「渠道差异前移到策略阶段」
 * 的落点，也是 copywriting 必须遵循的正文结构。骨架不可见的话，
 * 人工审核时根本没法判断文案是不是照着计划写的。
 */
function renderStrategy(out: StrategyOutput) {
  const steps = Array.isArray(out.content_structure) ? out.content_structure : []
  const angles = Array.isArray(out.content_angles) ? out.content_angles : []
  const messages = Array.isArray(out.key_messages) ? out.key_messages : []
  const hasCounts = messages.length > 0 || angles.length > 0
  return (
    <div>
      {out.core_value_proposition ? (
        <p className="muted">核心主张 · {out.core_value_proposition}</p>
      ) : null}
      {steps.length > 0 ? (
        <div className="structure-block">
          <p className="structure-head">内容骨架（正文必须按此顺序展开）</p>
          <ol className="structure-list">
            {steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="muted">本任务未给出内容骨架 · 创作阶段将退回渠道标准结构</p>
      )}
      {hasCounts ? (
        <p className="muted">
          {messages.length > 0 ? `关键信息 ${messages.length} 条` : ''}
          {messages.length > 0 && angles.length > 0 ? ' · ' : ''}
          {angles.length > 0 ? `内容角度 ${angles.length} 个` : ''}
        </p>
      ) : null}
    </div>
  )
}

/**
 * 审校阶段渲染。
 *
 * 重点是 `checklist`（逐项核对结果）——取代原来截断到 60 字符的通用 JSON。
 * 「渠道规范符合度清单」的项来自 `channels.yaml` 的 `review_checklist`
 * （判断力项：公式用没用对、风格像不像），与守门层的确定性口径互补。
 * 逐项 ✓/✕ 让审校结论可读、可复核。
 */
function renderReview(out: ReviewOutput) {
  const checklist = Array.isArray(out.checklist) ? out.checklist : []
  return (
    <div>
      <p className="muted">
        审校结论 · {out.passed ? '通过' : '不通过'}
        {checklist.length > 0 ? ` · 核对 ${checklist.length} 项` : ''}
      </p>
      {out.feedback ? <p className="muted review-feedback">{out.feedback}</p> : null}
      {checklist.length > 0 ? (
        <ul className="contract-list">
          {checklist.map((c, i) => (
            <li key={i} className={c.passed ? 'contract-ok' : 'contract-missing'}>
              <strong>
                {c.passed ? '✓' : '✕'} {c.item}
              </strong>
              {c.note ? <span className="muted">{c.note}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/** 把契约字段的值渲染成人能读的一行（字符串 / 数组 / 分层对象都兼容）。 */
function describeContractValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(describeContractValue).filter(Boolean).join('、')
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>)
      .map(describeContractValue)
      .filter(Boolean)
      .join(' ｜ ')
  }
  return String(value)
}

/**
 * 字符计数，对齐后端 raw 口径（`guardrails/text_metrics.py` 的 ``len(text)``）。
 *
 * 用 ``Array.from`` 按 code point 计，中文 / emoji 各算 1，与 Python 的 ``len``
 * 一致；不能用 ``s.length``（UTF-16 会把 emoji 等代理对算成 2，产生「编辑侧
 * 提示 40 字、守门层判 39 字」的口径漂移）。
 */
function charCount(s: string): number {
  return Array.from(s).length
}

/** 标题达标区间的可读文案；min==max==0 表示渠道未配置，返回空串（不显示提示）。 */
function titleRangeText(min: number, max: number): string {
  if (min > 0 && max > 0) return `${min}–${max} 字`
  if (max > 0) return `≤${max} 字`
  if (min > 0) return `≥${min} 字`
  return ''
}

/**
 * 渠道内容契约的逐项核验结果。
 *
 * 契约把渠道方法论从「提示词里的一句话」变成**可核验的字段**。
 * 这里的 `label` 由后端从 `channels.yaml` 带过来 —— 前端不自己维护
 * 「哪个渠道要哪些字段」的映射表，否则后端加了一条契约，前端还显示旧的。
 *
 * 缺口只标记、不自动补：守门层不会替模型写一个 `cta`，
 * 与「违禁词与 AI 味一律只标记，绝不静默改写」是同一条约定。
 */
function VariantContract({ checks, variant }: { checks: ContractCheck[]; variant: CopyVariant }) {
  if (checks.length === 0) return null
  const missing = checks.filter((c) => !c.ok)
  return (
    <div className="contract-block">
      <p className={missing.length > 0 ? 'contract-head contract-head-bad' : 'contract-head'}>
        渠道内容契约 {checks.length - missing.length}/{checks.length}
        {missing.length > 0 ? ' · 有未落实项' : ' · 全部落实'}
      </p>
      <ul className="contract-list">
        {checks.map((c) => (
          <li key={c.field} className={c.ok ? 'contract-ok' : 'contract-missing'}>
            <strong>
              {c.ok ? '✓' : '✕'} {c.label}
            </strong>
            <span>
              {c.ok ? describeContractValue(variant[c.field as keyof CopyVariant]) : c.detail}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 阶段状态的中文标签（徽章里显示）。 */
function stageBadgeLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '等待',
    running: '执行中',
    completed: '已完成',
    edited: '已编辑',
    failed: '失败',
    skipped: '已跳过',
  }
  return map[status] ?? status
}

/** severity 归一化：违禁词用 ``high``、AI 味用 ``fatal``，统一折算成三档。 */
function severityLevel(sev: string): 'fatal' | 'warning' | 'info' {
  if (sev === 'fatal' || sev === 'high') return 'fatal'
  if (sev === 'warning') return 'warning'
  return 'info'
}

function severityLabel(sev: string): string {
  const l = severityLevel(sev)
  return l === 'fatal' ? '致命' : l === 'warning' ? '警告' : '提示'
}

/** 定位到编辑器里对应变体的标题 / 正文 / 整卡，并给目标一个短暂高亮。 */
function scrollToVariantField(index: number, target: 'title' | 'body' | 'card') {
  const id = target === 'card' ? `variant-edit-${index}` : `variant-edit-${index}-${target}`
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.remove('locate-flash')
  void el.offsetWidth // 强制 reflow，让高亮动画能重复触发
  el.classList.add('locate-flash')
}

/** 单条可操作问题（由 ``VariantReport`` 的结构化字段重建，而非贴 ``needs_human`` 字符串）。 */
interface Issue {
  level: 'fatal' | 'warning' | 'info'
  /** 问题名：违禁词 / AI 味分类名 / 契约字段 label / 标题长度等 */
  label: string
  /** 命中位置（原文摘录） */
  context?: string
  /** 建议（怎么改） */
  suggestion?: string
  /** 点击「定位」跳到编辑器的哪个字段 */
  target: 'title' | 'body' | 'card'
}

/** 把一个变体的所有问题收集成可操作清单（按严重度降序）。 */
function collectIssues(report: VariantReport): Issue[] {
  const issues: Issue[] = []
  // 1) 违禁词（正文）
  for (const h of report.word_hits ?? []) {
    issues.push({
      level: severityLevel(h.severity),
      label: h.word,
      context: h.context,
      suggestion: h.suggestion,
      target: 'body',
    })
  }
  // 2) 标题违禁词
  for (const h of report.title?.forbidden_hits ?? []) {
    issues.push({
      level: severityLevel(h.severity),
      label: h.word,
      context: h.context,
      suggestion: h.suggestion,
      target: 'title',
    })
  }
  // 3) AI 味（正文）——分类名优先，命中片段给出「在哪」，建议给出「怎么改」
  for (const h of report.ai_tells ?? []) {
    issues.push({
      level: severityLevel(h.severity),
      label: h.category_label || h.pattern_id,
      context: h.samples?.length ? h.samples[0] : undefined,
      suggestion: h.suggestion,
      target: 'body',
    })
  }
  // 4) 标题 AI 味
  for (const h of report.title?.ai_tell_hits ?? []) {
    issues.push({
      level: severityLevel(h.severity),
      label: h.category_label || h.pattern_id,
      context: h.samples?.length ? h.samples[0] : undefined,
      suggestion: h.suggestion,
      target: 'title',
    })
  }
  // 5) 标题长度（空标题单独提示，避免「0 字」误导）
  const t = report.title
  if (t && !t.length_ok) {
    const range = titleRangeText(t.min_chars ?? 0, t.max_chars ?? 0)
    issues.push({
      level: 'warning',
      label: t.title === '' ? '标题为空' : `标题 ${t.length} 字${range ? `（达标 ${range}）` : ''}`,
      target: 'title',
    })
  }
  // 7) 字数处置（降档 / 硬裁 / 超限）
  if (report.over_limit) {
    issues.push({
      level: 'warning',
      label: `正文 ${report.body_chars} 字，仍超上限 ${report.max_chars} 字`,
      target: 'body',
    })
  }
  if (report.hard_cut) {
    issues.push({
      level: 'warning',
      label: report.downgrade_notes || '正文已硬裁至渠道上限',
      target: 'body',
    })
  } else if (report.downgraded) {
    issues.push({
      level: 'info',
      label: `正文超长，已降档 L${report.downgrade_level}`,
      suggestion: report.downgrade_notes,
      target: 'body',
    })
  }
  // 严重度降序：fatal 在前
  const order = { fatal: 0, warning: 1, info: 2 } as const
  issues.sort((a, b) => order[a.level] - order[b.level])
  return issues
}

/**
 * 单个变体的可操作体检卡。
 *
 * 每条问题都带：严重度徽章 + 问题名 + 命中位置（原文摘录）+ 怎么改（建议）+
 * 「定位」按钮（跳到编辑器对应字段）。相比之前的 ``needs_human`` 平铺字符串，
 * 用户能直接看到「哪里有问题、怎么改、点一下就能去改」。
 */
function VariantIssues({ report }: { report: VariantReport }) {
  const issues = collectIssues(report)
  const fatalCount = issues.filter((i) => i.level === 'fatal').length
  const warnCount = issues.filter((i) => i.level === 'warning').length
  const infoCount = issues.length - fatalCount - warnCount
  // 内容契约的逐项结果展示在编辑器的变体卡上（紧挨字段）；这里只保留通过比例。
  const contractTotal = report.contract?.length ?? 0
  const contractOk = report.contract?.filter((c) => c.ok).length ?? 0
  const clean = issues.length === 0 && (contractTotal === 0 || contractOk === contractTotal)
  return (
    <div className={`issue-card${clean ? ' issue-card-clean' : ''}`}>
      <p className="issue-head">
        变体{report.index + 1}
        {clean ? (
          <span className="issue-clean"> · ✓ 无问题</span>
        ) : (
          <span className="issue-summary">
            {fatalCount > 0 ? ` · ${fatalCount} 致命` : ''}
            {warnCount > 0 ? ` · ${warnCount} 警告` : ''}
            {infoCount > 0 ? ` · ${infoCount} 提示` : ''}
            {contractTotal > 0 ? ` · 内容契约 ${contractOk}/${contractTotal}` : ''}
          </span>
        )}
      </p>
      {issues.length > 0 && (
        <ul className="issue-list">
          {issues.map((iss, i) => (
            <li key={i} className={`issue-item issue-${iss.level}`}>
              <span className={`sev-badge sev-${iss.level}`}>{severityLabel(iss.level)}</span>
              <div className="issue-body">
                <strong>{iss.label}</strong>
                {iss.context ? <blockquote className="issue-context">{iss.context}</blockquote> : null}
                {iss.suggestion ? <span className="issue-suggestion">{iss.suggestion}</span> : null}
              </div>
              <button
                type="button"
                className="issue-locate"
                onClick={() => scrollToVariantField(report.index, iss.target)}
                title="定位到编辑器对应字段"
              >
                定位
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * 守门层报告渲染。
 *
 * 顺序是刻意的：先讲「结论 + 要你做什么」，再逐变体给出可操作问题清单，
 * 最后才是「工具自动修了什么」的留痕（好消息，放最后）。
 *
 * ``needs_human`` 不再平铺——它的每一项都能从 ``variant_reports`` 的结构化字段
 * （word_hits / ai_tells / title / contract / downgrade）重建，且重建后能带上
 * 命中位置、建议、并支持「定位到编辑器」。留痕（auto_fixed）单独收在底部。
 */
function renderGuardrail(report: GuardrailReport) {
  const stats = report.stats ?? {}
  const autoFixed = report.auto_fixed ?? []
  const variants = report.variant_reports ?? []
  const totalIssues = variants.reduce((n, v) => n + collectIssues(v).length, 0)
  return (
    <div>
      <p className="muted">
        {report.passed ? '✅ 确定性检查全部通过' : '⚠️ 有需人工处理的事项'} ·{' '}
        变体 {stats.variants ?? 0} 个 · 自动修正 {stats.auto_fixed ?? 0} 处
        {(stats.contract_gaps ?? 0) > 0 ? ` · 内容契约缺口 ${stats.contract_gaps} 处` : ''}
        {totalIssues > 0 ? ` · 待处理 ${totalIssues} 项` : ''}
      </p>
      {variants.length > 0 && variants.map((v) => <VariantIssues key={v.index} report={v} />)}
      {autoFixed.length > 0 && (
        <div className="autofixed-block">
          <p className="autofixed-head">已自动修正（留痕）</p>
          <ul className="kv-list">
            {autoFixed.slice(0, 5).map((note, i) => (
              <li key={i}>
                <span>{note}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function TaskDetail({ taskId }: Props) {
  const [task, setTask] = useState<Task | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [variants, setVariants] = useState<CopyVariant[]>([])
  const [editingStage, setEditingStage] = useState<Stage['stage_key'] | null>(null)
  const [stageDraft, setStageDraft] = useState('')
  const [brands, setBrands] = useState<{ id: number; name: string }[]>([])
  const [dirty, setDirty] = useState(false)
  const [learn, setLearn] = useState(false)
  const [learnBrandId, setLearnBrandId] = useState<number | ''>('')
  // 渠道切换：下拉选中新渠道后先挂起，确认条点「确认」才真正调接口重跑。
  const [channels, setChannels] = useState<Channel[]>([])
  const [pendingChannel, setPendingChannel] = useState('')
  const subscribedRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const t = await api.getTask(taskId)
      setTask(t)
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [taskId])

  // 首屏加载（后续由 SSE 实时推送；done 时重载一次）
  useEffect(() => {
    load()
    api.listBrands().then(setBrands).catch(() => undefined)
    // 渠道选项由后端 /api/channels 带来（含中文名），前端不维护渠道表
    api.getChannels().then(setChannels).catch(() => undefined)
  }, [load])

  // SSE 实时订阅
  useEffect(() => {
    if (subscribedRef.current) return
    subscribedRef.current = true
    return subscribeTask(taskId, (snap) => {
      setTask((prev) => {
        if (!prev) return prev
        const stageMap = new Map((prev.stages ?? []).map((s) => [s.stage_key, s]))
        for (const [key, status] of Object.entries(snap.stages)) {
          const st = stageMap.get(key as Stage['stage_key'])
          if (st) stageMap.set(key as Stage['stage_key'], { ...st, status: status as Stage['status'] })
        }
        return {
          ...prev,
          status: snap.status as Task['status'],
          total_cost: snap.total_cost,
          stages: Array.from(stageMap.values()),
        }
      })
    }, load)
  }, [taskId, load])

  // 编辑文案变体
  useEffect(() => {
    if (task && task.stages && variants.length === 0) {
      setVariants(parseVariants(task))
    }
  }, [task, variants.length])

  if (!task) {
    return <div className="page">{error ? <div className="error-box">{error}</div> : <p>加载中…</p>}</div>
  }

  const updateVariant = (index: number, field: keyof CopyVariant, value: string | string[]) => {
    setDirty(true)
    setVariants((prev) => prev.map((v, i) => (i === index ? { ...v, [field]: value } : v)))
  }

  const confirm = async () => {
    setBusy(true)
    try {
      const brandId = learn && learnBrandId !== '' ? Number(learnBrandId) : undefined
      await api.confirmTask(taskId, variants, learn && !!brandId, brandId)
      setDirty(false)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const rerun = async () => {
    setBusy(true)
    setError('')
    try {
      await api.rerunTask(taskId)
      setVariants([])
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // 切换发布渠道：后端会清空产出并按新渠道整条重跑（编辑器需重新初始化）。
  const changeChannel = async (channelId: string) => {
    setBusy(true)
    setError('')
    try {
      await api.updateTaskChannel(taskId, channelId)
      setPendingChannel('')
      setVariants([])
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const saveStageEdit = async (stageKey: Stage['stage_key']) => {
    try {
      await api.editStage(taskId, stageKey, { output: JSON.parse(stageDraft || '{}') })
      setEditingStage(null)
      await load()
    } catch (e) {
      setError(`编辑保存失败：${(e as Error).message}`)
    }
  }

  const renderStageOutput = (stage: Stage) => {
    const out = stage.output
    if (!out || Object.keys(out).length === 0) {
      return <p className="muted">暂无产出</p>
    }
    const objectOutput = Array.isArray(out) ? null : out
    // 调研阶段：展示联网检索来源（可溯源）
    if (stage.stage_key === 'research' && objectOutput && Array.isArray(objectOutput.sources) && objectOutput.sources.length > 0) {
      const sources = objectOutput.sources as { title?: string; url?: string }[]
      return (
        <div>
          <p className="muted research-summary">📎 依据 {sources.length} 个联网来源</p>
          <ul className="kv-list">
            {sources.slice(0, 5).map((s, i) => (
              <li key={i}>
                <a href={s.url} target="_blank" rel="noreferrer" className="source-link">
                  {s.title || s.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )
    }
    if (stage.stage_key === 'competitor' && objectOutput) {
      return renderCompetitor(objectOutput as unknown as CompetitorOutput)
    }
    if (stage.stage_key === 'guardrail') {
      return renderGuardrail(out as unknown as GuardrailReport)
    }
    if (stage.stage_key === 'strategy' && objectOutput) {
      return renderStrategy(objectOutput as unknown as StrategyOutput)
    }
    if (stage.stage_key === 'humanize') {
      return renderHumanize(out as unknown as HumanizeReport)
    }
    if (stage.stage_key === 'review' && objectOutput) {
      return renderReview(objectOutput as unknown as ReviewOutput)
    }
    if (stage.stage_key === 'copywriting') {
      const list = Array.isArray(out)
        ? (out as CopyVariant[])
        : Array.isArray(out.variants)
          ? (out.variants as CopyVariant[])
          : []
      return (
        <ul className="kv-list">
          {list.map((v, i) => (
            <li key={i}>
              <strong>{v.title}</strong>
              <span>{v.body.slice(0, 60)}…</span>
            </li>
          ))}
        </ul>
      )
    }
    const entries = Object.entries(out)
    return (
      <ul className="kv-list">
        {entries.slice(0, 4).map(([k, v]) => (
          <li key={k}>
            <strong>{k}</strong>
            <span>{typeof v === 'string' ? v : JSON.stringify(v).slice(0, 60)}</span>
          </li>
        ))}
      </ul>
    )
  }

  const waitingHuman = task.status === 'waiting_human'

  // 上游阶段被编辑（status === 'edited'）后，其下游产出已作废、将在确认时重跑。
  // 这里把所有「已编辑」阶段的下游合并成待重算集合，交给流水线可视化标出「待重算」。
  const staleKeys = new Set<Stage['stage_key']>()
  for (const s of task.stages ?? []) {
    if (s.status === 'edited') {
      for (const k of downstreamStageKeys(s.stage_key)) staleKeys.add(k)
    }
  }

  // 内容契约核验结果按变体序号对齐（来自守门层报告）。
  // label 由后端从 channels.yaml 带过来，前端不重复维护渠道字段表。
  const guardrailOutput = task.stages?.find((s) => s.stage_key === 'guardrail')?.output
  // 渠道话题标签风格（后端从 channels.yaml 带回），用于编辑器「可发布形态」预览。
  const hashtagStyle = (guardrailOutput as GuardrailReport | null)?.hashtag_style ?? ''
  // 渠道中文名：优先用 /api/channels 带回的名称，接口失败时退回静态映射。
  const channelName = (id: string) => channels.find((c) => c.id === id)?.name ?? channelLabel(id)
  const contractByIndex = new Map<number, ContractCheck[]>()
  // 整份守门报告按变体序号对齐：编辑器实时字数提示需要 title 达标区间
  // （min_chars / max_chars）与正文上限 max_chars —— 同样由后端带过来，不硬编码。
  const variantReportByIndex = new Map<number, VariantReport>()
  for (const report of (guardrailOutput as GuardrailReport | null)?.variant_reports ?? []) {
    if (report.contract?.length) contractByIndex.set(report.index, report.contract)
    variantReportByIndex.set(report.index, report)
  }

  return (
    <div className="page">
      {/* 任务头 */}
      <section className="card">
        <div className="task-head">
          <div>
            <h2>
              {task.topic}
              {task.brand_name && <span className="muted"> · {task.brand_name}</span>}
            </h2>
            <p className="muted task-meta">
              渠道：
              <select
                className="channel-select"
                value={pendingChannel || task.channel_id}
                onChange={(e) => {
                  const v = e.target.value
                  if (v !== task.channel_id) setPendingChannel(v)
                }}
                disabled={busy || task.status === 'running'}
                aria-label="切换发布渠道"
              >
                {(channels.length
                  ? channels
                  // 渠道列表加载失败时至少保住当前渠道可显示
                  : ([{ id: task.channel_id, name: task.channel_id, desc: '', tone: '', max_chars: 0 }] as Channel[])
                ).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name || c.id}
                  </option>
                ))}
              </select>{' '}
              · 目标受众：{task.target_audience || '未指定'} ·{' '}
              创建于 {formatTime(task.created_at)}
            </p>
            {pendingChannel && pendingChannel !== task.channel_id && (
              <div className="banner banner-human channel-change-bar" role="group">
                切换到「{channelName(pendingChannel)}」将清空当前产出并按新渠道整条重跑。
                <button
                  className="btn small danger"
                  onClick={() => changeChannel(pendingChannel)}
                  disabled={busy}
                >
                  确认切换并重跑
                </button>
                <button className="btn small ghost" onClick={() => setPendingChannel('')} disabled={busy}>
                  取消
                </button>
              </div>
            )}
          </div>
          <div className="task-head-right">
            <StatusBadge status={task.status} />
            <span className="sr-only" aria-live="polite" aria-atomic="true">
              {statusLabel(task.status)}
            </span>
            <div className="metrics">
              <span>成本 ¥{task.total_cost.toFixed(4)}</span>
              <span>耗时 {task.total_latency.toFixed(1)}s</span>
            </div>
            <ConfirmButton
              className="btn ghost"
              label="重新生成"
              question="重跑将覆盖当前产出"
              confirmLabel="确定重跑"
              onConfirm={rerun}
              disabled={busy || task.status === 'running'}
            />
          </div>
        </div>

        {/* 流水线可视化 */}
        <Pipeline stages={task.stages ?? []} status={task.status} stale={staleKeys} />

        {waitingHuman && (
          <div className="banner banner-human">
            ⏸ 流水线已暂停，等待你审阅并确认/编辑下方文案后继续。
          </div>
        )}
        {task.status === 'failed' && (
          <div className="banner banner-failed">
            ✕ 流水线执行失败{task.error ? `：${task.error}` : '（后端未记录原因，请查看服务日志）'}
            <span className="muted"> · 可点「重新生成」重试</span>
          </div>
        )}
        {error && <div className="error-box">{error}</div>}
      </section>

      {/* 阶段产出 */}
      <section className="stage-grid">
        {STAGES.map((s) => {
          const stage = task.stages?.find((x) => x.stage_key === s.key)
          return (
            <div className="card stage-card" key={s.key}>
              <div className="stage-card-head">
                <h3>{s.label}</h3>
                <span className={`badge badge-${stage?.status ?? 'pending'}`}>
                  {stageBadgeLabel(stage?.status ?? 'pending')}
                </span>
              </div>
              {stage ? renderStageOutput(stage) : <p className="muted">等待执行</p>}
              {/* copywriting 用下方的变体编辑器改，不在这里给通用 JSON 编辑框 */}
              {stage?.status === 'completed' &&
                s.key !== 'copywriting' &&
                EDITABLE_STAGES.includes(s.key) && (
                  <button className="btn small ghost" onClick={() => setEditingStage(s.key)}>
                    编辑
                  </button>
                )}
              {editingStage === s.key && (
                <div className="edit-area">
                  <p className="edit-impact">
                    {downstreamStageLabels(s.key).length > 0
                      ? `保存后将重新执行下游：${downstreamStageLabels(s.key).join(' → ')}`
                      : '本阶段为末端阶段，保存后无下游需重新执行'}
                  </p>
                  <textarea
                    rows={5}
                    value={stageDraft}
                    onChange={(e) => setStageDraft(e.target.value)}
                    placeholder={JSON.stringify(stage?.output ?? {}, null, 2)}
                  />
                  <div className="edit-actions">
                    <button className="btn small" onClick={() => saveStageEdit(s.key)}>
                      保存
                    </button>
                    <button className="btn small ghost" onClick={() => setEditingStage(null)}>
                      取消
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </section>

      {/* 文案变体编辑与确认 */}
      <section className="card">
        <h2>文案变体（可编辑后确认）</h2>
        {variants.length === 0 ? (
          <p className="muted">文案生成后显示于此。</p>
        ) : (
          <div className="variant-grid">
            {variants.map((v, i) => {
              const report = variantReportByIndex.get(i)
              const titleLen = charCount(v.title)
              const bodyLen = charCount(v.body)
              const tMin = report?.title?.min_chars ?? 0
              const tMax = report?.title?.max_chars ?? 0
              const rangeText = titleRangeText(tMin, tMax)
              // 标题越界 = 软提示（守门层才做最终判定）；正文超限 = 会被降档/硬裁
              const titleOver = rangeText !== '' && ((tMin > 0 && titleLen < tMin) || (tMax > 0 && titleLen > tMax))
              const maxChars = report?.max_chars ?? 0
              const bodyOver = maxChars > 0 && bodyLen > maxChars
              return (
                <div className="variant-card" id={`variant-edit-${i}`} key={i}>
                  <input
                    className="variant-title"
                    id={`variant-edit-${i}-title`}
                    value={v.title}
                    onChange={(e) => updateVariant(i, 'title', e.target.value)}
                    placeholder="标题"
                  />
                  {rangeText !== '' && (
                    <span className={titleOver ? 'char-hint char-hint-warn' : 'char-hint'}>
                      {`标题 ${titleLen} 字 · 达标 ${rangeText}${titleOver ? ' · 已越界' : ''}`}
                    </span>
                  )}
                  <textarea
                    rows={8}
                    id={`variant-edit-${i}-body`}
                    value={v.body}
                    onChange={(e) => updateVariant(i, 'body', e.target.value)}
                    placeholder="正文"
                  />
                  {maxChars > 0 && (
                    <span className={bodyOver ? 'char-hint char-hint-over' : 'char-hint'}>
                      {`正文 ${bodyLen}/${maxChars} 字${bodyOver ? ` · 超 ${bodyLen - maxChars} 字` : ''}`}
                    </span>
                  )}
                  <input
                    className="variant-tags"
                    value={(v.hashtags ?? []).join(' ')}
                    onChange={(e) =>
                      updateVariant(
                        i,
                        'hashtags',
                        e.target.value.split(/\s+/).filter(Boolean),
                      )
                    }
                    placeholder="话题标签（空格分隔）"
                  />
                  {hashtagStyle === 'none' ? (
                    <span className="tag-preview tag-preview-none">本渠道不使用话题标签</span>
                  ) : hashtagStyle ? (
                    <span className="tag-preview">{`发布形态：${publishTags(v.hashtags ?? [], hashtagStyle) || '（未填写标签）'}`}</span>
                  ) : null}
                  <VariantContract checks={contractByIndex.get(i) ?? []} variant={v} />
                </div>
              )
            })}
          </div>
        )}
        {waitingHuman && variants.length > 0 && (
          <div className="confirm-area">
            {dirty && (
              <label className="learn-option">
                <input
                  type="checkbox"
                  checked={learn}
                  onChange={(e) => setLearn(e.target.checked)}
                />
                <span>把本次修改记入品牌偏好（纠错学习）</span>
                {learn && (
                  <select value={learnBrandId} onChange={(e) => setLearnBrandId(e.target.value === '' ? '' : Number(e.target.value))}>
                    <option value="">选择品牌…</option>
                    {brands.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name}
                      </option>
                    ))}
                  </select>
                )}
              </label>
            )}
            <button className="btn primary" onClick={confirm} disabled={busy}>
              {busy ? '处理中…' : '✓ 确认定稿并完成'}
            </button>
          </div>
        )}
      </section>
      <FeedbackPanel taskId={taskId} brandId={learnBrandId === '' ? null : learnBrandId} />
    </div>
  )
}
