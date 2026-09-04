# ContentForge 前端

React + Vite + TypeScript 单页应用，用于展示多 Agent 营销内容生产流水线。

## 开发

```bash
npm install
npm run dev
```

默认开发地址为 `http://localhost:5173`，前端通过 Vite 代理访问本地 FastAPI 后端。

## 验证

```bash
npm run build
npm test
```

当前包含任务列表、流水线阶段视图、文案编辑、品牌管理和反馈面板等界面，并由 GitHub Actions 自动执行类型检查、构建和 15 项前端测试。
