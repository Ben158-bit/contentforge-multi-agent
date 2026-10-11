import '@testing-library/jest-dom'
import { vi } from 'vitest'

// jsdom 未实现 window.matchMedia，这里给一个可用的桩，避免 useTheme 等代码在测试里崩溃。
// 各测试可通过覆盖 window.matchMedia 精确控制 matches 返回值（见 App.test.tsx）。
function makeMediaQueryList(matches: boolean): MediaQueryList {
  return {
    matches,
    media: '',
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as unknown as MediaQueryList
}

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  configurable: true,
  value: vi.fn(() => makeMediaQueryList(false)),
})
