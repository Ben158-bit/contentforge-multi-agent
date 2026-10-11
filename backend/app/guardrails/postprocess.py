"""守门层编排：归一化 → 检出 → 报告。

拆成两步的理由：**报告必须与产物作用于同一份规范化文本**，否则
「报出来的问题」和「实际交付的文案」对不上。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .ai_tells import AiTellHit, ai_tell_stats, scan_ai_tells
from .compliance import WordHit, scan_text
from .content_contract import (
    check_content_contract,
    gaps_of,
    styled_tiered_hashtags,
)
from .emoji_policy import apply_emoji_policy, count_emoji, policy_from_channel
from .hashtags import normalize_tags, rules_from_channel
from .length_policy import DowngradeResult, downgrade_body
from .report import (
    AiTellHitModel,
    ContractCheckModel,
    GuardrailReport,
    TitleCheckModel,
    VariantReport,
    WordHitModel,
)
from .text_metrics import count_variant, mode_from_channel
from .title_check import check_title

# 文案变体的标准字段
VARIANT_FIELDS = ("title", "body", "hashtags", "notes")


def _word_hit_model(hit: WordHit) -> WordHitModel:
    return WordHitModel(**hit.to_dict())


def _ai_tell_model(hit: AiTellHit) -> AiTellHitModel:
    return AiTellHitModel(**hit.to_dict())


def _title_model(check: Any) -> TitleCheckModel:
    return TitleCheckModel(**check.to_dict())


@dataclass
class NormalizeOutcome:
    """单条变体归一化的完整留痕，供报告与单测使用。"""

    variant: dict[str, Any]
    original_body: str
    hashtags_before: list[str] = field(default_factory=list)
    hashtag_notes: list[str] = field(default_factory=list)
    tag_source_notes: list[str] = field(default_factory=list)
    emoji_before: int = 0
    emoji_after: int = 0
    emoji_notes: list[str] = field(default_factory=list)
    downgrade: DowngradeResult | None = None
    pre_downgrade_body: str = ""


def _coerce_hashtags(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _tags_before(variant: dict[str, Any], channel: dict) -> tuple[list[str], list[str]]:
    """确定性的「标签输入」：分层来源优先，模型自带的 hashtags 只作兜底。

    渠道若声明了 tiered 标签来源（``feeds: hashtags``），标签的**唯一真源**
    就是那个分层结构 —— 否则「hashtags」与「分层结果」会变成两处真源，
    而「按层核验标签配比」正好依赖后者的可信度。

    正常情况下创作节点出口已经推导过一次（见 ``apply_derived_hashtags``），
    这里的推导是**兜底**：直接调用 ``run_guardrail`` 的调用方（测试、评测）
    未必经过节点。分层结构没填出来时**不静默清空标签**，
    退回模型给的 ``hashtags``，缺口交给 ``content_contract`` 去报。
    """
    raw = _coerce_hashtags(variant.get("hashtags"))
    derived = styled_tiered_hashtags(variant, channel)
    if not derived:
        return raw, []
    notes: list[str] = []
    if derived != raw:
        notes.append(f"标签以分层结构为准，扁平化得到 {len(derived)} 个")
    return derived, notes


def normalize_variant(
    variant: dict[str, Any],
    channel: dict,
    *,
    fallback_topic: str | None = None,
    max_chars: int | None = None,
) -> NormalizeOutcome:
    """对单条变体做可安全自动修的部分：emoji 策略、标签规范化、字数降档。

    只做「删除信息以外、不改变语义」的动作；违禁词替换与 AI 味改写
    一律只标记，交人工或 humanize 节点处理。
    """
    body = str(variant.get("body") or "")
    title = str(variant.get("title") or "")
    notes = str(variant.get("notes") or "")
    tags_before, tag_source_notes = _tags_before(variant, channel)

    limit = max_chars if max_chars is not None else int(channel.get("max_chars") or 0)
    emoji_policy = policy_from_channel(channel)

    emoji_before = count_emoji(body) + count_emoji(title)
    body, body_emoji_notes = apply_emoji_policy(body, emoji_policy)
    title, title_emoji_notes = apply_emoji_policy(title, emoji_policy)

    tag_rules = rules_from_channel(channel)
    tags_after, tag_notes = normalize_tags(
        tags_before,
        style=tag_rules["style"],
        min_count=tag_rules["min_count"],
        max_count=tag_rules["max_count"],
        fallback_topic=fallback_topic,
    )

    pre_downgrade = body
    if limit > 0:
        downgrade = downgrade_body(body, limit, emoji_policy=emoji_policy)
        body = downgrade.text
    else:
        # 渠道没配 max_chars：不做降档。注意不能把 0 传给 downgrade_body——
        # 它的语义是「预算为 0，什么都放不下」，会把正文清空。
        downgrade = DowngradeResult(text=body, level=0, fitted=True, full_text=body)

    emoji_after = count_emoji(body) + count_emoji(title)

    new_variant: dict[str, Any] = {
        "title": title,
        "body": body,
        "hashtags": tags_after,
        "notes": notes,
    }
    # 保留模型可能给出的渠道专属字段（如微博 hook_type、产品页 selling_points）
    for key, value in variant.items():
        if key not in VARIANT_FIELDS:
            new_variant[key] = value

    return NormalizeOutcome(
        variant=new_variant,
        original_body=str(variant.get("body") or ""),
        hashtags_before=tags_before,
        hashtag_notes=tag_notes,
        tag_source_notes=tag_source_notes,
        emoji_before=emoji_before,
        emoji_after=emoji_after,
        emoji_notes=body_emoji_notes + title_emoji_notes,
        downgrade=downgrade,
        pre_downgrade_body=pre_downgrade,
    )


def draft_text(variant: dict[str, Any]) -> str:
    """成稿扫描范围：标题 + 正文 + 标签（不含 notes——那是模型自述，不对外发布）。

    这是「哪些字段算对外交付物」的**唯一真源**。评测脚本（``evals/metrics.py``）
    也用它，否则评测会拿一套比生产更宽的范围去判违规：模型在 notes 里写
    「本篇刻意避开了『最好』这个词」会被判成命中违禁词。
    """
    tags = _coerce_hashtags(variant.get("hashtags"))
    parts = [str(variant.get("title") or ""), str(variant.get("body") or ""), " ".join(tags)]
    return "\n".join(part for part in parts if part)


def run_guardrail(
    variants: Iterable[dict[str, Any]],
    channel: dict,
    *,
    channel_id: str = "",
    source_texts: Iterable[str] | None = None,
    user_banned: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
    fallback_topic: str | None = None,
) -> tuple[list[dict[str, Any]], GuardrailReport]:
    """跑完整守门流程。

    Args:
        variants: LLM 产出的文案变体列表（不会被就地修改）。
        channel: 渠道配置（来自 ``channels.yaml``）。
        channel_id: 渠道 id（仅用于报告；缺省时从 channel 里取）。
        source_texts: 输入素材文本，用 ``scope="source"`` 单独扫描——
            素材自带的违禁词不计入成稿违规（对齐内化的微博引擎做法）。
        user_banned: 品牌自定义禁用词。
        keywords: 调研关键词，用于标题体检的「主题匹配」维度。
        fallback_topic: 标签数量不足时用于兜底的主题。

    Returns:
        (归一化后的变体列表, 守门报告)
    """
    channel_id = channel_id or str(channel.get("name") or "")
    max_chars = int(channel.get("max_chars") or 0)
    mode = mode_from_channel(channel)
    title_min = int(channel.get("title_min_chars") or 0)
    title_max = int(channel.get("title_max_chars") or 0)
    # 标签风格与 normalize_tags 内部用的是同一份推导（rules_from_channel），
    # 保证「前端预览的形态」与「守门层实际落库的标签」一致。
    tag_style = rules_from_channel(channel)["style"]

    variant_list = [v for v in variants if isinstance(v, dict)]
    fixed: list[dict[str, Any]] = []
    variant_reports: list[VariantReport] = []
    auto_fixed: list[str] = []
    needs_human: list[str] = []
    contract_gaps: list[str] = []

    for index, variant in enumerate(variant_list):
        outcome = normalize_variant(
            variant, channel, fallback_topic=fallback_topic, max_chars=max_chars
        )
        normalized = outcome.variant
        fixed.append(normalized)

        # 内容契约核验：把 copywriting_guide 里可穷举的要求变成可核验的结论。
        contract_checks = check_content_contract(normalized, channel)

        downgrade = outcome.downgrade or DowngradeResult(text="", level=0, fitted=True)
        breakdown = count_variant(normalized, mode)
        body_chars = breakdown["body"]
        hard_cut = not downgrade.fitted
        over_limit = bool(max_chars) and body_chars > max_chars

        label = f"变体{index + 1}"
        if outcome.emoji_notes:
            auto_fixed.extend(f"{label}：{note}" for note in outcome.emoji_notes)
        if outcome.tag_source_notes:
            auto_fixed.extend(f"{label}：{note}" for note in outcome.tag_source_notes)
        if outcome.hashtag_notes:
            auto_fixed.extend(f"{label}：{note}" for note in outcome.hashtag_notes)
        if downgrade.level > 0:
            auto_fixed.append(
                f"{label}：正文 {len(outcome.pre_downgrade_body)} → {body_chars} 字"
                f"（降档 L{downgrade.level}：{downgrade.notes}）"
            )
        if hard_cut:
            needs_human.append(f"{label}：{downgrade.notes or '正文已硬裁至渠道上限'}")
        if over_limit:
            needs_human.append(
                f"{label}：正文 {body_chars} 字仍超出渠道上限 {max_chars} 字，需人工精简"
            )

        # 内容契约缺口：进 needs_human 让人和 review 都看到，但**不改变 passed** ——
        # passed 只表达硬合规；「内容够不够好」是 review 的判断，不是工具的。
        for gap in gaps_of(contract_checks):
            message = f"{label}：{gap.summary()}"
            contract_gaps.append(message)
            needs_human.append(message)

        draft_hits = scan_text(
            draft_text(normalized), scope="draft", user_banned=user_banned
        )
        ai_hits = scan_ai_tells(str(normalized.get("body") or ""))
        title_check = check_title(
            normalized.get("title", ""),
            min_chars=title_min,
            max_chars=title_max,
            keywords=keywords,
            user_banned=user_banned,
        )

        variant_reports.append(
            VariantReport(
                index=index,
                body_chars=body_chars,
                max_chars=max_chars,
                over_limit=over_limit,
                hard_cut=hard_cut,
                char_breakdown=breakdown,
                downgraded=downgrade.level > 0,
                downgrade_level=downgrade.level,
                downgrade_notes=downgrade.notes,
                dropped=downgrade.dropped,
                hashtags_before=outcome.hashtags_before,
                hashtags_after=list(normalized.get("hashtags") or []),
                hashtag_notes=outcome.hashtag_notes,
                emoji_before=outcome.emoji_before,
                emoji_after=outcome.emoji_after,
                emoji_notes=outcome.emoji_notes,
                word_hits=[_word_hit_model(hit) for hit in draft_hits],
                ai_tells=[_ai_tell_model(hit) for hit in ai_hits],
                title=_title_model(title_check),
                contract=[
                    ContractCheckModel(**check.to_dict()) for check in contract_checks
                ],
            )
        )

    # 汇总成稿命中与致命 AI 味
    draft_hits: list[WordHit] = []
    ai_hits_all: list[AiTellHit] = []
    for variant in fixed:
        draft_hits.extend(
            scan_text(draft_text(variant), scope="draft", user_banned=user_banned)
        )
        ai_hits_all.extend(scan_ai_tells(str(variant.get("body") or "")))

    source_hits: list[WordHit] = []
    for text in source_texts or []:
        if text:
            source_hits.extend(scan_text(str(text), scope="source", user_banned=user_banned))

    tell_stats = ai_tell_stats(ai_hits_all)
    fatal_count = int(tell_stats.get("fatal", 0))

    if draft_hits:
        needs_human.append(
            f"成稿命中违禁词/广告法用语 {len(draft_hits)} 处，需人工确认替换："
            + "、".join(sorted({hit.word for hit in draft_hits}))
        )
    if fatal_count:
        needs_human.append(
            f"成稿含 {fatal_count} 处致命 AI 味句式（如「不是 X，而是 Y」），建议改写"
        )
    for report in variant_reports:
        if report.title is not None and not report.title.length_ok:
            needs_human.append(
                f"变体{report.index + 1}：标题长度不合规（{report.title.length} 字）"
            )

    passed = (
        bool(fixed)
        and not any(r.over_limit or r.hard_cut for r in variant_reports)
        and not draft_hits
    )
    if fatal_count:
        passed = False

    report = GuardrailReport(
        channel_id=channel_id,
        max_chars=max_chars,
        hashtag_style=tag_style,
        variant_reports=variant_reports,
        draft_hits=[_word_hit_model(hit) for hit in draft_hits],
        source_hits=[_word_hit_model(hit) for hit in source_hits],
        passed=passed,
        auto_fixed=auto_fixed,
        needs_human=needs_human,
        contract_gaps=contract_gaps,
        stats={
            "variants": len(fixed),
            "over_limit": sum(1 for r in variant_reports if r.over_limit),
            "hard_cut": sum(1 for r in variant_reports if r.hard_cut),
            "downgraded": sum(1 for r in variant_reports if r.downgraded),
            "draft_hits": len(draft_hits),
            "source_hits": len(source_hits),
            "auto_fixed": len(auto_fixed),
            "needs_human": len(needs_human),
            "contract_gaps": len(contract_gaps),
        },
        ai_tell_stats=tell_stats,
    )
    return fixed, report
