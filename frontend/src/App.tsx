import { useState } from 'react'
import TaskList from './components/TaskList'
import TaskDetail from './components/TaskDetail'
import BrandManager from './components/BrandManager'
import { useTheme } from './useTheme'

type View = 'tasks' | 'brands'

/** 太阳图标（浅色模式当前态），内联 SVG 而非 emoji。 */
function SunIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  )
}

/** 月亮图标（深色模式当前态），内联 SVG 而非 emoji。 */
function MoonIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}

export default function App() {
  const [view, setView] = useState<View>('tasks')
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)
  const { theme, toggle } = useTheme()

  const goTasks = () => {
    setView('tasks')
    setSelectedTaskId(null)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <div className="app-brand">
            <span className="app-logo" aria-hidden="true">
              C
            </span>
            <h1>
              ContentForge
              <span className="subtitle">多 Agent 营销内容工作台</span>
            </h1>
          </div>
          <div className="app-actions">
            <nav className="app-nav">
              <button
                className={`btn ghost nav-btn${view === 'tasks' && !selectedTaskId ? ' active' : ''}`}
                onClick={goTasks}
              >
                任务列表
              </button>
              <button
                className={`btn ghost nav-btn${view === 'brands' ? ' active' : ''}`}
                onClick={() => setView('brands')}
              >
                品牌档案
              </button>
              {selectedTaskId !== null && (
                <button className="btn ghost nav-btn" onClick={goTasks}>
                  ← 返回
                </button>
              )}
            </nav>
            <button
              className="btn ghost theme-toggle"
              onClick={toggle}
              aria-label={theme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
              title={theme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
            >
              {theme === 'light' ? <MoonIcon /> : <SunIcon />}
            </button>
          </div>
        </div>
      </header>
      <main className="app-main">
        {view === 'brands' ? (
          <BrandManager />
        ) : selectedTaskId === null ? (
          <TaskList onOpen={setSelectedTaskId} />
        ) : (
          <TaskDetail taskId={selectedTaskId} />
        )}
      </main>
      <footer className="app-footer">
        LangGraph · FastAPI · React · DeepSeek —— 多 Agent 协作 + human-in-the-loop + 记忆层
      </footer>
    </div>
  )
}
