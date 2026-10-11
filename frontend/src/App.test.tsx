import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

// 主题测试聚焦 App 的主题切换行为；子组件挂载时会发网络请求，故替换为桩。
vi.mock('./components/TaskList', () => ({ default: () => <div data-testid="task-list" /> }))
vi.mock('./components/TaskDetail', () => ({ default: () => <div data-testid="task-detail" /> }))
vi.mock('./components/BrandManager', () => ({ default: () => <div data-testid="brand-manager" /> }))

/** 覆盖 window.matchMedia，控制「系统偏好」为深色/浅色。 */
function stubSystemPrefersDark(dark: boolean) {
  const mm = window.matchMedia as unknown as {
    mockImplementation: (impl: (query: string) => MediaQueryList) => void
  }
  mm.mockImplementation(
    (query: string) =>
      ({
        matches: dark && query.includes('prefers-color-scheme'),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList,
  )
}

beforeEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('App 主题切换', () => {
  it('未手动选择且系统为浅色时，默认浅色', () => {
    stubSystemPrefersDark(false)
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('未手动选择且系统为深色时，跟随系统为深色', () => {
    stubSystemPrefersDark(true)
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('点击切换按钮在浅/深之间翻转，并持久化到 localStorage', () => {
    stubSystemPrefersDark(false)
    render(<App />)

    const toDark = screen.getByRole('button', { name: '切换到深色模式' })
    fireEvent.click(toDark)
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('cf-theme')).toBe('dark')

    const toLight = screen.getByRole('button', { name: '切换到浅色模式' })
    fireEvent.click(toLight)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('cf-theme')).toBe('light')
  })

  it('手动选择优先于系统偏好', () => {
    localStorage.setItem('cf-theme', 'dark')
    stubSystemPrefersDark(false) // 系统浅色，但用户手动选了深色
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
