import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Brand, Channel, Stats, Task } from '../types'
import { StatusBadge, formatTime } from './Pipeline'

interface Props {
  onOpen: (taskId: number) => void
}

export default function TaskList({ onOpen }: Props) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [channels, setChannels] = useState<Channel[]>([])
  const [brands, setBrands] = useState<Brand[]>([])
  const [error, setError] = useState('')

  // 表单状态
  const [topic, setTopic] = useState('')
  const [channelId, setChannelId] = useState('xiaohongshu')
  const [brandId, setBrandId] = useState<number | ''>('')
  const [brandName, setBrandName] = useState('')
  const [audience, setAudience] = useState('')
  const [extra, setExtra] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([api.listTasks(), api.getStats()])
      setTasks(t)
      setStats(s)
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    refresh()
    api.getChannels().then(setChannels).catch((e) => setError((e as Error).message))
    api.listBrands().then(setBrands).catch(() => undefined)
  }, [refresh])

  const create = async () => {
    if (!topic.trim()) {
      setError('请填写营销主题')
      return
    }
    setSubmitting(true)
    try {
      await api.createTask({
        topic: topic.trim(),
        channel_id: channelId,
        brand_name: brandName.trim(),
        target_audience: audience.trim(),
        extra_requirements: extra.trim(),
        brand_id: brandId === '' ? undefined : Number(brandId),
      })
      setTopic('')
      setBrandName('')
      setAudience('')
      setExtra('')
      setBrandId('')
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const removeTask = async (task: Task) => {
    if (!window.confirm('确定删除任务“' + task.topic + '”吗？删除后无法恢复。')) return
    try {
      await api.deleteTask(task.id)
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="page">
      {/* 统计卡片 */}
      <div className="stats-row">
        <div className="stat-card">
          <strong>{stats?.task_count ?? '-'}</strong>
          <span>任务总数</span>
        </div>
        <div className="stat-card">
          <strong>{stats?.completed_count ?? '-'}</strong>
          <span>已完成</span>
        </div>
        <div className="stat-card">
          <strong>¥{(stats?.total_cost ?? 0).toFixed(4)}</strong>
          <span>累计 LLM 成本</span>
        </div>
        <div className="stat-card">
          <strong>{(stats?.avg_latency ?? 0).toFixed(1)}s</strong>
          <span>平均耗时</span>
        </div>
      </div>

      {/* 创建表单 */}
      <section className="card">
        <h2>新建营销任务</h2>
        <div className="form-grid">
          <label className="field field-wide">
            <span>营销主题 *</span>
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例如：智能保温杯新品上市推广"
            />
          </label>
          <label className="field">
            <span>目标渠道</span>
            <select value={channelId} onChange={(e) => setChannelId(e.target.value)}>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>品牌档案（记忆层）</span>
            <select value={brandId} onChange={(e) => setBrandId(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">不使用品牌档案</option>
              {brands.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>品牌 / 产品名</span>
            <input value={brandName} onChange={(e) => setBrandName(e.target.value)} placeholder="例如：暖芯" />
          </label>
          <label className="field">
            <span>目标受众</span>
            <input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder="例如：25-35 都市白领" />
          </label>
          <label className="field field-wide">
            <span>附加要求</span>
            <input
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              placeholder="例如：突出 24 小时长效保温，价格亲民"
            />
          </label>
        </div>
        <button className="btn primary" onClick={create} disabled={submitting}>
          {submitting ? '创建中…' : '启动多 Agent 流水线'}
        </button>
        {error && <div className="error-box">{error}</div>}
      </section>

      {/* 任务列表 */}
      <section className="card">
        <h2>历史任务</h2>
        {tasks.length === 0 ? (
          <p className="empty-hint">还没有任务，创建第一个试试。</p>
        ) : (
          <table className="task-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>主题</th>
                <th>渠道</th>
                <th>状态</th>
                <th>成本</th>
                <th>耗时</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr
                  key={t.id}
                  onClick={() => onOpen(t.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onOpen(t.id)
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`打开任务：${t.topic}`}
                  className="clickable"
                >
                  <td>#{t.id}</td>
                  <td>
                    <strong>{t.topic}</strong>
                    {t.brand_name && <span className="muted"> · {t.brand_name}</span>}
                  </td>
                  <td>{t.channel_id}</td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td>¥{t.total_cost.toFixed(4)}</td>
                  <td>{t.total_latency.toFixed(1)}s</td>
                  <td className="muted">{formatTime(t.created_at)}</td>
                  <td>
                    <button
                      className="btn small ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        void removeTask(t)
                      }}
                      aria-label={'删除任务：' + t.topic}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
