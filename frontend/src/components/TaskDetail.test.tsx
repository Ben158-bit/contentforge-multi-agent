import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, subscribeTask } from '../api'
import TaskDetail from './TaskDetail'
import type { Task } from '../types'

vi.mock('../api', () => ({
  api: {
    getTask: vi.fn(),
    listBrands: vi.fn(),
    rerunTask: vi.fn(),
    getChannels: vi.fn(),
    updateTaskChannel: vi.fn(),
  },
  subscribeTask: vi.fn(),
}))

const baseTask: Task = {
  id: 24,
  topic: '宣传品牌',
  brand_name: 'LZ高奢手提包',
  target_audience: '20-40 喜爱奢侈品的精英女士',
  channel_id: 'wechat',
  extra_requirements: '',
  status: 'failed',
  total_cost: 0,
  total_latency: 0,
  created_at: '2026-10-10 16:19:36',
  updated_at: '2026-10-10 16:19:36',
  stages: [],
}

function renderWith(task: Task) {
  vi.mocked(api.getTask).mockResolvedValue(task)
  return render(<TaskDetail taskId={task.id} />)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listBrands).mockResolvedValue([])
  vi.mocked(api.rerunTask).mockResolvedValue(baseTask)
  vi.mocked(api.getChannels).mockResolvedValue([
    { id: 'wechat', name: '公众号', desc: '', tone: '', max_chars: 0 },
    { id: 'xiaohongshu', name: '小红书', desc: '', tone: '', max_chars: 0 },
  ])
  vi.mocked(api.updateTaskChannel).mockResolvedValue(baseTask)
  vi.mocked(subscribeTask).mockReturnValue(() => undefined)
})

/**
 * 失败原因必须看得见。
 *
 * 起因是一次真实故障：任务失败后前端只显示一个「失败」，成本 0、阶段全停在
 * 「等待」，用户完全无从判断是配置、模型还是存储问题。
 */
describe('TaskDetail 失败原因', () => {
  it('展示后端记录的失败原因', async () => {
    renderWith({
      ...baseTask,
      error: 'OperationalError: attempt to write a readonly database',
    })

    expect(
      await screen.findByText(/attempt to write a readonly database/),
    ).toBeInTheDocument()
  })

  it('未记录原因时明说，不静默留白', async () => {
    renderWith({ ...baseTask, error: '' })

    expect(await screen.findByText(/后端未记录原因/)).toBeInTheDocument()
  })

  it('非失败状态不出现失败横幅', async () => {
    renderWith({ ...baseTask, status: 'waiting_human', error: '' })

    expect(await screen.findByText('宣传品牌')).toBeInTheDocument()
    expect(screen.queryByText(/后端未记录原因/)).not.toBeInTheDocument()
  })
})

/**
 * 「重新生成」的确认链路。
 *
 * 它以前用原生 `window.confirm()` 确认，在内置浏览器预览面板里点击后**毫无反应**：
 * CDP 探针实测确认框会阻塞渲染进程（`Runtime.evaluate` 超时 8s+），而 `/rerun`
 * 网络请求数为 0、页面状态不变 —— 用户既看不到弹窗，也得不到任何反馈。
 */
describe('TaskDetail 重新生成', () => {
  it('点击后先展开行内确认，不直接重跑', async () => {
    renderWith({ ...baseTask, status: 'waiting_human', error: '' })

    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }))

    expect(screen.getByText('重跑将覆盖当前产出')).toBeInTheDocument()
    expect(vi.mocked(api.rerunTask)).not.toHaveBeenCalled()
  })

  it('确认之后才调用 rerun 接口', async () => {
    renderWith({ ...baseTask, status: 'waiting_human', error: '' })

    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }))
    fireEvent.click(screen.getByRole('button', { name: '确定重跑' }))

    await waitFor(() => expect(vi.mocked(api.rerunTask)).toHaveBeenCalledWith(24))
  })

  it('取消则不调用接口', async () => {
    renderWith({ ...baseTask, status: 'waiting_human', error: '' })

    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(vi.mocked(api.rerunTask)).not.toHaveBeenCalled()
  })

  it('运行中的任务按钮禁用（有可见的禁用态，不是静默无反应）', async () => {
    renderWith({ ...baseTask, status: 'running', error: '' })

    expect(await screen.findByRole('button', { name: '重新生成' })).toBeDisabled()
  })
})

/**
 * 任务页直接切换发布渠道。
 *
 * 需求：同一品牌项目想换渠道投放时，不必回主页重新手打一整套输入。
 * 切换渠道意味着旧产出按旧渠道口径生成、全部作废，所以必须二次确认
 * （沿用 ConfirmButton 的教训：禁止原生 confirm()，用应用内确认条）。
 */
describe('TaskDetail 渠道切换', () => {
  it('任务头用下拉展示当前渠道（选项来自 /api/channels 的中文名）', async () => {
    renderWith({ ...baseTask, channel_id: 'wechat', status: 'waiting_human', error: '' })

    const select = await screen.findByLabelText('切换发布渠道')
    expect(select).toHaveValue('wechat')
    expect(screen.getByRole('option', { name: '小红书' })).toBeInTheDocument()
  })

  it('选中新渠道先展示确认条说明后果，不直接调接口', async () => {
    renderWith({ ...baseTask, channel_id: 'wechat', status: 'waiting_human', error: '' })

    fireEvent.change(await screen.findByLabelText('切换发布渠道'), {
      target: { value: 'xiaohongshu' },
    })

    expect(screen.getByText(/将清空当前产出并按新渠道整条重跑/)).toBeInTheDocument()
    expect(vi.mocked(api.updateTaskChannel)).not.toHaveBeenCalled()
  })

  it('确认后调用渠道切换接口', async () => {
    renderWith({ ...baseTask, channel_id: 'wechat', status: 'waiting_human', error: '' })

    fireEvent.change(await screen.findByLabelText('切换发布渠道'), {
      target: { value: 'xiaohongshu' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确认切换并重跑' }))

    await waitFor(() =>
      expect(vi.mocked(api.updateTaskChannel)).toHaveBeenCalledWith(24, 'xiaohongshu'),
    )
  })

  it('取消则不调用接口', async () => {
    renderWith({ ...baseTask, channel_id: 'wechat', status: 'waiting_human', error: '' })

    fireEvent.change(await screen.findByLabelText('切换发布渠道'), {
      target: { value: 'xiaohongshu' },
    })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(vi.mocked(api.updateTaskChannel)).not.toHaveBeenCalled()
  })

  it('运行中的任务渠道下拉禁用', async () => {
    renderWith({ ...baseTask, status: 'running', error: '' })

    expect(await screen.findByLabelText('切换发布渠道')).toBeDisabled()
  })
})

// ---------------------------------------------------------------------------
// 渠道内容契约
//
// 契约把渠道方法论（「小红书要 2-3 个大标签 + 3-5 个长尾标签」「公众号的小标题
// 要真的出现在正文里」）从**提示词里的一句话**变成**可核验的字段**。
// 前端不自己维护「哪个渠道要哪些字段」的表：label 一律由后端从 channels.yaml 带来，
// 否则后端加了一条契约，前端还显示旧的那一套。
// ---------------------------------------------------------------------------
const XHS_CONTRACT = [
  { field: 'hook_type', label: '开头钩子类型', kind: 'enum', ok: true, detail: '' },
  { field: 'cta', label: '行动号召', kind: 'text', ok: false, detail: '缺失或为空' },
]

function copywritingStage(variant: Record<string, unknown>) {
  return {
    id: 4,
    stage_key: 'copywriting' as const,
    status: 'completed' as const,
    output: [variant],
    feedback: '',
    revision_round: 0,
    cost: 0,
    latency: 0,
  }
}

function guardrailStage(
  report: Record<string, unknown>,
  variantReports: Record<string, unknown>[],
) {
  return {
    id: 6,
    stage_key: 'guardrail' as const,
    status: 'completed' as const,
    output: {
      channel_id: 'xiaohongshu',
      max_chars: 1200,
      passed: true,
      auto_fixed: [],
      needs_human: [],
      contract_gaps: [],
      stats: {},
      ai_tell_stats: {},
      variant_reports: variantReports,
      ...report,
    },
    feedback: '',
    revision_round: 0,
    cost: 0,
    latency: 0,
  }
}

const XHS_VARIANT_REPORT = {
  index: 0,
  body_chars: 120,
  max_chars: 1200,
  over_limit: false,
  hard_cut: false,
  downgraded: false,
  downgrade_level: 0,
  downgrade_notes: '',
  dropped: [],
  hashtags_before: [],
  hashtags_after: [],
  hashtag_notes: [],
  emoji_before: 0,
  emoji_after: 0,
  emoji_notes: [],
  word_hits: [],
  ai_tells: [],
  title: null,
  contract: XHS_CONTRACT,
}

describe('TaskDetail 内容契约', () => {
  it('守门报告标出契约缺口数量', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: [] }),
        guardrailStage(
          {
            contract_gaps: ['变体1：内容契约「行动号召」缺失或为空'],
            stats: { variants: 1, contract_gaps: 1 },
          },
          [XHS_VARIANT_REPORT],
        ),
      ],
    })

    expect(await screen.findByText(/内容契约缺口 1 处/)).toBeInTheDocument()
    // 「· 内容契约 1/2」是守门报告里的逐变体比例；变体卡片上的那份前缀是「渠道」
    expect(screen.getByText(/· 内容契约 1\/2/)).toBeInTheDocument()
  })

  it('变体卡片逐项展示契约：落实的给出值，缺口给出原因', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({
          title: '标题',
          body: '正文',
          hashtags: [],
          hook_type: '场景痛点',
        }),
        guardrailStage({}, [XHS_VARIANT_REPORT]),
      ],
    })

    // 覆盖：两项目里一项落实、一项缺失
    expect(await screen.findByText(/渠道内容契约 1\/2/)).toBeInTheDocument()
    expect(screen.getByText('场景痛点')).toBeInTheDocument()
    expect(screen.getByText('缺失或为空')).toBeInTheDocument()
    expect(screen.getByText(/有未落实项/)).toBeInTheDocument()
  })

  it('全部落实时不显示未落实提示', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: [], hook_type: '场景痛点' }),
        guardrailStage({}, [
          {
            ...XHS_VARIANT_REPORT,
            contract: [
              { field: 'hook_type', label: '开头钩子类型', kind: 'enum', ok: true, detail: '' },
              { field: 'cta', label: '行动号召', kind: 'text', ok: true, detail: '' },
            ],
          },
        ]),
      ],
    })

    expect(await screen.findByText(/渠道内容契约 2\/2/)).toBeInTheDocument()
    expect(screen.getByText(/全部落实/)).toBeInTheDocument()
    expect(screen.queryByText(/有未落实项/)).not.toBeInTheDocument()
  })

  it('渠道未声明契约（无 contract 字段）时不渲染契约区块', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: [] }),
        guardrailStage({}, [{ ...XHS_VARIANT_REPORT, contract: [] }]),
      ],
    })

    expect(await screen.findByText('文案变体（可编辑后确认）')).toBeInTheDocument()
    expect(screen.queryByText(/渠道内容契约/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 策略阶段：渠道原生内容骨架
//
// 骨架是「渠道差异前移到策略阶段」的落点，也是 copywriting 必须遵循的正文结构。
// 它必须**在界面上可见**：看不到骨架，人工就没法判断文案是不是照计划写的。
// ---------------------------------------------------------------------------
function strategyStage(structure: unknown) {
  return {
    id: 3,
    stage_key: 'strategy' as const,
    status: 'completed' as const,
    output: {
      target_audience: '25-35 岁通勤上班族',
      core_value_proposition: '一杯暖芯，24 小时暖到你心里',
      key_messages: ['a', 'b', 'c'],
      content_angles: ['角度一', '角度二'],
      content_structure: structure,
      channel_strategy: '小红书侧重场景种草',
    },
    feedback: '',
    revision_round: 0,
    cost: 0,
    latency: 0,
  }
}

describe('TaskDetail 竞品真实数据', () => {
  const competitorStage = (output: Record<string, unknown>) => ({
    id: 8,
    stage_key: 'competitor' as const,
    status: 'completed' as const,
    output,
    feedback: '',
    revision_round: 0,
    cost: 0,
    latency: 0,
  })

  it('展示数据源抓到的真实竞品实例（标题/作者/互动/链接可溯源）', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [competitorStage({
        competitors: [{ name: '同类磁轴键盘品牌（演示）' }],
        gaps: ['缺少场景化内容'],
        evidence: [{
          channel: 'xiaohongshu', kind: 'note', title: '磁轴键盘开箱',
          author: '测评君', metrics: { likes: 9335, fans: 30165 },
          link: 'https://www.xiaohongshu.com/explore/abc',
        }],
      })],
    })

    expect(await screen.findByText(/真实竞品实例 1 条/)).toBeInTheDocument()
    expect(screen.getByText(/磁轴键盘开箱/)).toBeInTheDocument()
    expect(screen.getByText(/测评君/)).toBeInTheDocument()
    expect(screen.getByText(/赞9335/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /查看原文/ })
    expect(link).toHaveAttribute('href', 'https://www.xiaohongshu.com/explore/abc')
  })

  it('无真实数据时明说「模型内部知识」，不假装有依据', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [competitorStage({ competitors: [{ name: '竞品A' }], gaps: [] })],
    })

    expect(await screen.findByText(/未配置真实竞品数据源/)).toBeInTheDocument()
  })
})

describe('TaskDetail 策略内容骨架', () => {
  it('按顺序展示内容骨架', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [strategyStage(['开头钩子（通勤场景）', '场景痛点共鸣', '亲测体验', '行动号召'])],
    })

    expect(await screen.findByText(/内容骨架（正文必须按此顺序展开）/)).toBeInTheDocument()
    expect(screen.getByText('场景痛点共鸣')).toBeInTheDocument()
    expect(screen.getByText('行动号召')).toBeInTheDocument()
    expect(screen.getByText(/关键信息 3 条/)).toBeInTheDocument()
  })

  it('骨架缺失时说明会退回渠道标准结构，而不是显示空白', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [strategyStage([])],
    })

    expect(
      await screen.findByText(/本任务未给出内容骨架 · 创作阶段将退回渠道标准结构/),
    ).toBeInTheDocument()
  })
})

function reviewStage(
  checklist: { item: string; passed: boolean; note?: string }[],
  passed = true,
) {
  return {
    id: 7,
    stage_key: 'review' as const,
    status: 'completed' as const,
    output: { passed, feedback: passed ? '' : '结尾需补充行动号召', checklist },
    feedback: '',
    revision_round: 0,
    cost: 0,
    latency: 0,
  }
}

describe('TaskDetail 审校清单', () => {
  it('逐项展示审校 checklist 的通过/未通过与判断依据', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        reviewStage(
          [
            { item: '主题一致性', passed: true, note: '核对主题：保温杯' },
            { item: '标题是否命中「关键词 + 情绪触发词」', passed: false, note: '缺少情绪触发词' },
          ],
          false,
        ),
      ],
    })

    expect(await screen.findByText(/审校结论 · 不通过/)).toBeInTheDocument()
    expect(screen.getByText(/标题是否命中「关键词 \+ 情绪触发词」/)).toBeInTheDocument()
    expect(screen.getByText(/缺少情绪触发词/)).toBeInTheDocument()
    expect(screen.getByText(/结尾需补充行动号召/)).toBeInTheDocument()
  })

  it('checklist 为空时只显示结论，不崩溃', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [reviewStage([], true)],
    })

    expect(await screen.findByText(/审校结论 · 通过/)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 编辑器实时字数辅助
//
// 后端守门报告带出「标题达标区间（min_chars/max_chars）」与「正文上限 max_chars」，
// 前端在编辑时实时反馈字数，避免「编辑后守门层又打回」的往返。
// 区间值一律由后端带过来，前端不硬编码渠道区间表（与契约 label 同一条铁律）。
// ---------------------------------------------------------------------------
function variantReportWithTitle(min: number, max: number, overrides: Record<string, unknown> = {}) {
  return {
    ...XHS_VARIANT_REPORT,
    title: {
      title: '标题',
      length: 0,
      length_ok: true,
      min_chars: min,
      max_chars: max,
      grade: 'B',
    },
    ...overrides,
  }
}

describe('TaskDetail 实时字数辅助', () => {
  it('标题展示达标区间，越界时标出', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({
          title: '一二三四五六七八九十一二三四五六七八九十一二', // 22 字，超 18–20
          body: '正文',
          hashtags: [],
        }),
        guardrailStage({}, [variantReportWithTitle(18, 20)]),
      ],
    })

    const hint = await screen.findByText(/标题 22 字 · 达标 18–20 字 · 已越界/)
    expect(hint.className).toContain('char-hint-warn')
  })

  it('标题在区间内不标越界', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({
          title: '一二三四五六七八九十一二三四五六七八九', // 19 字，达标
          body: '正文',
          hashtags: [],
        }),
        guardrailStage({}, [variantReportWithTitle(18, 20)]),
      ],
    })

    expect(await screen.findByText(/标题 19 字 · 达标 18–20 字$/)).toBeInTheDocument()
    expect(screen.queryByText(/已越界/)).not.toBeInTheDocument()
  })

  it('正文超限标出超出字数', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '字'.repeat(1300), hashtags: [] }),
        // XHS_VARIANT_REPORT.max_chars = 1200
        guardrailStage({}, [XHS_VARIANT_REPORT]),
      ],
    })

    const hint = await screen.findByText(/正文 1300\/1200 字 · 超 100 字/)
    expect(hint.className).toContain('char-hint-over')
  })

  it('渠道未配置标题区间（0/0）时不显示区间提示', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: [] }),
        guardrailStage({}, [variantReportWithTitle(0, 0)]),
      ],
    })

    expect(await screen.findByText('文案变体（可编辑后确认）')).toBeInTheDocument()
    expect(screen.queryByText(/达标/)).not.toBeInTheDocument()
  })

  it('编辑标题后字数提示实时更新', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({
          title: '一二三四五六七八九十一二三四五六七八九', // 19 字，达标
          body: '正文',
          hashtags: [],
        }),
        guardrailStage({}, [variantReportWithTitle(18, 20)]),
      ],
    })

    expect(await screen.findByText(/标题 19 字 · 达标 18–20 字$/)).toBeInTheDocument()

    const titleInput = screen.getByPlaceholderText('标题')
    fireEvent.change(titleInput, {
      target: { value: '一二三四五六七八九十一二三四五六七八九十一二' }, // 22 字
    })

    expect(screen.getByText(/标题 22 字 · 达标 18–20 字 · 已越界/)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 守门报告可操作清单
//
// 之前的守门报告把 `needs_human` 平铺成字符串，违禁词只显示词、AI 味根本不显示，
// 用户看不到「命中位置、怎么改」。现在按变体分组，每条问题带 severity 徽章 +
// 命中位置摘录 + 建议 + 「定位」按钮（跳到编辑器对应字段）。
// ---------------------------------------------------------------------------
describe('TaskDetail 守门报告可操作清单', () => {
  it('违禁词命中带位置摘录与替换建议，severity 归为致命', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: [] }),
        guardrailStage({}, [
          {
            ...XHS_VARIANT_REPORT,
            contract: [],
            word_hits: [
              {
                scope: 'draft',
                category: 'absolute_advertising',
                word: '全网最低',
                severity: 'high',
                context: '……全网最低价的 3 个方法……',
                suggestion: '改为「价格实惠」',
                basis: '广告法第九条',
              },
            ],
          },
        ]),
      ],
    })

    expect(await screen.findByText('全网最低')).toBeInTheDocument()
    expect(screen.getByText(/全网最低价的 3 个方法/)).toBeInTheDocument()
    expect(screen.getByText(/价格实惠/)).toBeInTheDocument()
    expect(screen.getByText('致命')).toBeInTheDocument()
  })

  it('AI 味命中带中文分类名、命中片段与改写建议', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: [] }),
        guardrailStage({}, [
          {
            ...XHS_VARIANT_REPORT,
            contract: [],
            ai_tells: [
              {
                pattern_id: 'corporate_jargon',
                category: 'language',
                category_label: '语言与句式',
                severity: 'warning',
                count: 2,
                suggestion: '换成日常说法',
                samples: ['我们赋能客户，赋能团队'],
              },
            ],
          },
        ]),
      ],
    })

    expect(await screen.findByText('语言与句式')).toBeInTheDocument()
    expect(screen.getByText(/我们赋能客户/)).toBeInTheDocument()
    expect(screen.getByText(/换成日常说法/)).toBeInTheDocument()
    expect(screen.getByText('警告')).toBeInTheDocument()
  })

  it('点击定位滚动到编辑器对应字段', async () => {
    const scrollSpy = vi.fn()
    const elProto = Element.prototype as unknown as Record<string, unknown>
    const original = elProto.scrollIntoView
    elProto.scrollIntoView = scrollSpy
    try {
      renderWith({
        ...baseTask,
        status: 'waiting_human',
        error: '',
        stages: [
          copywritingStage({ title: '标题', body: '正文', hashtags: [] }),
          guardrailStage({}, [
            {
              ...XHS_VARIANT_REPORT,
              contract: [],
              word_hits: [
                {
                  scope: 'draft',
                  category: 'absolute_advertising',
                  word: '全网最低',
                  severity: 'high',
                  context: '……全网最低……',
                  suggestion: '改为「价格实惠」',
                },
              ],
            },
          ]),
        ],
      })

      const locateButtons = await screen.findAllByRole('button', { name: '定位' })
      expect(locateButtons.length).toBeGreaterThan(0)
      fireEvent.click(locateButtons[0])
      expect(scrollSpy).toHaveBeenCalled()
    } finally {
      if (original === undefined) delete elProto.scrollIntoView
      else elProto.scrollIntoView = original
    }
  })
})

// ---------------------------------------------------------------------------
// 阶段编辑下游影响可视化
//
// 编辑上游阶段（research/competitor/strategy）会在确认时重跑其下游，
// 下游当前产出随之作废。前端要在「编辑中」提示将重跑哪些下游、「编辑后」
// 把作废的下游节点标成「待重算」——否则用户会误以为下游还是最新的。
// ---------------------------------------------------------------------------
describe('TaskDetail 编辑下游影响', () => {
  it('编辑上游阶段时提示将重跑的下游', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [strategyStage(['步骤一', '步骤二'])],
    })

    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))

    expect(
      await screen.findByText(/保存后将重新执行下游：文案创作 → 去 AI 味 → 质量守门 → 审校优化/),
    ).toBeInTheDocument()
  })

  it('编辑末端阶段时提示无下游', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [reviewStage([], true)],
    })

    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))

    expect(await screen.findByText(/本阶段为末端阶段，保存后无下游需重新执行/)).toBeInTheDocument()
  })

  it('上游已编辑时下游节点标出「待重算」', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        {
          id: 1, stage_key: 'research', status: 'edited', output: {},
          feedback: '', revision_round: 0, cost: 0, latency: 0,
        },
      ],
    })

    // research 的下游共 6 个阶段，全部应标「待重算」
    const staleHints = await screen.findAllByText('待重算')
    expect(staleHints.length).toBe(6)
  })

  it('无编辑时流水线不出现「待重算」', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [strategyStage(['步骤一'])],
    })

    expect(await screen.findByText(/内容骨架（正文必须按此顺序展开）/)).toBeInTheDocument()
    expect(screen.queryByText('待重算')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 标签可发布形态预览
//
// 渠道标签风格（hashtag_style：hash / paired / none）由后端守门报告带回，
// 前端编辑器据此把用户输入的原始标签实时渲染成「发布后长什么样」，
// 不自己维护「哪个渠道用哪种样式」的映射表。
// ---------------------------------------------------------------------------
describe('TaskDetail 标签可发布形态预览', () => {
  it('hash 渠道预览带单井号的可发布形态', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: ['通勤好物', '保温杯'] }),
        guardrailStage({ hashtag_style: 'hash' }, [XHS_VARIANT_REPORT]),
      ],
    })

    expect(await screen.findByText('发布形态：#通勤好物 #保温杯')).toBeInTheDocument()
  })

  it('paired 渠道预览成对井号', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: ['通勤好物'] }),
        guardrailStage({ hashtag_style: 'paired' }, [XHS_VARIANT_REPORT]),
      ],
    })

    expect(await screen.findByText('发布形态：#通勤好物#')).toBeInTheDocument()
  })

  it('不使用标签的渠道提示不使用，而非显示空形态', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: ['通勤好物'] }),
        guardrailStage({ hashtag_style: 'none' }, [{ ...XHS_VARIANT_REPORT, contract: [] }]),
      ],
    })

    expect(await screen.findByText('本渠道不使用话题标签')).toBeInTheDocument()
    expect(screen.queryByText(/发布形态：/)).not.toBeInTheDocument()
  })

  it('编辑标签后发布形态实时更新', async () => {
    renderWith({
      ...baseTask,
      status: 'waiting_human',
      error: '',
      stages: [
        copywritingStage({ title: '标题', body: '正文', hashtags: ['通勤好物'] }),
        guardrailStage({ hashtag_style: 'hash' }, [XHS_VARIANT_REPORT]),
      ],
    })

    expect(await screen.findByText('发布形态：#通勤好物')).toBeInTheDocument()

    const tagInput = screen.getByPlaceholderText('话题标签（空格分隔）')
    fireEvent.change(tagInput, { target: { value: '通勤好物 保温杯' } })

    expect(screen.getByText('发布形态：#通勤好物 #保温杯')).toBeInTheDocument()
  })
})
