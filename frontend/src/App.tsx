import { useState } from 'react'
import TaskList from './components/TaskList'
import TaskDetail from './components/TaskDetail'

export default function App() {
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <h1>
            ContentForge
            <span className="subtitle">多 Agent 营销内容工作台</span>
          </h1>
          {selectedTaskId !== null && (
            <button className="btn ghost" onClick={() => setSelectedTaskId(null)}>
              ← 返回任务列表
            </button>
          )}
        </div>
      </header>
      <main className="app-main">
        {selectedTaskId === null ? (
          <TaskList onOpen={setSelectedTaskId} />
        ) : (
          <TaskDetail taskId={selectedTaskId} />
        )}
      </main>
      <footer className="app-footer">
        LangGraph · FastAPI · React · DeepSeek —— 多 Agent 协作 + human-in-the-loop
      </footer>
    </div>
  )
}
