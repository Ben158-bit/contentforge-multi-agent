import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import { formatTime, parseVariants, statusLabel } from './Pipeline'

describe('formatTime', () => {
  it('把 UTC 时间转本地展示（YYYY-MM-DD HH:MM）', () => {
    const s = formatTime('2026-08-28 10:00:00')
    expect(s).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
  })

  it('空输入返回空串', () => {
    expect(formatTime('')).toBe('')
  })

  it('非法输入原样返回', () => {
    expect(formatTime('not-a-date')).toBe('not-a-date')
  })
})

describe('statusLabel', () => {
  it('映射各任务状态', () => {
    expect(statusLabel('waiting_human')).toBe('待人工确认')
    expect(statusLabel('completed')).toBe('已完成')
    expect(statusLabel('running')).toBe('执行中')
    expect(statusLabel('unknown' as never)).toBe('unknown')
  })
})

function baseTask(): Task {
  return {
    id: 1, topic: 't', channel_id: 'xiaohongshu', brand_name: '',
    target_audience: '', extra_requirements: '',
    status: 'waiting_human', total_cost: 0, total_latency: 0,
    created_at: '', updated_at: '',
  }
}

describe('parseVariants', () => {
  it('从 artifacts 解析对象变体', () => {
    const task: Task = {
      ...baseTask(),
      artifacts: [{
        id: 1, stage_key: 'copywriting', variant_index: 0,
        content: { title: '标题', body: '正文', hashtags: [], notes: '' },
        status: 'draft',
      }],
    }
    const v = parseVariants(task)
    expect(v[0].title).toBe('标题')
  })

  it('content 为 JSON 字符串时解析', () => {
    const task: Task = {
      ...baseTask(),
      artifacts: [{
        id: 1, stage_key: 'copywriting', variant_index: 0,
        content: '{"title":"T","body":"B","hashtags":[]}', status: 'draft',
      }],
    }
    const v = parseVariants(task)
    expect(v[0].title).toBe('T')
  })

  it('无工件时从 copywriting 阶段 output 兜底', () => {
    const task: Task = {
      ...baseTask(),
      stages: [{
        id: 1, stage_key: 'copywriting', status: 'completed',
        output: { variants: [{ title: '兜底', body: 'x' }] },
        feedback: '', revision_round: 0, cost: 0, latency: 0,
      }],
    }
    const v = parseVariants(task)
    expect(v[0].title).toBe('兜底')
  })

  it('无任何来源时返回空数组', () => {
    expect(parseVariants(baseTask())).toEqual([])
  })
})
