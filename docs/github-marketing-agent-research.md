# GitHub 营销 Agent 方法调研

调研日期：2026-09-04

## 采用的参考

- [Marketing Practitioner](https://github.com/quocbao201104/marketing-practitioner)：采用其 decision-first、冻结已解决状态、区分 observation/interpretation/hypothesis/decision、证据匹配声明强度的思想。
- [Ad Copy Generator](https://github.com/karanjindaldev/ad-copy-generator)：参考角色、上下文、约束、结构化 JSON 的 Prompt 顺序。
- [StorePilot](https://github.com/mh-anwar/StorePilot)：仅参考结构化输出、审批门和审计思路；不移植其 Shopify 工作流。

## 未直接采用

- [GPT Copywriting Agency](https://github.com/ebabsjhh/GPT-Copywriting-Agency)：示例偏 AIDA/PAS，仓库内容不完整，缺少可核验评测。
- [AI Copywriting Prompts](https://github.com/Archibaldys/ai-copywriting-prompts)：主要是产品宣传页，缺少可靠的样本和测试证据。

## 在 ContentForge 中的落点

backend/app/agents/prompts.py 增加统一的营销任务控制；微博和电商产品页的具体约束仍由 backend/app/templates/channels.yaml 管理。这样选择渠道时自动生效，同时避免把一个外部通用 skill 原样复制进项目。
