import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import FeedbackPanel from './FeedbackPanel'

const { feedbackQueue } = vi.hoisted(() => ({ feedbackQueue: [] as Array<Record<string, unknown>> }))

vi.mock('../api', () => ({
  api: {
    getFeedback: vi.fn().mockImplementation(() =>
      feedbackQueue.length > 0
        ? Promise.resolve(feedbackQueue.shift())
        : Promise.reject(new Error('no data')),
    ),
    submitFeedback: vi.fn().mockResolvedValue({
      content_id: 1, views: 1000, conversions: 30, score: 3.5,
      is_simulated: 0, note: '', created_at: '',
    }),
    simulateFeedback: vi.fn().mockResolvedValue({
      content_id: 1, views: 5000, conversions: 200, score: 4.2,
      is_simulated: 1, note: '', created_at: '',
    }),
    listFeedbackRules: vi.fn()
      .mockResolvedValueOnce([
        { id: 1, rule_text: '标题含数字点击率高', strength: 1.5, created_at: '' },
      ])
      .mockResolvedValue([]),
    deleteFeedbackRule: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

describe('FeedbackPanel', () => {
  it('渲染回填表单并可保存', async () => {
    render(<FeedbackPanel taskId={1} />)
    expect(screen.getByText('效果回填')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('阅读量'), { target: { value: '1000' } })
    fireEvent.change(screen.getByLabelText('转化数'), { target: { value: '30' } })
    fireEvent.change(screen.getByLabelText('效果分'), { target: { value: '3.5' } })
    fireEvent.click(screen.getByText('保存效果'))
    await waitFor(() => expect(screen.getByText('已保存')).toBeTruthy())
  })

  it('一键模拟按钮存在且点击后显示模拟数据', async () => {
    feedbackQueue.push({
      content_id: 1, views: 5000, conversions: 200, score: 4.2,
      is_simulated: 1, note: '', created_at: '',
    })
    render(<FeedbackPanel taskId={1} />)
    expect(screen.getByText('一键模拟效果')).toBeTruthy()
    fireEvent.click(screen.getByText('一键模拟效果'))
    await waitFor(() => expect(screen.getByText(/（模拟）/)).toBeTruthy())
  })

  it('品牌关联时展示已学规律并可删除', async () => {
    render(<FeedbackPanel taskId={1} brandId={7} />)
    expect(await screen.findByText('标题含数字点击率高')).toBeInTheDocument()
    fireEvent.click(screen.getByText('删除'))
    await waitFor(() => expect(screen.queryByText('标题含数字点击率高')).not.toBeInTheDocument())
  })
})
