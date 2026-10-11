import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

/** localStorage key：用户手动选择的主题（写进 index.html 防 FOUC 脚本与这里保持一致）。 */
export const THEME_STORAGE_KEY = 'cf-theme'

/**
 * 初始主题：用户手动选过则用 localStorage，否则跟随系统偏好。
 * 独立成纯函数，方便测试直接调用验证解析逻辑。
 */
export function resolveInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // localStorage 不可用（隐私模式等）时回退到系统偏好
  }
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/**
 * 主题状态：初始值 = resolveInitialTheme()，切换时写 localStorage 并同步
 * document.documentElement.dataset.theme（CSS 据此切换 token）。
 * 用户没手动选过时，跟随系统偏好实时变化。
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(resolveInitialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme)
    } catch {
      // 忽略写入失败（隐私模式等）
    }
  }, [theme])

  useEffect(() => {
    // 用户手动选过就不再跟随系统，否则系统切深色时同步切换
    let manuallyChosen = true
    try {
      manuallyChosen = localStorage.getItem(THEME_STORAGE_KEY) !== null
    } catch {
      manuallyChosen = true
    }
    if (manuallyChosen) return

    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setTheme(e.matches ? 'dark' : 'light')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const toggle = useCallback(() => {
    setTheme((prev) => (prev === 'light' ? 'dark' : 'light'))
  }, [])

  return { theme, toggle }
}
