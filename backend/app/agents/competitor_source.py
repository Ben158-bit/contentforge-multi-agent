"""竞品数据源：从红狐 Hub 抓取真实竞品内容（小红书笔记 / 公众号文章 / 微博账号）。

为什么要这个模块
------------------
竞品分析 Agent 此前是纯 LLM：竞品定位、卖点、内容策略全靠模型内部知识编，
没有真实竞品实例，用户无法核验「这个竞品到底存不存在、火不火」。

这里把「抓真实竞品数据」做成一个可注入的数据源（与 research 的 ``web_search``
同一个模式）：抓到的真实内容既注入提示词让 Agent 基于它做分析，也作为
``evidence`` 字段附在竞品分析结果里 —— 前端可展示、用户可点击溯源。

设计原则
--------
1. **渠道 → 红狐端点映射是单一真源**（只在本文件声明）。新增/调整渠道只改这里。
2. **优雅降级**：无 ``REDFOX_API_KEY`` / 未知渠道 / 网络失败 / 接口报错，
   一律返回空列表，绝不抛异常中断流水线 —— 竞品分析退回模型内部知识。
3. **零新依赖**：``urllib`` 直连（与既有 ``xiaohongshu-write`` 技能脚本同款），
   不引入 ``redfox-python-sdk``。带两级网络回退：先直连（禁系统代理），
   失败再走系统代理 —— 兼容本机「代理指向 127.0.0.1:55045」的环境。

三渠道能力不对等（红狐 Hub 现状）：
- 小红书 ``xhs/search/search``：按关键词搜笔记，正文/标签/互动数据/相关搜索词俱全。
- 公众号 ``gzhData/searchArticle``：按关键词搜文章，标题/摘要/阅读赞藏评俱全。
- 微博 ``weibo/ability/accountSearch``：只能搜账号（无帖子内容搜索），
  返回官方账号名/粉丝/认证主体 —— 竞品分析里只作「品牌声量与认证」线索。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

from .state import AgentState

logger = logging.getLogger(__name__)

REDFOX_BASE = "https://redfox.hk"
REDFOX_SOURCE = "contentforge-competitor"
REQUEST_TIMEOUT = 15.0

# 渠道 id → 红狐端点路径。**单一真源**：竞品数据源的渠道支持范围只在这里声明。
CHANNEL_ENDPOINTS: dict[str, str] = {
    "xiaohongshu": "/story/api/xhs/search/search",
    "wechat": "/story/api/gzhData/searchArticle",
    "weibo": "/story/api/weibo/ability/accountSearch",
}


def _api_key() -> str:
    return os.environ.get("REDFOX_API_KEY", "").strip()


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST JSON 到红狐 Hub，两级网络回退：直连 → 系统代理。失败抛异常。"""
    url = REDFOX_BASE + path
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": _api_key(),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    # 先禁代理直连（本机 http_proxy 指向本地端口，服务可能没开；直连更稳），
    # 失败再回退到系统默认（走代理，兼容需要代理才能出海的网络）。
    for opener in (
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
        urllib.request.build_opener(),
    ):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
                text = resp.read().decode("utf-8")
            data = json.loads(text)
            if data.get("code") != 2000:
                raise RuntimeError(f"红狐接口返回 code={data.get('code')} msg={data.get('msg')}")
            return data
        except Exception as exc:  # noqa: BLE001 - 网络/协议错误都走下一级回退
            last_error = exc
    raise last_error  # type: ignore[misc]


def _num(value: Any) -> int:
    """宽松转整数（红狐返回的互动数可能是 int / str / '1.5w' 形态）。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace("+", "").replace(",", "").strip().lower()
    if not text:
        return 0
    if text.endswith("w"):
        try:
            return int(float(text[:-1]) * 10000)
        except ValueError:
            return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _note(title: str, summary: str, author: str, metrics: dict[str, int],
          link: str, published_at: str, channel: str, kind: str) -> dict[str, Any]:
    """统一竞品证据结构（三渠道归一）。"""
    out: dict[str, Any] = {
        "channel": channel,
        "kind": kind,
        "title": title,
        "summary": summary,
        "author": author,
        "metrics": metrics,
        "link": link,
    }
    if published_at:
        out["published_at"] = published_at
    return out


def _fetch_xiaohongshu(keyword: str, limit: int) -> list[dict[str, Any]]:
    data = _post_json(CHANNEL_ENDPOINTS["xiaohongshu"], {
        "keyword": keyword, "pageNum": 1, "pageSize": max(limit, 10),
        "startDate": "", "endDate": "", "source": REDFOX_SOURCE,
    })
    articles = (data.get("data") or {}).get("articles") or []
    evidence: list[dict[str, Any]] = []
    for item in articles[:limit]:
        note_id = item.get("id") or ""
        evidence.append(_note(
            title=str(item.get("title") or item.get("desc") or "")[:60],
            summary=str(item.get("desc") or "")[:200],
            author=str(item.get("authorNickname") or ""),
            metrics={
                "likes": _num(item.get("likedCount")),
                "collects": _num(item.get("collectedCount")),
                "comments": _num(item.get("commentsCount")),
                "shares": _num(item.get("sharedCount")),
                "interactions": _num(item.get("interactiveCount")),
                "fans": _num(item.get("authorFans")),
            },
            link=str(item.get("shareInfoLink") or f"https://www.xiaohongshu.com/explore/{note_id}"),
            published_at=str(item.get("createTime") or ""),
            channel="xiaohongshu", kind="note",
        ))
    return evidence


def _fetch_wechat(keyword: str, limit: int) -> list[dict[str, Any]]:
    data = _post_json(CHANNEL_ENDPOINTS["wechat"], {
        "keyword": keyword, "offset": 0, "sortType": "_0", "source": REDFOX_SOURCE,
    })
    articles = (data.get("data") or {}).get("list") or []
    evidence: list[dict[str, Any]] = []
    for item in articles[:limit]:
        evidence.append(_note(
            title=str(item.get("title") or "")[:60],
            summary=str(item.get("summary") or item.get("memo") or "")[:200],
            author=str(item.get("author") or ""),
            metrics={
                "reads": _num(item.get("readCount")),
                "likes": _num(item.get("likeCount")),
                "collects": _num(item.get("collectCount")),
                "comments": _num(item.get("commentCount")),
                "shares": _num(item.get("shareCount")),
            },
            link=str(item.get("workUrl") or ""),
            published_at=str(item.get("publishTime") or ""),
            channel="wechat", kind="article",
        ))
    return evidence


def _fetch_weibo(keyword: str, limit: int) -> list[dict[str, Any]]:
    data = _post_json(CHANNEL_ENDPOINTS["weibo"], {
        "keyword": keyword, "page": 1, "source": REDFOX_SOURCE,
    })
    accounts = data.get("data")
    if not isinstance(accounts, list):
        accounts = []
    evidence: list[dict[str, Any]] = []
    for item in accounts[:limit]:
        verified = bool(item.get("verified"))
        evidence.append(_note(
            title=str(item.get("name") or "")[:60],
            summary=str(item.get("customVerify") or "")[:200],
            author=str(item.get("name") or ""),
            metrics={"fans": _num(item.get("fansCount"))},
            link="",
            published_at="",
            channel="weibo", kind="account",
        ))
        if verified:
            evidence[-1]["verified"] = True
    return evidence


# 渠道 id → 抓取函数。三渠道按同一签名 (keyword, limit) -> list[evidence]。
_CHANNEL_FETCHERS = {
    "xiaohongshu": _fetch_xiaohongshu,
    "wechat": _fetch_wechat,
    "weibo": _fetch_weibo,
}

MAX_EVIDENCE_PER_CHANNEL = 8

# 常见营销动作词/后缀（作为「核心品类词」的截断点）。红狐搜索对完整营销长句
# 命中极差（「磁轴键盘新品上市推广」搜 0 条），但核心品类词（「磁轴键盘」）
# 能搜到大量真实笔记 —— 所以抓取前先把主题提炼成核心词。
_MARKETING_SUFFIXES = (
    "新品上市推广", "新品上市", "上市推广", "新品首发", "新品发布", "正式发布",
    "官方发布", "新品", "首发", "上市", "营销", "推广", "种草", "安利", "测评",
    "评测", "推荐", "攻略", "怎么选", "选购", "避坑", "好物", "分享", "活动",
    "促销", "策划", "方案",
)


def _extract_core_keyword(topic: str) -> str:
    """从营销主题提炼核心品类词（纯规则，不调模型）。

    在最早出现的营销动作词/后缀处截断，取前缀作为核心词：
    「磁轴键盘新品上市推广」→「磁轴键盘」；「磁轴键盘」→ 原样返回。
    截断后过短（<2 字，说明后缀打头或本就无实义）则回退原词。
    """
    text = (topic or "").strip()
    if not text:
        return ""
    cut = len(text)
    for suffix in _MARKETING_SUFFIXES:
        idx = text.find(suffix)
        if idx != -1 and idx < cut:
            cut = idx
    core = text[:cut].strip()
    return core if len(core) >= 2 else text


def fetch_competitor_evidence(channel_id: str, keyword: str,
                              limit: int = MAX_EVIDENCE_PER_CHANNEL) -> list[dict[str, Any]]:
    """按渠道抓取真实竞品证据。无 key / 未知渠道 / 失败均返回空列表。"""
    if not keyword or not keyword.strip():
        return []
    if not _api_key():
        logger.info("未配置 REDFOX_API_KEY，竞品分析退回模型内部知识")
        return []
    fetcher = _CHANNEL_FETCHERS.get(channel_id)
    if fetcher is None:
        return []
    try:
        evidence = fetcher(keyword.strip(), limit)
        logger.info("竞品数据源抓到 %d 条真实内容（渠道=%s，关键词=%s）",
                    len(evidence), channel_id, keyword)
        return evidence
    except Exception as exc:  # noqa: BLE001 - 数据源失败绝不影响主流水线
        logger.warning("竞品数据源抓取失败（渠道=%s，关键词=%s），降级为模型内部知识: %s",
                       channel_id, keyword, exc)
        return []


class CompetitorDataSource:
    """可注入的竞品数据源（与 ``web_search`` 同构，供 runner/组合根装配）。

    实例化时固定 api_key（默认读环境变量），``available`` 为 False 时
    ``fetch`` 直接返回空列表 —— 让「没配 key 时零抓取」在装配期即可判定。
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key if api_key is not None else _api_key()).strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def fetch(self, state: AgentState, limit: int = MAX_EVIDENCE_PER_CHANNEL) -> list[dict[str, Any]]:
        if not self.available:
            return []
        inp = state.get("input") or {}
        channel_id = str(inp.get("channel_id") or "")
        topic = str(inp.get("topic") or "")
        brand = str(inp.get("brand_name") or "")
        # 多级关键词回退：核心品类词 → 原主题 → 品牌，谁先抓到非空用谁。
        # 营销主题常是完整长句（红狐对长句命中差），核心词才搜得到真实竞品。
        candidates: list[str] = []
        for kw in (_extract_core_keyword(topic), topic, brand):
            kw = kw.strip()
            if kw and kw not in candidates:
                candidates.append(kw)
        for kw in candidates:
            evidence = fetch_competitor_evidence(channel_id, kw, limit=limit)
            if evidence:
                return evidence
        return []
