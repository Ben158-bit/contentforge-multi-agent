"""去 AI 味改写节点（humanize）：**混合闭环**，只对公众号启用。

为什么是「混合」而不是纯 Agent
------------------------------
纯 Agent 改写有两个不可接受的失败模式：① 把好文案改坏（丢事实、丢卖点）；
② 改完仍然有 AI 味却没人知道。所以这里套一圈**确定性护栏**：

```
确定性预扫 → Agent 改写 → 确定性复扫 → 验收判定 → 采纳 / 保留原文
```

- **预扫 / 复扫**复用守门层的 ``scan_ai_tells``（同一份 ``ai_tells_zh.yaml``），
  所以「守门层扫的词」与「模型被告知要改的词」不会漂移；
- **验收判定是纯函数**（``evaluate_rewrite``）：命中数必须**严格下降**、
  不得引入新的致命句式、长度不得塌陷。任何一条不满足就**保留原文**。
- **失败永远降级为原文**：模型报错、返回非 JSON、返回空正文，一律保留原文并
  把原因写进报告，任务继续。否则「去 AI 味」会变成「把交付物搞丢」。

为什么放在 ``copywriting`` 与 ``guardrail`` 之间
-----------------------------------------------
守门层报告必须描述**最终交付物**（P3 的不变式：报告与实际检查过的文案对齐）。
若把改写放在守门层之后，报告就会描述一份已经被改写掉的文字，两边对不上。
放在前面还有额外好处：改写若引入违禁词或超长，守门层照样能兜住。

为什么默认只覆盖公众号
----------------------
长文最容易堆砌套话，收益最高；小红书/微博是短句口语体，改写风险大于收益。
这是**渠道级**开关，不做「每任务开关」，因此不需要数据库迁移。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...guardrails import AiTellHit, ai_tell_stats, scan_ai_tells
from ...templates import get_channel
from ..prompts import build_humanize_messages
from ..state import AgentState
# 以别名导入：本模块的工厂形参也叫 temperature，直接导入会被形参遮蔽
from ._common import accumulate_all, safe_json
from ._common import temperature as resolve_temperature

logger = logging.getLogger(__name__)

DEFAULT_STATE_KEY = "humanize"

# 启用的渠道：公众号长文（渠道级开关，非每任务开关 → 无需 DB 迁移）
ENABLED_CHANNEL_ID = "wechat"

# 触发改写的级别：info 级只是排版提示（半角标点、粗体密度），
# 为此花一次模型调用既不划算、也容易把文案改跑偏。
REWRITE_SEVERITIES = ("fatal", "warning")

# 单次执行最多改写多少个变体（成本闸门：一次执行最多 3 次改写调用）
MAX_REWRITES_PER_RUN = 3

# 改写后正文不得短于原文的多少（防「删掉一整段」式伪改写）
MIN_LENGTH_RATIO = 0.5

DEFAULT_TEMPERATURE = 0.4

# 单条变体的处置结论
DECISION_ACCEPTED = "accepted"    # 采纳改写
DECISION_REJECTED = "rejected"    # 改写未通过验收 → 保留原文
DECISION_SKIPPED = "skipped"      # 无需改写（未检出 warning/fatal，或超出预算）
DECISION_FAILED = "failed"        # 调用/解析失败 → 保留原文

# 阶段状态：渠道不适用时用 skipped，而不是 completed ——
# 让「这一阶段跑了但什么也没做」在流水线上可见，而不是伪装成「已完成」。
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"


class HumanizeVariantReport(BaseModel):
    """单条变体的改写处置记录（前后对比，让降级原因可追溯）。"""

    index: int
    decision: str
    reason: str = ""
    ai_tells_before: int = 0
    ai_tells_after: int = 0
    fatal_before: int = 0
    fatal_after: int = 0
    body_chars_before: int = 0
    body_chars_after: int = 0
    changes: list[str] = Field(default_factory=list)


class HumanizeReport(BaseModel):
    """整次去 AI 味结果。``enabled=False`` 表示该渠道未启用（不消耗 token）。"""

    enabled: bool
    channel_id: str
    reason: str = ""
    variant_reports: list[HumanizeVariantReport] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


def needs_rewrite(hits: list[AiTellHit]) -> bool:
    """只有致命/警告级命中才值得改写（info 级是排版提示，不改）。"""
    return any(hit.severity in REWRITE_SEVERITIES for hit in hits)


def evaluate_rewrite(
    *,
    before: dict[str, int],
    after: dict[str, int],
    chars_before: int,
    chars_after: int,
    min_ratio: float = MIN_LENGTH_RATIO,
) -> tuple[bool, str]:
    """验收判定（**纯函数**，无 IO、无模型）。

    采纳条件（全部满足）：

    1. 改写结果非空；
    2. AI 味命中总数**严格下降** —— 没减少就不算「去味成功」，
       没理由用一份改动过的文字替换原文；
    3. 没有引入新的致命句式（fatal 不得变多）；
    4. 长度没有塌陷（不少于原文 ``min_ratio``）——
       防止「删掉一整段」式的伪改写把信息一起丢掉。

    Returns:
        ``(是否采纳, 原因)``，原因会原样写进报告，供人工复查。
    """
    if chars_after <= 0:
        return False, "改写结果为空，保留原文"

    before_total = int(before.get("total", 0))
    after_total = int(after.get("total", 0))
    before_fatal = int(before.get("fatal", 0))
    after_fatal = int(after.get("fatal", 0))

    if after_fatal > before_fatal:
        return False, (
            f"改写引入了新的致命 AI 味句式（fatal {before_fatal} → {after_fatal}），保留原文"
        )
    if after_total >= before_total:
        return False, (
            f"AI 味命中未减少（{before_total} → {after_total}），改写无收益，保留原文"
        )
    if chars_before > 0 and chars_after < chars_before * min_ratio:
        return False, (
            f"改写后正文明显变短（{chars_before} → {chars_after} 字，"
            f"低于原文的 {int(min_ratio * 100)}%），可能丢信息，保留原文"
        )
    return True, (
        f"AI 味命中 {before_total} → {after_total}，正文 {chars_before} → {chars_after} 字"
    )


def _stats_summary(reports: list[HumanizeVariantReport], rewrites: int) -> dict[str, Any]:
    def _count(decision: str) -> int:
        return sum(1 for report in reports if report.decision == decision)

    return {
        "variants": len(reports),
        "rewrites": rewrites,
        "accepted": _count(DECISION_ACCEPTED),
        "rejected": _count(DECISION_REJECTED),
        "skipped": _count(DECISION_SKIPPED),
        "failed": _count(DECISION_FAILED),
        "ai_tells_before": sum(report.ai_tells_before for report in reports),
        "ai_tells_after": sum(report.ai_tells_after for report in reports),
    }


def _disabled(state_key: str, channel_id: str) -> dict:
    report = HumanizeReport(
        enabled=False,
        channel_id=channel_id,
        reason=(
            f"去 AI 味改写仅对公众号（{ENABLED_CHANNEL_ID}）启用，"
            f"当前渠道为 {channel_id or '未指定'}"
        ),
        stats=_stats_summary([], 0),
    )
    return {
        state_key: report.model_dump(),
        "stage_status": {state_key: STATUS_SKIPPED},
    }


def make_humanize_node(
    llm,
    *,
    temperature: float | None = None,
    state_key: str = DEFAULT_STATE_KEY,
) -> Callable:
    """去 AI 味改写节点工厂。

    Args:
        llm: LLM 客户端（注入，便于测试用 fake）。
        temperature: 由流水线定义提供；未声明时用内置默认值。
        state_key: 报告写入的状态通道，由流水线定义的 ``contract.state_key`` 提供。
    """
    temp = resolve_temperature(DEFAULT_TEMPERATURE, temperature)

    def node(state: AgentState) -> dict:
        inp = state.get("input") or {}
        channel_id = str(inp.get("channel_id") or "")

        if channel_id != ENABLED_CHANNEL_ID:
            return _disabled(state_key, channel_id)

        channel = get_channel(channel_id)
        variants = [v for v in (state.get("copywriting") or []) if isinstance(v, dict)]

        out_variants: list[dict] = []
        reports: list[HumanizeVariantReport] = []
        results: list[Any] = []
        rewrites = 0

        for index, variant in enumerate(variants):
            body = str(variant.get("body") or "")
            hits_before = scan_ai_tells(body)
            stats_before = ai_tell_stats(hits_before)

            def _keep(decision: str, reason: str, stats_after: dict | None = None,
                      *, changes: list[str] | None = None, chars_after: int | None = None) -> None:
                """保留原文 + 记账（三个降级分支共用，避免各写一份导致口径不一）。"""
                after = stats_after or stats_before
                out_variants.append(variant)
                reports.append(
                    HumanizeVariantReport(
                        index=index,
                        decision=decision,
                        reason=reason,
                        ai_tells_before=int(stats_before.get("total", 0)),
                        ai_tells_after=int(after.get("total", 0)),
                        fatal_before=int(stats_before.get("fatal", 0)),
                        fatal_after=int(after.get("fatal", 0)),
                        body_chars_before=len(body),
                        body_chars_after=len(body) if chars_after is None else chars_after,
                        changes=list(changes or []),
                    )
                )

            if not needs_rewrite(hits_before):
                _keep(DECISION_SKIPPED, "未检出 warning/fatal 级 AI 味，无需改写")
                continue

            if rewrites >= MAX_REWRITES_PER_RUN:
                _keep(
                    DECISION_SKIPPED,
                    f"超出单次改写上限（{MAX_REWRITES_PER_RUN} 个变体），本变体保留原文",
                )
                continue

            rewrites += 1
            try:
                messages = build_humanize_messages(state, variant, hits_before, channel)
                result = llm.chat(messages, json_mode=True, temperature=temp)
                results.append(result)
                payload = safe_json(result.content)
            except Exception as exc:  # noqa: BLE001 - 改写失败必须降级，不能拖垮任务
                logger.warning("去 AI 味改写失败，保留原文（变体%d）：%s", index + 1, exc)
                _keep(DECISION_FAILED, f"改写调用失败，已保留原文：{exc}")
                continue

            if not isinstance(payload, dict):
                _keep(DECISION_FAILED, "改写返回的不是 JSON 对象，已保留原文")
                continue

            rewritten = str(payload.get("body") or "").strip()
            if not rewritten:
                # 与「改写更差」区分开：这里是**没有得到可用结果**，
                # 归 failed；有结果但没过验收才归 rejected。
                _keep(DECISION_FAILED, "改写返回的正文为空，已保留原文")
                continue
            changes = [
                str(item) for item in (payload.get("changes") or []) if str(item).strip()
            ]
            stats_after = ai_tell_stats(scan_ai_tells(rewritten))
            accepted, reason = evaluate_rewrite(
                before=stats_before,
                after=stats_after,
                chars_before=len(body),
                chars_after=len(rewritten),
            )

            if accepted:
                # 只替换 body，其余字段（notes / 渠道专属字段）保持模型原样。
                out_variants.append({**variant, "body": rewritten})
                reports.append(
                    HumanizeVariantReport(
                        index=index,
                        decision=DECISION_ACCEPTED,
                        reason=reason,
                        ai_tells_before=int(stats_before.get("total", 0)),
                        ai_tells_after=int(stats_after.get("total", 0)),
                        fatal_before=int(stats_before.get("fatal", 0)),
                        fatal_after=int(stats_after.get("fatal", 0)),
                        body_chars_before=len(body),
                        body_chars_after=len(rewritten),
                        changes=changes,
                    )
                )
            else:
                _keep(
                    DECISION_REJECTED,
                    reason,
                    stats_after,
                    changes=changes,
                    chars_after=len(rewritten),
                )

        report = HumanizeReport(
            enabled=True,
            channel_id=channel_id,
            variant_reports=reports,
            stats=_stats_summary(reports, rewrites),
        )
        update: dict[str, Any] = {
            state_key: report.model_dump(),
            # 回写：下游（guardrail / review / 人工确认）看到的必须是
            # 这次实际采纳过的正文（改写成功即为改写稿，失败即为原文）。
            "copywriting": out_variants,
            "stage_status": {state_key: STATUS_COMPLETED},
        }
        update.update(accumulate_all(state, results))
        logger.info(
            "去 AI 味[%s]完成：%d 个变体，改写 %d 次，采纳 %d / 拒绝 %d / 失败 %d",
            state_key, len(variants), rewrites,
            report.stats.get("accepted", 0), report.stats.get("rejected", 0),
            report.stats.get("failed", 0),
        )
        return update

    return node
