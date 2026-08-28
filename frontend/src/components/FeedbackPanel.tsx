import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ContentFeedback, FeedbackRule } from '../types'

interface Props {
  taskId: number
  brandId?: number | null
}

export default function FeedbackPanel({ taskId, brandId }: Props) {
  const [feedback, setFeedback] = useState<ContentFeedback | null>(null)
  const [rules, setRules] = useState<FeedbackRule[]>([])
  const [views, setViews] = useState('')
  const [conversions, setConversions] = useState('')
  const [score, setScore] = useState('')
  const [saved, setSaved] = useState(false)

  const load = async () => {
    try {
      const fb = await api.getFeedback(taskId)
      setFeedback(fb)
      setViews(String(fb.views))
      setConversions(String(fb.conversions))
      setScore(String(fb.score))
    } catch {
      setFeedback(null)
    }
    if (brandId) {
      api.listFeedbackRules(brandId).then(setRules).catch(() => undefined)
    } else {
      setRules([])
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, brandId])

  const save = async () => {
    await api.submitFeedback(taskId, {
      views: Number(views) || 0,
      conversions: Number(conversions) || 0,
      score: Math.min(5, Math.max(0, Number(score) || 0)),
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    void load()
  }

  const simulate = async () => {
    await api.simulateFeedback(taskId)
    void load()
  }

  const removeRule = async (ruleId: number) => {
    if (!brandId) return
    await api.deleteFeedbackRule(brandId, ruleId)
    void load()
  }

  return (
    <section className="card">
      <h2>效果回填</h2>
      <p className="muted">
        回填内容真实效果数据（或一键模拟），系统将提炼「什么内容有效」并用于后续生成。
      </p>
      <div className="feedback-form">
        <label>
          阅读量
          <input aria-label="阅读量" value={views} onChange={(e) => setViews(e.target.value)} />
        </label>
        <label>
          转化数
          <input aria-label="转化数" value={conversions} onChange={(e) => setConversions(e.target.value)} />
        </label>
        <label>
          效果分(0-5)
          <input aria-label="效果分" value={score} onChange={(e) => setScore(e.target.value)} />
        </label>
      </div>
      <div className="btn-row">
        <button className="btn small" onClick={() => void save()}>保存效果</button>
        <button className="btn small ghost" onClick={() => void simulate()}>一键模拟效果</button>
      </div>
      {saved && <span className="ok-hint">已保存</span>}
      {feedback && (
        <p className="muted">
          当前：阅读 {feedback.views} · 转化 {feedback.conversions} · 分 {feedback.score}
          {feedback.is_simulated === 1 ? '（模拟）' : ''}
        </p>
      )}
      {rules.length > 0 && (
        <div className="rules-list">
          <h3>已学到的效果规律</h3>
          <ul>
            {rules.map((r) => (
              <li key={r.id}>
                <span className={r.strength >= 0 ? 'rule-pos' : 'rule-neg'}>
                  {r.strength >= 0 ? '+' : ''}{r.strength}
                </span>{' '}
                {r.rule_text}{' '}
                <button className="btn small ghost" onClick={() => void removeRule(r.id)}>删除</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
