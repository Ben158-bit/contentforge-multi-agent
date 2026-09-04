/** 后端 API 客户端封装。 */

import type { Brand, Channel, ContentFeedback, CopyVariant, FeedbackRule, Stats, Task } from './types'

/** 构建时注入的 API Token（VITE_API_TOKEN），未配置时后端同样放行。 */
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`
  return headers
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: authHeaders(),
    ...options,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 忽略解析失败 */
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

export const api = {
  getChannels: () => request<Channel[]>('/api/channels'),
  getStats: () => request<Stats>('/api/stats'),

  listTasks: () => request<Task[]>('/api/tasks'),
  getTask: (id: number) => request<Task>(`/api/tasks/${id}`),
  deleteTask: (id: number) =>
    request<{ ok: boolean }>(`/api/tasks/${id}`, { method: 'DELETE' }),

  createTask: (payload: {
    topic: string
    channel_id: string
    brand_name?: string
    target_audience?: string
    extra_requirements?: string
    brand_id?: number
  }) =>
    request<Task>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  editStage: (taskId: number, stageKey: string, payload: Record<string, unknown>) =>
    request<Task>(`/api/tasks/${taskId}/stages/${stageKey}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  confirmTask: (taskId: number, variants: CopyVariant[], learn = false, brandId?: number) =>
    request<Task>(`/api/tasks/${taskId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ variants, learn, brand_id: brandId ?? null }),
    }),

  rerunTask: (taskId: number) =>
    request<Task>(`/api/tasks/${taskId}/rerun`, { method: 'POST' }),

  listBrands: () => request<Brand[]>('/api/brands'),
  getBrand: (id: number) => request<Brand>(`/api/brands/${id}`),
  createBrand: (payload: {
    name: string
    tone?: string
    core_claims?: string
    audience?: string
    taboos?: string
    notes?: string
  }) =>
    request<Brand>('/api/brands', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateBrand: (id: number, payload: Partial<Omit<Brand, 'id' | 'preferences'>>) =>
    request<Brand>(`/api/brands/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteBrand: (id: number) =>
    request<{ ok: boolean }>(`/api/brands/${id}`, { method: 'DELETE' }),

  // ---- 效果闭环 ----
  submitFeedback: (taskId: number, payload: { views: number; conversions: number; score: number; note?: string }) =>
    request<ContentFeedback>(`/api/tasks/${taskId}/feedback`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  simulateFeedback: (taskId: number) =>
    request<ContentFeedback>(`/api/tasks/${taskId}/feedback/simulate`, { method: 'POST' }),
  getFeedback: (taskId: number) => request<ContentFeedback>(`/api/tasks/${taskId}/feedback`),
  listFeedbackRules: (brandId: number) => request<FeedbackRule[]>(`/api/brands/${brandId}/feedback-rules`),
  deleteFeedbackRule: (brandId: number, ruleId: number) =>
    request<{ ok: boolean }>(`/api/brands/${brandId}/feedback-rules/${ruleId}`, { method: 'DELETE' }),
}

/** 订阅任务进度（SSE），返回取消函数。EventSource 无法自定义头，token 走 query 参数。 */
export function subscribeTask(
  taskId: number,
  onSnapshot: (snapshot: { status: string; stages: Record<string, string>; total_cost: number }) => void,
  onDone?: () => void,
): () => void {
  const query = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : ''
  const es = new EventSource(`/api/tasks/${taskId}/events${query}`)
  es.onmessage = (ev) => {
    try {
      onSnapshot(JSON.parse(ev.data))
    } catch {
      /* 忽略解析失败 */
    }
  }
  es.addEventListener('done', () => {
    onDone?.()
    es.close()
  })
  es.onerror = () => {
    // 服务端关闭或网络错误：EventSource 会自动重连，仅由上层决定是否关闭
    if (es.readyState === EventSource.CLOSED) return
  }
  return () => es.close()
}
