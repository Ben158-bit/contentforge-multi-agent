import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

// jsdom 环境下 `import.meta.url` 不是 file: 协议（fileURLToPath 会抛错），
// 因此以 vitest 的 cwd（= frontend/）定位 src。
const SRC = join(process.cwd(), 'src')

/** 递归收集源码文件（跳过测试自身，避免把断言里的字面量当成违规）。 */
function sourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      out.push(...sourceFiles(full))
    } else if (/\.tsx?$/.test(entry.name) && !entry.name.includes('.test.')) {
      out.push(full)
    }
  }
  return out
}

/**
 * 去掉块注释与整行行注释后再判断是否为真实调用。
 *
 * 关键点：替换时**保留原有换行**，否则行号会错位、指向错误的位置。
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ''))
    .split('\n')
    .map((line) => (/^\s*\/\//.test(line) ? '' : line))
    .join('\n')
}

/** 匹配原生对话框调用：`window.confirm(` 或裸的 `confirm(`（JS 里两者等价）。 */
const NATIVE_DIALOG = /\b(window\.)?(confirm|alert|prompt)\s*\(/

/**
 * 禁用原生对话框。
 *
 * 起因是一次真实故障：任务详情的「重新生成」用 `window.confirm()` 做确认，
 * 在内置浏览器预览面板（sandbox iframe / Electron 内嵌视图）里点击后**毫无反应** ——
 * CDP 探针实测确认框会阻塞渲染进程（`Runtime.evaluate` 超时 8s+），而 `/rerun`
 * 请求数为 0、页面状态不变，用户既看不到弹窗也拿不到任何反馈。
 *
 * 一律改用 `ConfirmButton`（应用内确认：可见、可控、可测）。
 */
describe('不使用原生对话框', () => {
  it('源码中不存在 confirm / alert / prompt 调用', () => {
    expect(existsSync(SRC), `找不到源码目录：${SRC}`).toBe(true)

    const offenders: string[] = []

    for (const file of sourceFiles(SRC)) {
      const lines = stripComments(readFileSync(file, 'utf8')).split('\n')
      lines.forEach((line, i) => {
        if (NATIVE_DIALOG.test(line)) {
          offenders.push(`${relative(SRC, file)}:${i + 1}  ${line.trim()}`)
        }
      })
    }

    expect(offenders).toEqual([])
  })
})
