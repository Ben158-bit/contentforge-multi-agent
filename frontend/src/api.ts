/** 后端 API 客户端封装。 */

import type { Channel, CopyVariant, Stats, Task } from './types'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
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

  createTask: (payload: {
    topic: string
    channel_id: string
    brand_name?: string
    target_audience?: string
    extra_requirements?: string
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

  confirmTask: (taskId: number, variants: CopyVariant[]) =>
    request<Task>(`/api/tasks/${taskId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ variants }),
    }),

  rerunTask: (taskId: number) =>
    request<Task>(`/api/tasks/${taskId}/rerun`, { method: 'POST' }),
}

/** 订阅任务进度（SSE），返回取消函数。 */
export function subscribeTask(
  taskId: number,
  onSnapshot: (snapshot: { status: string; stages: Record<string, string>; total_cost: number }) => void,
  onDone?: () => void,
): () => void {
  const es = new EventSource(`/api/tasks/${taskId}/events`)
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
    // 服务端关闭或网络错误：由上层轮询兜底
    es.close()
  }
  return () => es.close()
}
