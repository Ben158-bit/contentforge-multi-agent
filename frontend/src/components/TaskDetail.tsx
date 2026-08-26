import { useCallback, useEffect, useRef, useState } from 'react'
import { api, subscribeTask } from '../api'
import type { CopyVariant, Stage, Task } from '../types'
import { parseVariants, Pipeline, StatusBadge, STAGES } from './Pipeline'

interface Props {
  taskId: number
}

export default function TaskDetail({ taskId }: Props) {
  const [task, setTask] = useState<Task | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [variants, setVariants] = useState<CopyVariant[]>([])
  const [editingStage, setEditingStage] = useState<Stage['stage_key'] | null>(null)
  const [stageDraft, setStageDraft] = useState('')
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

  // 首屏加载 + 轮询兜底（SSE 断开时）
  useEffect(() => {
    load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
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
    setVariants((prev) => prev.map((v, i) => (i === index ? { ...v, [field]: value } : v)))
  }

  const confirm = async () => {
    setBusy(true)
    try {
      await api.confirmTask(taskId, variants)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const rerun = async () => {
    if (!window.confirm('重新生成将覆盖当前产出，确定重跑整个流水线？')) return
    setBusy(true)
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
    if (stage.stage_key === 'copywriting') {
      const list = Array.isArray(out.variants) ? (out.variants as CopyVariant[]) : []
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
            <p className="muted">
              渠道：{task.channel_id} · 目标受众：{task.target_audience || '未指定'} ·{' '}
              创建于 {task.created_at}
            </p>
          </div>
          <div className="task-head-right">
            <StatusBadge status={task.status} />
            <div className="metrics">
              <span>成本 ¥{task.total_cost.toFixed(4)}</span>
              <span>耗时 {task.total_latency.toFixed(1)}s</span>
            </div>
            <button className="btn ghost" onClick={rerun} disabled={busy || task.status === 'running'}>
              重新生成
            </button>
          </div>
        </div>

        {/* 流水线可视化 */}
        <Pipeline stages={task.stages ?? []} status={task.status} />

        {waitingHuman && (
          <div className="banner banner-human">
            ⏸ 流水线已暂停，等待你审阅并确认/编辑下方文案后继续。
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
                  {stage?.status ?? 'pending'}
                </span>
              </div>
              {stage ? renderStageOutput(stage) : <p className="muted">等待执行</p>}
              {stage?.status === 'completed' && s.key !== 'copywriting' && (
                <button className="btn small ghost" onClick={() => setEditingStage(s.key)}>
                  编辑
                </button>
              )}
              {editingStage === s.key && (
                <div className="edit-area">
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
            {variants.map((v, i) => (
              <div className="variant-card" key={i}>
                <input
                  className="variant-title"
                  value={v.title}
                  onChange={(e) => updateVariant(i, 'title', e.target.value)}
                  placeholder="标题"
                />
                <textarea
                  rows={8}
                  value={v.body}
                  onChange={(e) => updateVariant(i, 'body', e.target.value)}
                  placeholder="正文"
                />
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
              </div>
            ))}
          </div>
        )}
        {waitingHuman && variants.length > 0 && (
          <button className="btn primary" onClick={confirm} disabled={busy}>
            {busy ? '处理中…' : '✓ 确认定稿并完成'}
          </button>
        )}
      </section>
    </div>
  )
}
