"""字数硬校验与五级降档。

内化自微博确定性引擎的 ``fit()`` + ``clip()``：**穷举所有组合取不超限的最长
可行解**，而不是估算或截断。模型做不到这种精度，代码可以。

降档优先级（与引擎一致，不是按「改动大小」而是按「保内容」）：

    保要点条数 → 保细节详略 → 砍结语 → 去 emoji

被裁掉的要点不会消失——调用方应保留降档前的完整版（本模块通过
``DowngradeResult.dropped`` 与 ``full_text`` 提供），供长文版或人工参考。

对 LLM 自由文本的适配：引擎的输入是结构化的 ``points[]``，而项目里
``variants[].body`` 是一整段文本。因此先把 body 按句切分成 Segment 并赋
优先级（首句=钩子、其余=要点、末句=结语），再对「要点数 × 细节档 × 结语 ×
emoji」做穷举。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .emoji_policy import POLICY_KEEP, strip_emoji

# 裁剪时可以从句尾剥掉的虚词（内化自微博引擎的 TAIL_BAD）
TAIL_BAD = "和与及的了在是就而或把被对从也都还很非常这那一"
# 优先在此处断句
PAUSE_CHARS = "，。、；：！？"
SHORT_DETAIL_CHARS = 22
THESIS_CHARS = 14
# 组合穷举上限：超出则退化为逐级贪心，避免超长文本把 CPU 打满
MAX_COMBOS = 200

DETAIL_FULL = 2
DETAIL_SHORT = 1
DETAIL_THESIS = 0

DETAIL_LABELS = {
    DETAIL_FULL: "论点+完整细节",
    DETAIL_SHORT: "论点+短细节",
    DETAIL_THESIS: "只留论点",
}

_SENTENCE_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]*")


def clean(value: object) -> str:
    """把任意输入压成单行字符串（内部空白折叠）。"""
    text = str(value if value is not None else "")
    return text.replace("\r", " ").replace("\n", " ").strip()


def clip(value: object, limit: int) -> str:
    """按字符数裁剪，优先在标点处断句并去掉句尾虚词（内化自微博引擎）。

    硬保证：**返回值长度一定 ≤ limit**（除非 limit ≤ 0 返回空串）。
    断句只在 ``text[:limit]`` 窗口内寻找标点，所以不会「断到限额之外」。
    """
    text = str(value if value is not None else "").strip().rstrip("。，！？；：、")
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text

    look = text[:limit]
    cut = -1
    for punct in PAUSE_CHARS:
        index = look.rfind(punct)
        if index > cut and index >= max(3, limit * 0.5):
            cut = index

    if cut > 0:
        # 连同断点标点一起保留，中文截断读起来更完整
        return look[: cut + 1]

    # 没有合适断点：硬裁后剥掉句尾虚词，避免留下「的」「和」这类半截词
    out = look
    while out and out[-1] in TAIL_BAD:
        out = out[:-1]
    return out.rstrip() or look


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点切分（连续标点如「？！」不拆句），换行视为分隔。"""
    if not text:
        return []
    return [chunk.strip() for chunk in _SENTENCE_RE.findall(text) if chunk.strip()]


def _detail_levels(sentence: str) -> tuple[str, str, str]:
    """生成一句话的三档细节：完整 / 短细节 / 只留论点。

    含「：」时以冒号前为论点；否则整句即论点，细节档退化为截断。
    """
    sentence = clean(sentence)
    if "：" in sentence:
        thesis, detail = sentence.split("：", 1)
        thesis, detail = thesis.strip(), detail.strip()
        if thesis and detail:
            return (
                f"{thesis}：{detail}",
                f"{thesis}：{clip(detail, SHORT_DETAIL_CHARS)}",
                thesis,
            )
    short = clip(sentence, SHORT_DETAIL_CHARS)
    return (sentence, short, clip(sentence, THESIS_CHARS))


@dataclass(frozen=True)
class Segment:
    """降档视图中最小可裁剪单元。"""

    text: str
    kind: str  # hook | point | closing
    priority: int
    levels: tuple[str, str, str]  # (完整, 短细节, 只留论点)
    protect: bool = False

    def at(self, mode: int) -> str:
        index = {DETAIL_FULL: 0, DETAIL_SHORT: 1, DETAIL_THESIS: 2}.get(mode, 0)
        return self.levels[index]


@dataclass
class DowngradeResult:
    text: str
    level: int
    fitted: bool
    dropped: list[str] = field(default_factory=list)
    notes: str = ""
    full_text: str = ""
    detail_mode: int = DETAIL_FULL
    points_kept: int = 0
    points_total: int = 0
    closing_kept: bool = True
    emoji_kept: bool = True


def build_segments(body: str) -> tuple[Segment | None, list[Segment], Segment | None]:
    """把一段正文拆成 (钩子, 要点列表, 结语)。

    首句为钩子（永不裁剪），末句在句子数 ≥3 时视为可砍的结语。
    """
    sentences = split_sentences(body)
    if not sentences:
        return None, [], None

    hook = Segment(
        text=sentences[0],
        kind="hook",
        priority=90,
        levels=_detail_levels(sentences[0]),
        protect=True,
    )
    rest = sentences[1:]
    closing: Segment | None = None
    if len(rest) >= 2:
        closing = Segment(
            text=rest[-1],
            kind="closing",
            priority=20,
            levels=_detail_levels(rest[-1]),
        )
        rest = rest[:-1]

    points = [
        Segment(
            text=sentence,
            kind="point",
            priority=60,
            levels=_detail_levels(sentence),
        )
        for sentence in rest
    ]
    return hook, points, closing


def _assemble(
    hook: Segment,
    points: list[Segment],
    closing: Segment | None,
    *,
    keep: int,
    mode: int,
    keep_closing: bool,
    keep_emoji: bool,
) -> str:
    parts = [hook.at(mode)]
    parts.extend(point.at(mode) for point in points[:keep])
    if keep_closing and closing is not None:
        parts.append(closing.at(mode))
    text = "\n".join(part for part in parts if part)
    return text if keep_emoji else strip_emoji(text)


def _derive_level(
    *,
    emoji_kept: bool,
    closing_kept: bool,
    mode: int,
    kept: int,
    total: int,
    closing_exists: bool,
) -> int:
    """把「实际发生了什么」折算成 0-4 的降档深度。"""
    intact = mode == DETAIL_FULL and kept == total
    if intact and closing_kept and emoji_kept:
        return 0
    if intact and closing_kept:
        return 1  # 仅去 emoji
    if intact and not closing_exists:
        return 0  # 本就没有结语可砍，视同未降档
    if intact:
        return 2  # 砍结语
    if kept == total:
        return 3  # 要点都在，仅细节降级
    return 4  # 砍了要点


def downgrade_body(
    body: str,
    max_chars: int,
    *,
    emoji_policy: str = POLICY_KEEP,
    max_keep: int = 3,
) -> DowngradeResult:
    """把一段正文降到 ``max_chars`` 以内，返回结果与降档说明。

    始终保证：若存在可行解，返回的 ``text`` 一定不超限；
    若连「只剩钩子」都放不下，则硬裁到上限并置 ``fitted=False`` 交人工。
    """
    if max_chars <= 0:
        return DowngradeResult(text="", level=0, fitted=True, full_text=clean(body))

    hook, points, closing = build_segments(body)
    if hook is None:
        return DowngradeResult(text="", level=0, fitted=True, full_text="")

    total = len(points)
    full_text = _assemble(
        hook, points, closing, keep=total, mode=DETAIL_FULL, keep_closing=True, keep_emoji=True
    )
    # 原样就在限额内：零改动返回
    if len(full_text) <= max_chars:
        return DowngradeResult(
            text=full_text,
            level=0,
            fitted=True,
            full_text=full_text,
            detail_mode=DETAIL_FULL,
            points_kept=total,
            points_total=total,
        )

    combinations = (total + 1) * 3 * 2 * 2
    exhaustive = combinations <= MAX_COMBOS

    best: tuple[str, int, int, bool, bool] | None = None
    if exhaustive:
        # 与引擎一致：优先多保要点，其次保细节，再次保结语与 emoji
        for keep in range(total, -1, -1):
            for mode in (DETAIL_FULL, DETAIL_SHORT, DETAIL_THESIS):
                for keep_closing in (True, False):
                    for keep_emoji in (True, False):
                        candidate = _assemble(
                            hook,
                            points,
                            closing,
                            keep=keep,
                            mode=mode,
                            keep_closing=keep_closing,
                            keep_emoji=keep_emoji,
                        )
                        if len(candidate) <= max_chars:
                            best = (candidate, keep, mode, keep_closing, keep_emoji)
                            break
                    if best:
                        break
                if best:
                    break
            if best:
                break
    else:
        # 组合过多：逐级贪心（要点全留→细节降级→砍结语→去 emoji→砍要点）
        ladder = [
            (total, DETAIL_FULL, True, True),
            (total, DETAIL_SHORT, True, True),
            (total, DETAIL_THESIS, True, True),
            (total, DETAIL_FULL, False, True),
            (total, DETAIL_FULL, True, False),
            (total, DETAIL_THESIS, False, False),
        ]
        for keep in range(total, -1, -1):
            for mode, keep_closing, keep_emoji in (
                (DETAIL_THESIS, False, False),
                (DETAIL_THESIS, True, False),
                (DETAIL_THESIS, False, True),
            ):
                ladder.append((keep, mode, keep_closing, keep_emoji))
        for keep, mode, keep_closing, keep_emoji in ladder:
            candidate = _assemble(
                hook,
                points,
                closing,
                keep=keep,
                mode=mode,
                keep_closing=keep_closing,
                keep_emoji=keep_emoji,
            )
            if len(candidate) <= max_chars:
                best = (candidate, keep, mode, keep_closing, keep_emoji)
                break

    if best is None:
        # 连「只剩钩子」都放不下：硬裁，并标记「钩子本身被切过」交人工
        hook_clean = strip_emoji(hook.text)
        squeezed = clip(hook_clean, max_chars)
        hard_cut = len(hook_clean) > max_chars
        return DowngradeResult(
            text=squeezed,
            level=4,
            fitted=not hard_cut,
            dropped=[point.text for point in points]
            + ([closing.text] if closing is not None else []),
            notes=(
                f"素材过长：即使只保留开头也超出 {max_chars} 字，已硬裁到上限，请人工精简。"
                if hard_cut
                else f"已降到只剩开头（{len(squeezed)} 字），建议人工补写。"
            ),
            full_text=full_text,
            detail_mode=DETAIL_THESIS,
            points_kept=0,
            points_total=total,
            closing_kept=False,
            emoji_kept=False,
        )

    text, kept, mode, keep_closing, keep_emoji = best
    dropped = [point.text for point in points[kept:]]
    if closing is not None and not keep_closing:
        dropped.append(closing.text)

    level = _derive_level(
        emoji_kept=keep_emoji,
        closing_kept=keep_closing,
        mode=mode,
        kept=kept,
        total=total,
        closing_exists=closing is not None,
    )

    notes_parts: list[str] = []
    if kept < total:
        notes_parts.append(
            f"要点入稿 {kept}/{total} 条，其余 {total - kept} 条已移出正文（完整内容见原稿）"
        )
    if mode != DETAIL_FULL:
        notes_parts.append(f"细节降级为「{DETAIL_LABELS[mode]}」")
    if closing is not None and not keep_closing:
        notes_parts.append("已删除结语")
    if not keep_emoji:
        notes_parts.append("已去除 emoji")
    if not notes_parts:
        notes_parts.append("无降档")

    return DowngradeResult(
        text=text,
        level=level,
        fitted=True,
        dropped=dropped,
        notes="；".join(notes_parts),
        full_text=full_text,
        detail_mode=mode,
        points_kept=kept,
        points_total=total,
        closing_kept=keep_closing,
        emoji_kept=keep_emoji,
    )
