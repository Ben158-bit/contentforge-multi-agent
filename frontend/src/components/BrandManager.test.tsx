import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import BrandManager from './BrandManager'

vi.mock('../api', () => ({
  api: {
    listBrands: vi.fn().mockResolvedValue([
      { id: 1, name: '暖芯', tone: '温暖', core_claims: '保温', audience: '', taboos: '', notes: '', pref_count: 2 },
    ]),
    createBrand: vi.fn().mockResolvedValue({ id: 2 }),
    updateBrand: vi.fn(),
    deleteBrand: vi.fn().mockResolvedValue({ ok: true }),
    getBrand: vi.fn().mockResolvedValue({
      id: 1, name: '暖芯', tone: '温暖', core_claims: '保温', audience: '', taboos: '', notes: '',
      preferences: [{ id: 1, rule_text: '标题避免感叹号', source_task: null, created_at: '' }],
    }),
  },
}))

describe('BrandManager', () => {
  it('渲染品牌列表', async () => {
    render(<BrandManager />)
    expect(await screen.findByText('暖芯')).toBeInTheDocument()
    expect(screen.getByText('偏好数')).toBeInTheDocument()
  })

  it('查看品牌详情展示已学偏好', async () => {
    render(<BrandManager />)
    fireEvent.click(await screen.findByRole('button', { name: '暖芯' }))
    expect(await screen.findByText('标题避免感叹号')).toBeInTheDocument()
  })

  it('缺少品牌名时创建提示错误', async () => {
    render(<BrandManager />)
    fireEvent.click(screen.getByRole('button', { name: '创建品牌' }))
    await waitFor(() => expect(screen.getByText('请填写品牌名')).toBeInTheDocument())
  })
})
