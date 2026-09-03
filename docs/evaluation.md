# ContentForge 评测报告

## 评测目的

本评测区分两类问题：工程可重复性，以及真实模型效果。Fake 基线只能回答工程可重复性，不能证明真实文案质量提升。

## 数据集

- 文件：`evals/dataset.jsonl`
- 样本数：30 条
- 渠道：小红书 9、微信公众号 9、微博 6、电商产品页 6
- 全部为虚构品牌和营销场景
- 每条包含主题、品牌、目标受众、渠道、额外要求、禁用词、硬约束和复核备注
- 数据集加载与校验：`evals/test_dataset.py`

## 自动评分

| 指标 | 判定方式 |
|---|---|
| Schema 通过率 | 变体为非空列表，且包含 title/body/hashtags/notes |
| 硬约束通过率 | 必须短语均出现，且不含禁用词、不超过渠道长度 |
| 品牌对齐率 | 品牌名出现在输出文本中 |
| 成本 | 按现有 DeepSeek 基准单价估算 token |
| 延迟 | Provider 返回的单次调用耗时 |

评分器是纯函数，不依赖网络、FastAPI、LangGraph 或数据库。LLM-as-judge 不作为唯一验收依据。

## FakeLLM 基线

运行命令：

~~~powershell
Set-Location 'D:\何泽斌\Documents\cursor项目\contentforge'
.\.venv\Scripts\python.exe evals/run_eval.py --dataset evals/dataset.jsonl --provider fake --output evals/results/baseline-fake.json --seed 42
~~~

结果文件：`evals/results/baseline-fake.json`

| 指标 | 结果 |
|---|---:|
| 样本数 | 30 |
| 成功数 | 30 |
| 错误数 | 0 |
| Schema 通过率 | 100% |
| 硬约束通过率 | 100% |
| 品牌对齐率 | 100% |
| 平均成本 | ¥0.0006 |
| 平均延迟 | 0.001 秒 |

Fake Provider 是按样本字段生成的确定性测试夹具。100% 结果表示评测管线、评分器和成本统计可重复，不表示 DeepSeek 的真实质量或合规率。

## DeepSeek 基线

正式基线应固定数据集、Prompt、模型、temperature、seed 和超时参数。建议先跑跨渠道 5 条试跑，再决定是否跑完整 30 条。API Key 从 `backend/.env` 读取，不写入结果文件；真实调用前确认成本。

## 人工复核

人工复核表位于 `docs/evaluation-human-review.md`。建议对 10 条跨渠道样本按 1-5 分评价相关性、品牌一致性、渠道适配、约束遵守、可用性和 CTA。规则失败样本必须追加复核；规则通过样本随机抽样复核。没有人工复核证据时，不对质量提升作结论。

## 回归门槛

初始门槛存于 `evals/thresholds.json`：Schema >= 0.98、硬约束 >= 0.90、平均成本 <= 基线 150%、平均延迟 <= 基线 200%。使用 `evals/check_regression.py` 检查候选结果；CI 只运行 Fake Provider。
