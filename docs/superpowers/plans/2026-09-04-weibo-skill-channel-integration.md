# 微博电商推理 Skill 渠道接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让用户选择微博渠道时，现有文案创作和审校 Agent 自动使用微博电商推理规则。

**Architecture:** 沿用 channels.yaml 作为渠道能力注册表。扩充 weibo.copywriting_guide，由现有 Prompt 构建器同时注入文案创作和审校阶段；不新增 loader、API 或前端状态。

**Tech Stack:** Python, pytest, PyYAML, LangGraph prompt builders

---

### Task 1: 验证微博规则注入契约

**Files:**
- Modify: backend/tests/test_prompts.py（按现有测试目录实际文件调整）
- Test: backend/tests/test_prompts.py

- [ ] **Step 1: 写失败测试**：构造 channel_id 为 weibo 的最小 AgentState，断言 copywriting user 消息包含“现象 → 机制 → 动作 → 指标/结果”、多样本证据边界和 280 字约束；再断言 xiaohongshu 消息不包含微博专属推理文本。
- [ ] **Step 2: 运行目标测试确认失败**：pytest backend/tests/test_prompts.py -q，预期新增断言因渠道 YAML 尚未包含这些规则而失败。
- [ ] **Step 3: 扩充微博渠道指南**：在 backend/app/templates/channels.yaml 的 weibo.copywriting_guide 增加推理结构、表达模式、研究证据和合规边界字段。
- [ ] **Step 4: 重新运行目标测试**：预期全部通过。

### Task 2: 回归验证与文档核对

**Files:**
- Modify: backend/app/templates/channels.yaml
- Test: backend/tests/ 现有模板和 Prompt 测试

- [ ] **Step 1: 运行模板加载测试**：pytest backend/tests -q，确认 YAML 和所有渠道 Prompt 合同没有回归。
- [ ] **Step 2: 检查微博输出约束**：确认现有 weibo 分支仍要求 280 字、2-4 个话题标签和 JSON 输出。
- [ ] **Step 3: 检查 Git diff**：git diff --check，确保无空白错误和无关文件变更。
