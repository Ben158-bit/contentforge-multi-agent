import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ConfirmButton from './ConfirmButton'

/**
 * 行内二次确认组件。
 *
 * 存在的理由是一次真实故障：任务详情页的「重新生成」用原生 `window.confirm()`
 * 做确认，在内置浏览器预览面板里点击后**毫无反应**（CDP 探针实测：confirm 阻塞
 * 渲染进程 8s+、/rerun 请求数为 0、页面状态不变）。原生模态框在嵌入式环境里
 * 不可靠，所以改为应用内确认。
 */
describe('ConfirmButton', () => {
  const base = { label: '重新生成', question: '重跑将覆盖当前产出', confirmLabel: '确定重跑' }

  it('常态只有一个按钮，确认文案不提前出现', () => {
    render(<ConfirmButton {...base} onConfirm={vi.fn()} />)

    expect(screen.getByRole('button', { name: '重新生成' })).toBeInTheDocument()
    expect(screen.queryByText('重跑将覆盖当前产出')).not.toBeInTheDocument()
  })

  it('第一次点击只展开确认条，绝不执行操作', () => {
    const onConfirm = vi.fn()
    render(<ConfirmButton {...base} onConfirm={onConfirm} />)

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))

    expect(screen.getByText('重跑将覆盖当前产出')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确定重跑' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('点「确定」才真正执行', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(<ConfirmButton {...base} onConfirm={onConfirm} />)

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    fireEvent.click(screen.getByRole('button', { name: '确定重跑' }))

    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1))
  })

  it('点「取消」不执行，并退回常态', () => {
    const onConfirm = vi.fn()
    render(<ConfirmButton {...base} onConfirm={onConfirm} />)

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.queryByText('重跑将覆盖当前产出')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeInTheDocument()
  })

  it('Esc 可退出确认态（误触后必须能退回来）', () => {
    render(<ConfirmButton {...base} onConfirm={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    expect(screen.getByText('重跑将覆盖当前产出')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(screen.queryByText('重跑将覆盖当前产出')).not.toBeInTheDocument()
  })

  it('禁用时点不动，也不会展开确认条', () => {
    render(<ConfirmButton {...base} onConfirm={vi.fn()} disabled />)

    const btn = screen.getByRole('button', { name: '重新生成' })
    expect(btn).toBeDisabled()

    fireEvent.click(btn)
    expect(screen.queryByText('重跑将覆盖当前产出')).not.toBeInTheDocument()
  })

  it('确认条的点击不冒泡到外层（任务表格的行点击会打开详情）', () => {
    const outer = vi.fn()
    render(
      <div onClick={outer}>
        <ConfirmButton label="删除" question="删除后无法恢复" onConfirm={vi.fn()} />
      </div>,
    )

    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(outer).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(outer).not.toHaveBeenCalled()
  })
})
