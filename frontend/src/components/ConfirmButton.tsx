import { useEffect, useState } from 'react'

export interface ConfirmButtonProps {
  /** 常态按钮文案 */
  label: string
  /** 确认态提示，必须说明**后果**而不是只问「确定吗」 */
  question: string
  /** 确认按钮文案 */
  confirmLabel?: string
  onConfirm: () => void | Promise<void>
  /** 由父组件控制的禁用（如任务正在运行、请求进行中） */
  disabled?: boolean
  /** 常态按钮的类名，默认与项目其它次级操作一致 */
  className?: string
  /** 表格等场景下按钮文案过短，需要补上下文 */
  ariaLabel?: string
}

/**
 * 带二次确认的按钮：点击后原地展开确认条，而不是调用原生 `confirm()`。
 *
 * 为什么不用 `window.confirm()` —— 实测（CDP 驱动真实 Chrome 复现）发现，在本项目
 * 的运行环境（内置浏览器预览面板 = sandbox iframe / Electron 内嵌视图）里，原生模态框
 * 会阻塞整个渲染进程：点击「重新生成」后脚本调用超时 8s+、`/rerun` 网络请求数为 0、
 * 页面状态毫无变化 —— 用户看到的就是「点击后没反应」，而且没有任何可排查的线索
 * （网络面板干净、控制台无报错）。
 *
 * 应用内确认没有这些问题：可见、不受宿主限制、可被测试覆盖。
 */
export default function ConfirmButton({
  label,
  question,
  confirmLabel = '确定',
  onConfirm,
  disabled = false,
  className = 'btn ghost',
  ariaLabel,
}: ConfirmButtonProps) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)

  // Esc 退出确认态：误触后必须能退回来，否则按钮等于失效
  useEffect(() => {
    if (!asking) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAsking(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [asking])

  const confirm = async () => {
    setAsking(false)
    setBusy(true)
    try {
      await onConfirm()
    } finally {
      setBusy(false)
    }
  }

  if (asking) {
    return (
      // 表格里按钮常嵌在可点击的 <tr> 内，确认条的点击不能冒泡出去（否则会顺带打开详情）
      <span className="confirm-inline" role="group" onClick={(e) => e.stopPropagation()}>
        <span className="confirm-question">{question}</span>
        <button className="btn small danger" onClick={confirm} disabled={busy}>
          {confirmLabel}
        </button>
        <button className="btn small ghost" onClick={() => setAsking(false)} disabled={busy}>
          取消
        </button>
      </span>
    )
  }

  return (
    <button
      className={className}
      onClick={(e) => {
        e.stopPropagation()
        setAsking(true)
      }}
      disabled={disabled || busy}
      aria-label={ariaLabel}
    >
      {busy ? '处理中…' : label}
    </button>
  )
}
