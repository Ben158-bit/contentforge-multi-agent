"""确定性守门层。

把「规则可穷举、要求 100% 可复现」的文案约束从提示词里拿出来，变成可测试、
可审计、零 token 成本的代码。原项目把最该由工具承担的合规风险交给了 LLM 软判断，
本包补上这一层。

设计约束：
- 依赖仅限**标准库 + pyyaml + pydantic**（项目已有），不导入 fastapi / langgraph /
  openai —— 因此既能在 Agent 节点里用，也能被 ``evals/`` 独立复用；
- 全部公开函数是**纯函数**，无 IO 副作用、不调用模型、同输入必同输出；
- 词表与规则全部放在 ``guardrails/rules/*.yaml``，改词表不需要改代码。

与技能包的关系：本包的方法论内化自工作区技能包
（微博确定性引擎的 51 条广告法词表与五级降档、小红书六维标题评分卡与违禁词、
公众号文风拆解），但**不依赖技能包运行**——项目保持零外部依赖与 Docker 可移植。
"""
from __future__ import annotations

from .ai_tells import (
    AiTellHit,
    AiTellRules,
    ai_tell_stats,
    load_ai_tell_rules,
    render_patterns_for_prompt,
    scan_ai_tells,
)
from .compliance import (
    WordEntry,
    WordHit,
    Wordlist,
    load_wordlist,
    scan_text,
    scan_texts,
    split_user_words,
    summarize_hits,
)
from .content_contract import (
    FEED_TARGETS,
    KNOWN_KINDS,
    ContractCheck,
    apply_derived_hashtags,
    check_content_contract,
    contract_from_channel,
    derive_hashtags,
    flatten_tiered_tags,
    gaps_of,
    styled_tiered_hashtags,
    tag_source_field,
    tiered_tags_entry,
)
from .emoji_policy import (
    POLICY_CAP,
    POLICY_KEEP,
    POLICY_REMOVE,
    apply_emoji_policy,
    count_emoji,
    is_emoji,
    policy_from_channel,
    strip_emoji,
)
from .hashtags import (
    STYLE_HASH,
    STYLE_NONE,
    STYLE_PAIRED,
    normalize_tag,
    normalize_tags,
    rules_from_channel,
)
from .length_policy import (
    DETAIL_LABELS,
    DowngradeResult,
    Segment,
    build_segments,
    clip,
    downgrade_body,
    split_sentences,
)
from .postprocess import draft_text, normalize_variant, run_guardrail
from .report import (
    AiTellHitModel,
    ContractCheckModel,
    GuardrailReport,
    TitleCheckModel,
    VariantReport,
    WordHitModel,
)
from .text_metrics import count_chars, count_variant, mode_from_channel, visual_width
from .title_check import TitleCheck, check_title, grade_from_score, weighted_grade

__all__ = [
    # 字数与降档
    "count_chars",
    "count_variant",
    "mode_from_channel",
    "visual_width",
    "clip",
    "split_sentences",
    "build_segments",
    "downgrade_body",
    "Segment",
    "DowngradeResult",
    "DETAIL_LABELS",
    # 合规
    "load_wordlist",
    "scan_text",
    "scan_texts",
    "summarize_hits",
    "split_user_words",
    "WordEntry",
    "WordHit",
    "Wordlist",
    # AI 味
    "load_ai_tell_rules",
    "scan_ai_tells",
    "ai_tell_stats",
    "render_patterns_for_prompt",
    "AiTellHit",
    "AiTellRules",
    # emoji
    "is_emoji",
    "count_emoji",
    "strip_emoji",
    "apply_emoji_policy",
    "policy_from_channel",
    "POLICY_REMOVE",
    "POLICY_CAP",
    "POLICY_KEEP",
    # 标签
    "normalize_tag",
    "normalize_tags",
    "rules_from_channel",
    "STYLE_PAIRED",
    "STYLE_HASH",
    "STYLE_NONE",
    # 内容契约
    "check_content_contract",
    "contract_from_channel",
    "flatten_tiered_tags",
    "tiered_tags_entry",
    "derive_hashtags",
    "styled_tiered_hashtags",
    "apply_derived_hashtags",
    "tag_source_field",
    "gaps_of",
    "ContractCheck",
    "KNOWN_KINDS",
    "FEED_TARGETS",
    # 标题
    "check_title",
    "weighted_grade",
    "grade_from_score",
    "TitleCheck",
    # 编排
    "run_guardrail",
    "normalize_variant",
    "draft_text",
    "GuardrailReport",
    "VariantReport",
    "TitleCheckModel",
    "WordHitModel",
    "AiTellHitModel",
    "ContractCheckModel",
]
