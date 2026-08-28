import { useState } from 'react'
import TaskList from './components/TaskList'
import TaskDetail from './components/TaskDetail'
import BrandManager from './components/BrandManager'

type View = 'tasks' | 'brands'

export default function App() {
  const [view, setView] = useState<View>('tasks')
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)

  const goTasks = () => {
    setView('tasks')
    setSelectedTaskId(null)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <h1>
            ContentForge
            <span className="subtitle">多 Agent 营销内容工作台</span>
          </h1>
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
