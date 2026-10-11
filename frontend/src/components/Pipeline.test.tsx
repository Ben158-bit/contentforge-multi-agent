import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import {
  EDITABLE_STAGES,
  formatTime,
  humanizeDecisionLabel,
  parseGuardrailReport,
  parseHumanizeReport,
  parseVariants,
  stageStateLabel,
  statusLabel,
  STAGES,
  downstreamStageKeys,
  downstreamStageLabels,
  publishTags,
} from './Pipeline'

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
  it('无工件时解析后端直接返回的文案数组', () => {
    const task: Task = {
      ...baseTask(),
      stages: [{
        id: 1, stage_key: 'copywriting', status: 'completed',
        output: [{ title: '真实接口格式', body: '正文' }],
        feedback: '', revision_round: 0, cost: 0, latency: 0,
      }],
    }
    const v = parseVariants(task)
    expect(v).toEqual([{ title: '真实接口格式', body: '正文' }])
  })

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

describe('流水线阶段契约', () => {
  it('阶段顺序与后端一致（含去 AI 味与确定性守门层）', () => {
    // 后端 marketing.yaml 里 is_stage: true 的节点顺序
    expect(STAGES.map((s) => s.key)).toEqual([
      'research', 'competitor', 'strategy', 'copywriting',
      'humanize', 'guardrail', 'review',
    ])
  })

  it('工具型阶段不可人工编辑（守门结论与去 AI 味处置报告）', () => {
    expect(EDITABLE_STAGES).not.toContain('guardrail')
    // 去 AI 味的产出是处置报告，真正改的是 copywriting；开放编辑不会回填
    expect(EDITABLE_STAGES).not.toContain('humanize')
    expect(EDITABLE_STAGES).toContain('copywriting')
    expect(EDITABLE_STAGES).toContain('research')
  })

  it('skipped 与 completed 的展示文案不同（不把「没做」显示成「已完成」）', () => {
    expect(stageStateLabel('completed', '原始说明')).toBe('已完成')
    expect(stageStateLabel('skipped', '原始说明')).toContain('跳过')
    expect(stageStateLabel('pending', '原始说明')).toBe('原始说明')
  })
})

describe('下游阶段推导（编辑阶段下游影响可视化）', () => {
  it('research 的下游是其后全部 6 个阶段', () => {
    expect(downstreamStageKeys('research')).toEqual([
      'competitor', 'strategy', 'copywriting', 'humanize', 'guardrail', 'review',
    ])
  })

  it('strategy 的下游是创作与后续 4 个阶段', () => {
    expect(downstreamStageKeys('strategy')).toEqual([
      'copywriting', 'humanize', 'guardrail', 'review',
    ])
  })

  it('末端阶段 review 无下游', () => {
    expect(downstreamStageKeys('review')).toEqual([])
  })

  it('未知阶段返回空（不误报下游）', () => {
    expect(downstreamStageKeys('nope' as never)).toEqual([])
  })

  it('下游中文名与 STAGES 顺序一致', () => {
    expect(downstreamStageLabels('competitor')).toEqual([
      '策略制定', '文案创作', '去 AI 味', '质量守门', '审校优化',
    ])
  })
})

describe('标签可发布形态预览（publishTags）', () => {
  it('hash 风格：每个标签补单个井号', () => {
    expect(publishTags(['通勤好物', '保温杯'], 'hash')).toBe('#通勤好物 #保温杯')
  })

  it('paired 风格：每个标签前后成对井号（微博写法）', () => {
    expect(publishTags(['通勤好物', '保温杯'], 'paired')).toBe('#通勤好物# #保温杯#')
  })

  it('none 风格：不使用话题标签，返回空串', () => {
    expect(publishTags(['通勤好物'], 'none')).toBe('')
  })

  it('空 / 未知风格不崩溃，未知风格原样拼接', () => {
    expect(publishTags([], 'hash')).toBe('')
    expect(publishTags(['a'], '')).toBe('')
  })

  it('去掉用户已手输的井号再补（对齐后端 normalize_tag，避免 ##标签）', () => {
    expect(publishTags(['#通勤好物', '＃保温杯＃'], 'hash')).toBe('#通勤好物 #保温杯')
  })
})

describe('parseGuardrailReport', () => {
  it('没有守门阶段时返回 null（流水线未启用守门层）', () => {
    expect(parseGuardrailReport(baseTask())).toBeNull()
  })

  it('读出报告并保留需人工处理的事项', () => {
    const task: Task = {
      ...baseTask(),
      stages: [{
        id: 1, stage_key: 'guardrail', status: 'completed',
        output: {
          channel_id: 'xiaohongshu', max_chars: 1200, passed: false,
          variant_reports: [], draft_hits: [], source_hits: [],
          auto_fixed: ['变体1：正文 1300 → 1180 字（降档 L1）'],
          needs_human: ['变体1：成稿命中违禁词/广告法用语 1 处，需人工确认替换：全网最低'],
          stats: { variants: 1, auto_fixed: 1, needs_human: 1 },
          ai_tell_stats: {},
        },
        feedback: '', revision_round: 0, cost: 0, latency: 0,
      }],
    }
    const report = parseGuardrailReport(task)
    expect(report?.passed).toBe(false)
    expect(report?.needs_human[0]).toContain('全网最低')
    expect(report?.stats.auto_fixed).toBe(1)
  })
})

describe('parseHumanizeReport', () => {
  it('没有该阶段时返回 null（流水线未启用去 AI 味）', () => {
    expect(parseHumanizeReport(baseTask())).toBeNull()
  })

  it('读出报告并保留每条变体的处置与前后对比', () => {
    const task: Task = {
      ...baseTask(),
      stages: [{
        id: 1, stage_key: 'humanize', status: 'completed',
        output: {
          enabled: true, channel_id: 'wechat', reason: '',
          variant_reports: [{
            index: 0, decision: 'rejected', reason: 'AI 味命中未减少（3 → 8），保留原文',
            ai_tells_before: 3, ai_tells_after: 8, fatal_before: 1, fatal_after: 1,
            body_chars_before: 480, body_chars_after: 450, changes: [],
          }],
          stats: { variants: 1, rewrites: 1, accepted: 0, rejected: 1, failed: 0 },
        },
        feedback: '', revision_round: 0, cost: 0.0006, latency: 0.4,
      }],
    }
    const report = parseHumanizeReport(task)
    expect(report?.enabled).toBe(true)
    expect(report?.variant_reports[0].decision).toBe('rejected')
    expect(report?.stats.rejected).toBe(1)
  })

  it('渠道未启用时 enabled=false（不消耗 token，也不该装作改过）', () => {
    const task: Task = {
      ...baseTask(),
      stages: [{
        id: 1, stage_key: 'humanize', status: 'skipped',
        output: {
          enabled: false, channel_id: 'xiaohongshu',
          reason: '去 AI 味改写仅对公众号（wechat）启用', variant_reports: [], stats: {},
        },
        feedback: '', revision_round: 0, cost: 0, latency: 0,
      }],
    }
    expect(parseHumanizeReport(task)?.enabled).toBe(false)
  })

  it('处置结论有中文说明', () => {
    expect(humanizeDecisionLabel('accepted')).toBe('已采纳改写')
    expect(humanizeDecisionLabel('rejected')).toContain('保留原文')
    expect(humanizeDecisionLabel('failed')).toContain('保留原文')
  })
})
