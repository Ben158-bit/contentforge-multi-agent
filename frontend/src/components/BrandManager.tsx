import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Brand } from '../types'

const EMPTY_FORM = { name: '', tone: '', core_claims: '', audience: '', taboos: '', notes: '' }

export default function BrandManager() {
  const [brands, setBrands] = useState<Brand[]>([])
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [detail, setDetail] = useState<Brand | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)

  const refresh = useCallback(async () => {
    try {
      setBrands(await api.listBrands())
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const startCreate = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  const startEdit = (brand: Brand) => {
    setEditingId(brand.id)
    setForm({
      name: brand.name, tone: brand.tone, core_claims: brand.core_claims,
      audience: brand.audience, taboos: brand.taboos, notes: brand.notes,
    })
  }

  const save = async () => {
    if (!form.name.trim()) {
      setError('请填写品牌名')
      return
    }
    try {
      if (editingId === null) {
        await api.createBrand(form)
      } else {
        await api.updateBrand(editingId, form)
      }
      setForm(EMPTY_FORM)
      setEditingId(null)
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = async (id: number) => {
    if (!window.confirm('删除品牌将同时删除其偏好规则，确定？')) return
    try {
      await api.deleteBrand(id)
      if (detail?.id === id) setDetail(null)
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const showDetail = async (id: number) => {
    try {
      setDetail(await api.getBrand(id))
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const set = (key: keyof typeof EMPTY_FORM, value: string) =>
    setForm((f) => ({ ...f, [key]: value }))

  return (
    <div className="page">
      <section className="card">
        <h2>品牌档案（记忆层）</h2>
        <p className="muted">
          创建品牌档案后，关联该品牌的任务会自动注入调性与卖点；确认时勾选「记入偏好」，
          系统会从你的修改中自动学习品牌内容偏好。
        </p>
        {error && <div className="error-box">{error}</div>}

        <div className="form-grid">
          <label className="field">
            <span>品牌名 *</span>
            <input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="例如：暖芯" />
          </label>
          <label className="field">
            <span>品牌调性</span>
            <input value={form.tone} onChange={(e) => set('tone', e.target.value)} placeholder="例如：温暖亲切、专业可信" />
          </label>
          <label className="field">
            <span>核心卖点/主张</span>
            <input value={form.core_claims} onChange={(e) => set('core_claims', e.target.value)} placeholder="例如：24 小时长效保温" />
          </label>
          <label className="field">
            <span>目标受众</span>
            <input value={form.audience} onChange={(e) => set('audience', e.target.value)} placeholder="例如：25-35 岁都市白领" />
          </label>
          <label className="field">
            <span>禁忌词/表达</span>
            <input value={form.taboos} onChange={(e) => set('taboos', e.target.value)} placeholder="例如：不夸大宣传、不用'促销'" />
          </label>
          <label className="field">
            <span>备注</span>
            <input value={form.notes} onChange={(e) => set('notes', e.target.value)} />
          </label>
        </div>
        <button className="btn primary" onClick={save}>
          {editingId === null ? '创建品牌' : '保存修改'}
        </button>
        {editingId !== null && (
          <button className="btn ghost" onClick={startCreate}>
            取消编辑
          </button>
        )}
      </section>

      <section className="card">
        <h2>品牌列表</h2>
        {brands.length === 0 ? (
          <p className="empty-hint">还没有品牌，创建第一个试试。</p>
        ) : (
          <table className="task-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>名称</th>
                <th>调性</th>
                <th>核心主张</th>
                <th>偏好数</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {brands.map((b) => (
                <tr key={b.id}>
                  <td>#{b.id}</td>
                  <td>
                    <button className="btn small ghost" onClick={() => showDetail(b.id)}>
                      {b.name}
                    </button>
                  </td>
                  <td>{b.tone}</td>
                  <td className="muted">{b.core_claims.slice(0, 20)}</td>
                  <td>{b.pref_count ?? 0}</td>
                  <td>
                    <button className="btn small ghost" onClick={() => startEdit(b)}>编辑</button>{' '}
                    <button className="btn small ghost" onClick={() => remove(b.id)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {detail && (
          <div className="card">
            <h3>「{detail.name}」已学到的内容偏好</h3>
            {detail.preferences && detail.preferences.length > 0 ? (
              <ul className="kv-list">
                {detail.preferences.map((p) => (
                  <li key={p.id}>
                    <strong>规则 #{p.id}{p.source_task ? `（来自任务 #${p.source_task}）` : ''}</strong>
                    <span>{p.rule_text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">暂无偏好规则——在任务确认时勾选「记入偏好」即可自动学习。</p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
