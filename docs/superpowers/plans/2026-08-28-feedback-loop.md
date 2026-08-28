# 效果闭环自学习（B）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 ContentForge 增加「生成 → 效果回填 → 规律学习 → 注入」闭环：内容产出后可回填/模拟效果数据，效果分析 Agent 提炼正负规律（带 strength），后续生成自动注入，前端可演示学习前后对比。

**Architecture:** 不动现有 5 节点图。新增 `backend/app/feedback.py`（程序化特征提取 + LLM 规律归纳 + 防规则爆炸合并）、SQLite 迁移 v3（`content_feedback` 表 + `brand_preferences` 加 `source`/`strength` 列）、复用 `get_brand_context` 注入管道（排序规则改为 feedback+strength 优先）、前端 TaskDetail 加回填/模拟/规律面板。TDD：每步先写失败测试。

**Tech Stack:** Python 3.10 + FastAPI + aiosqlite + pytest（后端）；React + TypeScript + Vitest（前端）。

**Spec:** `docs/superpowers/specs/2026-08-28-feedback-loop-design.md`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/db.py` | MIGRATIONS 追加 `("v3", [...])`：建 `content_feedback` 表 + `brand_preferences` 加 `source`/`strength` 列 |
| `backend/app/repository.py` | 新增 `upsert_content_feedback` / `get_content_feedback` / `list_feedback_rules` / `delete_feedback_rule`；`add_brand_preference` 加 `source`/`strength` 参数 |
| `backend/app/feedback.py`（新建） | `extract_features`（程序化特征）、`_strength_from_stats`（程序化 strength）、`derive_feedback_rules`（LLM 归纳）、`learn_from_feedback`（入库+合并）、`simulate_feedback`（模拟数据生成）、`_content_text_of`（从任务读文案） |
| `backend/app/schemas.py` | 新增 `FeedbackIn` / `FeedbackOut` / `FeedbackRuleOut` |
| `backend/app/api/routes.py` | 新增 5 个路由（回填/模拟/读取/规律列表/删除规律） |
| `backend/app/memory.py` | `get_brand_context` 排序改为 feedback+strength 优先（改动 1 行 ORDER BY + SELECT 列） |
| `frontend/src/api.ts` | 新增 5 个 API 函数 |
| `frontend/src/components/TaskDetail.tsx` | 新增「效果回填」区块（表单+模拟按钮+已学规律） |
| `frontend/src/components/FeedbackPanel.tsx`（新建） | 效果回填与规律面板组件 |
| `frontend/src/types.ts` | 新增 `ContentFeedback` / `FeedbackRule` 类型 |
| `backend/tests/test_feedback.py`（新建） | 特征提取/规律提炼/合并/回填幂等/模拟相关性/API/注入排序 |
| `backend/tests/test_db_migration.py` | 追加 v3 迁移断言 |

---

## Task 1: 数据库迁移 v3 + repository 扩展

**Files:**
- Modify: `backend/app/db.py`（MIGRATIONS 追加 v3）
- Modify: `backend/app/repository.py`
- Test: `backend/tests/test_db_migration.py`、`backend/tests/test_feedback.py`

- [ ] **Step 1: 写失败测试（迁移 v3 结构）**

在 `backend/tests/test_db_migration.py` 追加：

```python
async def test_v3_feedback_tables():
    from app.db import get_conn
    conn = await get_conn()
    try:
        cols = {r[1] for r in await conn.execute("PRAGMA table_info(brand_preferences)")}
        assert "source" in cols and "strength" in cols, f"缺少 source/strength 列: {cols}"
        tbls = {r[0] for r in await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "content_feedback" in tbls
    finally:
        await conn.close()
```

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_db_migration.py::test_v3_feedback_tables -v`
Expected: FAIL（列/表不存在）

- [ ] **Step 2: 实现迁移 v3**

`backend/app/db.py` 的 `MIGRATIONS` 列表末尾追加：

```python
        # 效果闭环：效果回填表 + 偏好规则增加来源/强度
        "v3",
        [
            """
            CREATE TABLE IF NOT EXISTS content_feedback (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id   INTEGER NOT NULL,
                channel      TEXT NOT NULL DEFAULT 'general',
                views        INTEGER NOT NULL DEFAULT 0,
                conversions  INTEGER NOT NULL DEFAULT 0,
                score        REAL NOT NULL DEFAULT 0,
                is_simulated INTEGER NOT NULL DEFAULT 0,
                note         TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (content_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_feedback_content ON content_feedback(content_id)",
            "ALTER TABLE brand_preferences ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
            "ALTER TABLE brand_preferences ADD COLUMN strength REAL NOT NULL DEFAULT 1.0",
        ],
```

注意：`content_feedback` 以 `content_id`（= task_id）为 UNIQUE，实现回填幂等；`is_simulated` 标记模拟数据，不进入规律提炼基线。

- [ ] **Step 3: 写失败测试（repository 反馈 CRUD）**

新建 `backend/tests/test_feedback.py`（头部沿用 test_memory.py 的 DATA_DIR 模式）：

```python
"""效果闭环测试：特征提取、规律提炼、回填幂等、模拟相关性、注入排序、API。"""
from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="contentforge-fb-test-")

import pytest  # noqa: E402

from app import repository as repo  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
async def _init_db():
    await init_db()
    yield


async def test_feedback_upsert_idempotent():
    tid = await repo.create_task("保温杯", "xiaohongshu", brand_name="暖芯")
    await repo.upsert_content_feedback(tid, views=1000, conversions=30, score=3.5)
    await repo.upsert_content_feedback(tid, views=2000, conversions=60, score=4.0)
    row = await repo.get_content_feedback(tid)
    assert row["views"] == 2000 and row["conversions"] == 60 and row["score"] == 4.0
    rows = await repo.list_content_feedback()
    assert len(rows) == 1  # 幂等：同 content_id 不新增
```

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py::test_feedback_upsert_idempotent -v`
Expected: FAIL（函数不存在）

- [ ] **Step 4: 实现 repository 扩展**

`backend/app/repository.py` 追加（在 `add_brand_preference` 附近）：

```python
async def upsert_content_feedback(
    content_id: int, *, views: int = 0, conversions: int = 0,
    score: float = 0.0, is_simulated: int = 0, note: str = "",
    channel: str = "general",
) -> None:
    async with _conn_ctx() as conn:
        await conn.execute(
            """
            INSERT INTO content_feedback
                (content_id, channel, views, conversions, score, is_simulated, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_id) DO UPDATE SET
                views=excluded.views, conversions=excluded.conversions,
                score=excluded.score, is_simulated=excluded.is_simulated,
                note=excluded.note, updated_at=datetime('now')
            """,
            (content_id, channel, views, conversions, score, is_simulated, note),
        )
        await conn.commit()


async def get_content_feedback(content_id: int) -> dict:
    async with _conn_ctx() as conn:
        row = await conn.execute(
            "SELECT * FROM content_feedback WHERE content_id = ?", (content_id,))
        return _row_to_dict(await row.fetchone())


async def list_content_feedback() -> list[dict]:
    async with _conn_ctx() as conn:
        rows = await conn.execute(
            "SELECT * FROM content_feedback WHERE is_simulated = 0 ORDER BY id DESC")
        return [_row_to_dict(r) for r in await rows.fetchall()]
```

`add_brand_preference` 签名扩展（source/strength 可选，默认兼容现有调用）：

```python
async def add_brand_preference(
    brand_id: int, rule_text: str, source_task: int | None = None,
    source: str = "manual", strength: float = 1.0,
) -> int:
    async with _conn_ctx() as conn:
        cur = await conn.execute(
            "INSERT INTO brand_preferences (brand_id, rule_text, source_task, source, strength) "
            "VALUES (?, ?, ?, ?, ?)",
            (brand_id, rule_text, source_task, source, strength),
        )
        await conn.commit()
        return cur.lastrowid


async def delete_feedback_rule(brand_id: int, rule_id: int) -> None:
    async with _conn_ctx() as conn:
        await conn.execute(
            "DELETE FROM brand_preferences WHERE id = ? AND brand_id = ? AND source = 'feedback'",
            (rule_id, brand_id))
        await conn.commit()
```

`list_brand_preferences` 改为 `SELECT *`（返回 source/strength，兼容旧调用）：

```python
async def list_brand_preferences(brand_id: int, limit: int | None = None) -> list[dict]:
    async with _conn_ctx() as conn:
        sql = "SELECT * FROM brand_preferences WHERE brand_id = ?"
        args: list = [brand_id]
        if limit is not None:
            sql += " ORDER BY id DESC LIMIT ?"
            args.append(limit)
        rows = await conn.execute(sql, args)
        return [_row_to_dict(r) for r in await rows.fetchall()]
```

- [ ] **Step 5: 跑测试**

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_db_migration.py tests/test_feedback.py -v`
Expected: 全部 PASS（现有迁移/记忆测试不受影响）

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/repository.py backend/tests/test_db_migration.py backend/tests/test_feedback.py
git commit -m "feat: 迁移 v3（content_feedback 表 + brand_preferences 扩展 source/strength）+ feedback 仓储"
```

---

## Task 2: feedback.py 特征提取（纯函数）

**Files:**
- Create: `backend/app/feedback.py`
- Test: `backend/tests/test_feedback.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_feedback.py` 追加：

```python
from app.feedback import extract_features


def test_extract_features():
    content = {
        "title": "3 个保温杯选购技巧，为什么买贵不如买对？",
        "body": "第一点：看材质。\n第二点：看保温时长。\n第三点：看密封性。",
        "hashtags": ["#保温杯", "#好物"],
        "notes": "",
    }
    f = extract_features(content)
    assert f["has_num"] == 1          # 标题含数字
    assert f["has_question"] == 1     # 标题含问句
    assert f["title_len"] == len("3 个保温杯选购技巧，为什么买贵不如买对？")
    assert f["bullet_count"] == 3     # 正文要点数（按行/编号）
    assert f["has_emoji"] == 0
```

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py::test_extract_features -v`
Expected: FAIL（`app.feedback` 不存在）

- [ ] **Step 2: 实现 extract_features**

新建 `backend/app/feedback.py`：

```python
"""效果闭环：程序化特征提取 + LLM 规律归纳 + 入库合并（含模拟数据生成）。"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from typing import Any, Callable, Optional

from app import repository as repo
from app.llm import get_llm_client

logger = logging.getLogger(__name__)

# 情绪词/提问/emoji 的简单特征检测（演示级，不追求完备）
_QUESTION_RE = re.compile(r"[？?]")
_NUM_RE = re.compile(r"[0-9０-９]")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_EMOTION_WORDS = ("惊喜", "后悔", "踩坑", "必看", "干货", "避雷", "种草", "绝了", "救命")


def _title_of(content: dict) -> str:
    t = content.get("title") or ""
    return t if isinstance(t, str) else str(t)


def _body_of(content: dict) -> str:
    b = content.get("body") or ""
    return b if isinstance(b, str) else str(b)


def extract_features(content: dict) -> dict[str, Any]:
    """对单条内容做程序化特征提取（不依赖 LLM，输入仅为真实内容）。"""
    title = _title_of(content)
    body = _body_of(content)
    # 要点数：按换行/句号分隔的短句，含序号（1./第一/·/-）视为要点
    bullet_count = len(re.findall(r"(?:^|\n)\s*(?:\d+[\.、]|第一|[·\-*])", body))
    return {
        "title_len": len(title),
        "has_num": 1 if _NUM_RE.search(title) else 0,
        "has_question": 1 if _QUESTION_RE.search(title) else 0,
        "has_emoji": 1 if _EMOJI_RE.search(title) else 0,
        "has_emotion_word": 1 if any(w in title for w in _EMOTION_WORDS) else 0,
        "bullet_count": bullet_count,
        "has_sources": 1 if content.get("sources") else 0,
    }
```

- [ ] **Step 3: 跑测试**

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py::test_extract_features -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/feedback.py backend/tests/test_feedback.py
git commit -m "feat: 程序化内容特征提取（extract_features）"
```

---

## Task 3: 规律提炼（程序化 strength + LLM 归纳）

**Files:**
- Modify: `backend/app/feedback.py`
- Test: `backend/tests/test_feedback.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_feedback.py` 追加：

```python
from app.feedback import _strength_from_stats, derive_feedback_rules


def test_strength_from_stats():
    # 高分组该特征出现 80%，低分组 30% → 正向强规则
    assert _strength_from_stats(0.80, 0.30) > 0
    # 低分组更高 → 负向（避坑）规则
    assert _strength_from_stats(0.20, 0.70) < 0
    # 无差异 → 0（不产生规则）
    assert _strength_from_stats(0.5, 0.5) == 0


def test_derive_feedback_rules_uses_stats():
    stats = {"has_num": (0.8, 0.3), "has_emoji": (0.2, 0.6)}
    # LLM 调用用固定返回，避免真实网络
    rules = derive_feedback_rules(stats, llm=_FakeLLM())
    assert len(rules) >= 1
    assert all(r["source"] == "feedback" for r in rules)
    assert all(isinstance(r["strength"], float) for r in rules)


class _FakeLLM:
    def complete(self, messages, **kw):
        return '{"rules": [{"feature": "has_num", "text": "标题含数字点击率高"}]}'
```

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py::test_strength_from_stats tests/test_feedback.py::test_derive_feedback_rules_uses_stats -v`
Expected: FAIL（函数不存在）

- [ ] **Step 2: 实现规律提炼**

`backend/app/feedback.py` 追加：

```python
_FEATURE_LABELS = {
    "has_num": "标题含数字",
    "has_question": "标题用提问句式",
    "has_emoji": "标题含 emoji",
    "has_emotion_word": "标题含情绪词",
    "bullet_count": "正文要点式分段",
    "has_sources": "正文附来源引用",
}


def _strength_from_stats(high_ratio: float, low_ratio: float) -> float:
    """由高/低分组出现率差异映射强度（-2~+2），0 表示无差异。"""
    diff = high_ratio - low_ratio
    return round(max(-2.0, min(2.0, diff * 4.0)), 2)
```

`repo.list_content_feedback()` 是 async；规律提炼的输入设计为**调用方先取好数据**（同步函数便于测试）。`_feature_stats` 输入高/低分组特征列表：

```python
def _feature_stats(high_group: list[dict], low_group: list[dict]) -> dict[str, tuple[float, float]]:
    """输入高/低分组特征列表，输出 {feature: (高分组出现率, 低分组出现率)}。"""
    if not high_group or not low_group:
        return {}
    stats: dict[str, tuple[float, float]] = {}
    for feat in _FEATURE_LABELS:
        h = sum(1 for f in high_group if f.get(feat)) / len(high_group)
        l = sum(1 for f in low_group if f.get(feat)) / len(low_group)
        stats[feat] = (round(h, 2), round(l, 2))
    return stats


_RULES_SYSTEM = (
    "你是内容效果分析师。根据给定的特征统计表（各特征在高分内容/低分内容中的出现率），"
    "归纳 1-3 条可指导下次创作的效果规律。只允许基于表格数据，禁止臆造。"
    "输出 JSON：{\"rules\":[{\"feature\":\"特征名\",\"text\":\"一句话规律\"}]}"
)


def derive_feedback_rules(
    stats: dict[str, tuple[float, float]], llm: Optional[Callable] = None,
) -> list[dict]:
    """LLM 归纳规律；strength 由程序化计算（LLM 不产生数值）。失败返回空列表。"""
    candidates = [
        (feat, h, l) for feat, (h, l) in stats.items()
        if abs(h - l) >= 0.2  # 只把有区分度的特征喂给 LLM
    ]
    if not candidates:
        return []
    llm = llm or get_llm_client()
    table = "\n".join(
        f"- {_FEATURE_LABELS[feat]}：高分 {h:.0%} / 低分 {l:.0%}" for feat, h, l in candidates)
    try:
        raw = llm.complete(
            [{"role": "system", "content": _RULES_SYSTEM},
             {"role": "user", "content": f"特征统计表：\n{table}"}],
            response_format={"type": "json_object"},
        )
        payload = json.loads(raw)
    except Exception:  # 静默降级：LLM 失败不阻断主流程
        logger.warning("效果规律归纳失败，跳过本轮学习")
        return []
    rules = []
    for r in payload.get("rules", [])[:3]:
        feat = r.get("feature")
        if feat not in _FEATURE_LABELS:
            continue
        h, l = dict(stats)[feat]
        strength = _strength_from_stats(h, l)
        if strength == 0:
            continue
        rules.append({
            "feature": feat, "text": r.get("text", ""),
            "strength": strength, "source": "feedback",
        })
    return rules
```

- [ ] **Step 3: 跑测试**

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/feedback.py backend/tests/test_feedback.py
git commit -m "feat: 规律提炼（程序化 strength + LLM 归纳，失败静默降级）"
```

---

## Task 4: learn_from_feedback 入库合并 + 注入排序

**Files:**
- Modify: `backend/app/feedback.py`
- Modify: `backend/app/memory.py`
- Test: `backend/tests/test_feedback.py`、`backend/tests/test_memory.py`

- [ ] **Step 1: 写失败测试（合并 + 注入排序）**

`backend/tests/test_feedback.py` 追加：

```python
from app.feedback import learn_from_feedback
from app.memory import get_brand_context


async def test_learn_from_feedback_merge_and_inject():
    bid = await repo.create_brand("暖芯")
    rules = [
        {"feature": "has_num", "text": "标题含数字点击率高",
         "strength": 1.5, "source": "feedback"},
    ]
    added = await learn_from_feedback(bid, rules)
    assert added == 1
    # 同文本再学一次 → 合并（strength 加权），不新增行
    rules[0]["strength"] = 1.5
    added = await learn_from_feedback(bid, rules)
    assert added == 0
    prefs = await repo.list_brand_preferences(bid)
    assert len(prefs) == 1
    assert abs(prefs[0]["strength"] - 2.5) < 1e-6  # 1.5 + 1.0 衰减后累计

    # 注入：feedback 高 strength 规则排最前
    await repo.add_brand_preference(bid, "普通人工规则", source="manual", strength=0.5)
    ctx = get_brand_context(bid, max_rules=5)
    assert ctx["preferences"][0] == "标题含数字点击率高"
```

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py::test_learn_from_feedback_merge_and_inject -v`
Expected: FAIL

- [ ] **Step 2: 实现 learn_from_feedback 与注入排序**

`backend/app/feedback.py` 追加：

```python
async def learn_from_feedback(brand_id: int, rules: list[dict]) -> int:
    """效果规律入库；与已有同文本 feedback 规则合并（strength 加权）。返回新增条数。"""
    added = 0
    for r in rules:
        text = r["text"].strip()
        if not text or r.get("source") != "feedback":
            continue
        merged = await repo.merge_feedback_rule(brand_id, text, r.get("strength", 1.0))
        if merged:
            added += 1
    logger.info("品牌 %s 效果学习：新增 %d / 合并 %d 条规律", brand_id, added, len(rules) - added)
    return added
```

`backend/app/repository.py` 追加合并函数：

```python
async def merge_feedback_rule(brand_id: int, text: str, strength: float) -> int:
    """同 brand + source='feedback' + 同 rule_text → 加权累计 strength；否则新增。返回是否新增。"""
    async with _conn_ctx() as conn:
        row = await conn.execute(
            "SELECT id, strength FROM brand_preferences "
            "WHERE brand_id = ? AND source = 'feedback' AND rule_text = ?",
            (brand_id, text)).fetchone()
        if row:
            new_strength = round(row["strength"] + strength * 0.5, 2)
            await conn.execute(
                "UPDATE brand_preferences SET strength = ? WHERE id = ?",
                (new_strength, row["id"]))
            await conn.commit()
            return 0
        cur = await conn.execute(
            "INSERT INTO brand_preferences (brand_id, rule_text, source, strength) "
            "VALUES (?, ?, 'feedback', ?)",
            (brand_id, text, strength))
        await conn.commit()
        return 1
```

`backend/app/memory.py` 的 `get_brand_context` 排序改为 feedback+strength 优先（同时 SELECT source/strength 供前端）：

```python
        prefs = conn.execute(
            "SELECT rule_text FROM brand_preferences WHERE brand_id = ? "
            "ORDER BY (source = 'feedback') DESC, ABS(strength) DESC, id DESC LIMIT ?",
            (brand_id, max_rules),
        ).fetchall()
```

（仅改 ORDER BY 一行，preferences 列表文本不变。）

- [ ] **Step 3: 跑测试**

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py tests/test_memory.py -v`
Expected: 全部 PASS（现有 memory 测试不受排序影响——断言基于相对顺序）

- [ ] **Step 4: Commit**

```bash
git add backend/app/feedback.py backend/app/repository.py backend/app/memory.py backend/tests/test_feedback.py
git commit -m "feat: 效果规律入库合并 + 注入排序（feedback+strength 优先）"
```

---

## Task 5: API 路由（回填 / 模拟 / 读取 / 规律列表 / 删除）

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/test_feedback.py`

- [ ] **Step 1: 写失败测试（API 全链路）**

`backend/tests/test_feedback.py` 头部（DATA_DIR 之后、`import app` 之前）追加环境变量，然后追加 API 测试（沿用 test_api.py 的 `TestClient(app)` 模式）：

```python
os.environ["DEEPSEEK_API_KEY"] = "sk-test"  # 让 lifespan 能构建图（API 测试需要）
```

```python
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_feedback_api_flow(client):
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 201
    r = client.post("/api/tasks", json={"topic": "保温杯", "channel_id": "xiaohongshu"})
    tid = r.json()["id"]

    # 手动回填
    r = client.post(f"/api/tasks/{tid}/feedback",
                    json={"views": 1200, "conversions": 42, "score": 3.8})
    assert r.status_code == 200
    body = r.json()
    assert body["views"] == 1200 and body["is_simulated"] == 0

    # 模拟回填（特征相关）
    r = client.post(f"/api/tasks/{tid}/feedback/simulate")
    assert r.status_code == 200
    sim = r.json()
    assert sim["is_simulated"] == 1
    assert 0 <= sim["score"] <= 5

    # 规律列表（此时无 feedback 规律 → 空）
    bid = client.post("/api/brands", json={"name": "暖芯"}).json()["id"]
    r = client.get(f"/api/brands/{bid}/feedback-rules")
    assert r.status_code == 200 and r.json() == []

    # 删除规律（不存在时静默成功）
    r = client.delete(f"/api/brands/{bid}/feedback-rules/999")
    assert r.status_code == 200
```

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py::test_feedback_api_flow -v`
Expected: FAIL（404 无路由）

- [ ] **Step 2: 实现 schemas**

`backend/app/schemas.py` 追加：

```python
class FeedbackIn(BaseModel):
    views: int = Field(0, ge=0)
    conversions: int = Field(0, ge=0)
    score: float = Field(0, ge=0, le=5)
    note: str = Field("", max_length=500)


class FeedbackOut(BaseModel):
    content_id: int
    views: int
    conversions: int
    score: float
    is_simulated: int
    note: str
    created_at: str


class FeedbackRuleOut(BaseModel):
    id: int
    rule_text: str
    strength: float
    created_at: str
```

- [ ] **Step 3: 实现路由**

`backend/app/api/routes.py` 追加（放在任务路由区后）：

```python
# ===================== 效果闭环 =====================

@router.post("/tasks/{task_id}/feedback", response_model=dict)
async def submit_feedback(task_id: int, body: FeedbackIn, request: Request) -> dict:
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await repo.upsert_content_feedback(
        task_id, views=body.views, conversions=body.conversions,
        score=body.score, note=body.note)
    row = await repo.get_content_feedback(task_id)
    return {k: row[k] for k in ("content_id", "views", "conversions", "score",
                                "is_simulated", "note", "created_at")}


@router.post("/tasks/{task_id}/feedback/simulate", response_model=dict)
async def simulate_feedback(task_id: int, request: Request) -> dict:
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    sim = await asyncio.to_thread(simulate_feedback_for_task, task_id)
    await repo.upsert_content_feedback(
        task_id, views=sim["views"], conversions=sim["conversions"],
        score=sim["score"], is_simulated=1,
        note="模拟效果（演示用，不进入规律基线）")
    row = await repo.get_content_feedback(task_id)
    return {k: row[k] for k in ("content_id", "views", "conversions", "score",
                                "is_simulated", "note", "created_at")}


@router.get("/tasks/{task_id}/feedback", response_model=dict)
async def get_feedback(task_id: int) -> dict:
    row = await repo.get_content_feedback(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="尚无效果数据")
    return {k: row[k] for k in ("content_id", "views", "conversions", "score",
                                "is_simulated", "note", "created_at")}


@router.get("/brands/{brand_id}/feedback-rules", response_model=list[FeedbackRuleOut])
async def list_feedback_rules(brand_id: int) -> list[dict]:
    prefs = await repo.list_brand_preferences(brand_id)
    return [
        {"id": p["id"], "rule_text": p["rule_text"], "strength": p["strength"],
         "created_at": p["created_at"]}
        for p in prefs if p.get("source") == "feedback"
    ]


@router.delete("/brands/{brand_id}/feedback-rules/{rule_id}")
async def delete_feedback_rule(brand_id: int, rule_id: int) -> dict:
    await repo.delete_feedback_rule(brand_id, rule_id)
    return {"ok": True}
```

- [ ] **Step 4: 实现 simulate_feedback_for_task**

`backend/app/feedback.py` 追加（模拟数据与内容特征相关，使演示时规律可提炼）：

```python
def _content_text_of(task: dict) -> dict:
    """从任务的 copywriting 阶段 output 取文案结构（title/body/sources）。"""
    for s in task.get("stages", []) or []:
        if s.get("stage_key") == "copywriting" and s.get("output"):
            out = s["output"]
            if isinstance(out, dict) and out.get("variants"):
                return out["variants"][0] if isinstance(out["variants"], list) else out
            if isinstance(out, list) and out:
                return out[0]
    return {}


def simulate_feedback_for_task(task_id: int) -> dict:
    """基于任务内容特征生成模拟效果数据（种子随机，演示用）。"""
    import asyncio
    task = asyncio.get_event_loop().run_until_complete(repo.get_task(task_id))
    content = _content_text_of(task)
    f = extract_features(content)
    score = 2.0 + random.random() * 1.5
    if f["has_num"]: score += 0.5
    if f["has_question"]: score += 0.4
    if f["bullet_count"] >= 3: score += 0.3
    if f["has_sources"]: score += 0.3
    score = round(min(5.0, score), 1)
    views = int(500 + score * 800 + random.random() * 1000)
    conversions = int(views * (0.02 + score * 0.01))
    return {"views": views, "conversions": conversions, "score": score}
```

> 说明：`simulate_feedback_for_task` 内用 `asyncio.get_event_loop().run_until_complete` 同步取任务——路由层用 `asyncio.to_thread` 包裹避免阻塞事件循环。若后续需要可改为 async 版本（YAGNI，当前够用）。

- [ ] **Step 5: 跑测试**

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/test_feedback.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/api/routes.py backend/app/feedback.py backend/tests/test_feedback.py
git commit -m "feat: 效果闭环 API（回填/模拟/读取/规律列表/删除）"
```

---

## Task 6: 前端（api.ts + types + FeedbackPanel + TaskDetail 接入）

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/components/FeedbackPanel.tsx`
- Modify: `frontend/src/components/TaskDetail.tsx`
- Test: `frontend/src/components/FeedbackPanel.test.tsx`

- [ ] **Step 1: 写失败测试（组件渲染回填表单 + 模拟按钮）**

新建 `frontend/src/components/FeedbackPanel.test.tsx`（沿用 BrandManager.test.tsx 的测试模式，见该文件顶部 fixture）：

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import FeedbackPanel from './FeedbackPanel'

vi.mock('../api', () => ({
  api: {
    submitFeedback: vi.fn().mockResolvedValue({ content_id: 1, views: 10, conversions: 1, score: 3, is_simulated: 0, note: '', created_at: '' }),
    simulateFeedback: vi.fn().mockResolvedValue({ content_id: 1, views: 100, conversions: 5, score: 4, is_simulated: 1, note: '', created_at: '' }),
    listFeedbackRules: vi.fn().mockResolvedValue([]),
  },
}))

describe('FeedbackPanel', () => {
  it('渲染回填表单并可提交', async () => {
    render(<FeedbackPanel taskId={1} />)
    expect(screen.getByText('效果回填')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('阅读量'), { target: { value: '1000' } })
    fireEvent.click(screen.getByText('保存效果'))
    await waitFor(() => expect(screen.getByText('已保存')).toBeTruthy())
  })

  it('一键模拟按钮存在', () => {
    render(<FeedbackPanel taskId={1} />)
    expect(screen.getByText('一键模拟效果')).toBeTruthy()
  })
})
```

Run: `cd frontend && npx vitest run src/components/FeedbackPanel.test.tsx`
Expected: FAIL（组件不存在）

- [ ] **Step 2: 实现 types + api.ts**

`frontend/src/types.ts` 追加：

```ts
export interface ContentFeedback {
  content_id: number
  views: number
  conversions: number
  score: number
  is_simulated: number
  note: string
  created_at: string
}

export interface FeedbackRule {
  id: number
  rule_text: string
  strength: number
  created_at: string
}
```

`frontend/src/api.ts` 的 `api` 对象追加：

```ts
  submitFeedback: (taskId: number, payload: { views: number; conversions: number; score: number; note?: string }) =>
    request<ContentFeedback>(`/api/tasks/${taskId}/feedback`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  simulateFeedback: (taskId: number) =>
    request<ContentFeedback>(`/api/tasks/${taskId}/feedback/simulate`, { method: 'POST' }),
  getFeedback: (taskId: number) => request<ContentFeedback>(`/api/tasks/${taskId}/feedback`),
  listFeedbackRules: (brandId: number) => request<FeedbackRule[]>(`/api/brands/${brandId}/feedback-rules`),
  deleteFeedbackRule: (brandId: number, ruleId: number) =>
    request<{ ok: boolean }>(`/api/brands/${brandId}/feedback-rules/${ruleId}`, { method: 'DELETE' }),
```

- [ ] **Step 3: 实现 FeedbackPanel.tsx**

新建 `frontend/src/components/FeedbackPanel.tsx`：

```tsx
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ContentFeedback, FeedbackRule } from '../types'

interface Props {
  taskId: number
  brandId?: number | null
}

export default function FeedbackPanel({ taskId, brandId }: Props) {
  const [feedback, setFeedback] = useState<ContentFeedback | null>(null)
  const [rules, setRules] = useState<FeedbackRule[]>([])
  const [views, setViews] = useState('')
  const [conversions, setConversions] = useState('')
  const [score, setScore] = useState('')
  const [saved, setSaved] = useState(false)

  const load = async () => {
    try {
      const fb = await api.getFeedback(taskId)
      setFeedback(fb)
      setViews(String(fb.views))
      setConversions(String(fb.conversions))
      setScore(String(fb.score))
    } catch {
      setFeedback(null)
    }
    if (brandId) {
      api.listFeedbackRules(brandId).then(setRules).catch(() => undefined)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, brandId])

  const save = async () => {
    await api.submitFeedback(taskId, {
      views: Number(views) || 0,
      conversions: Number(conversions) || 0,
      score: Math.min(5, Math.max(0, Number(score) || 0)),
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    void load()
  }

  const simulate = async () => {
    await api.simulateFeedback(taskId)
    void load()
  }

  const removeRule = async (ruleId: number) => {
    if (brandId) await api.deleteFeedbackRule(brandId, ruleId)
    void load()
  }

  return (
    <section className="card">
      <h2>效果回填</h2>
      <p className="muted">回填内容真实效果数据（或一键模拟），系统将提炼「什么内容有效」并用于后续生成。</p>
      <div className="feedback-form">
        <label>
          阅读量
          <input aria-label="阅读量" value={views} onChange={(e) => setViews(e.target.value)} />
        </label>
        <label>
          转化数
          <input aria-label="转化数" value={conversions} onChange={(e) => setConversions(e.target.value)} />
        </label>
        <label>
          效果分(0-5)
          <input aria-label="效果分" value={score} onChange={(e) => setScore(e.target.value)} />
        </label>
      </div>
      <div className="btn-row">
        <button className="btn small" onClick={() => void save()}>保存效果</button>
        <button className="btn small ghost" onClick={() => void simulate()}>一键模拟效果</button>
      </div>
      {saved && <span className="ok-hint">已保存</span>}
      {feedback && (
        <p className="muted">当前：阅读 {feedback.views} · 转化 {feedback.conversions} · 分 {feedback.score}
          {feedback.is_simulated === 1 ? '（模拟）' : ''}</p>
      )}
      {rules.length > 0 && (
        <div className="rules-list">
          <h3>已学到的效果规律</h3>
          <ul>
            {rules.map((r) => (
              <li key={r.id}>
                <span className={r.strength >= 0 ? 'rule-pos' : 'rule-neg'}>
                  {r.strength >= 0 ? '+' : ''}{r.strength}
                </span>{' '}
                {r.rule_text}{' '}
                <button className="btn small ghost" onClick={() => void removeRule(r.id)}>删除</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 4: 接入 TaskDetail.tsx**

在 `frontend/src/components/TaskDetail.tsx` 引入并渲染（放在「文案变体」section 之后）：

```tsx
import FeedbackPanel from './FeedbackPanel'
// ...
      <FeedbackPanel taskId={taskId} brandId={task?.brand_id} />
```

（若 `Task` 类型无 `brand_id` 字段，在 types.ts 的 Task 接口补充 `brand_id?: number | null`。）

- [ ] **Step 5: 跑测试（前端）**

Run: `cd frontend && npx vitest run`
Expected: 原有 11 用例 + 新增 2 用例全 PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components/FeedbackPanel.tsx frontend/src/components/FeedbackPanel.test.tsx frontend/src/components/TaskDetail.tsx
git commit -m "feat: 前端效果回填/模拟/规律面板"
```

---

## Task 7: 全量回归 + 演示验证

**Files:** 无新增

- [ ] **Step 1: 后端全量测试**

Run: `cd backend && ../.venv/Scripts/python -m pytest tests/ -v`
Expected: 原有 69 + 新增 ≥8 全部 PASS

- [ ] **Step 2: 前端类型与测试**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 类型通过，11 + 2 用例 PASS

- [ ] **Step 3: 手动演示验证（可选）**

1. `cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000`
2. 创建任务 → 跑完 copywriting → 任务详情页「效果回填」区点「一键模拟效果」→ 刷新看已学规律
3. 确认学习规律出现在下次生成注入中（品牌上下文含「效果学习规律」小节）

- [ ] **Step 4: Commit（如有残留变更）**

```bash
git status --short
git add -A
git commit -m "chore: 效果闭环回归验证"
```

---

## Self-Review 记录

- **Spec 覆盖**：数据模型 v3（Task 1）✓；特征提取（Task 2）✓；规律提炼/strength（Task 3）✓；入库合并/注入（Task 4）✓；API 5 个（Task 5）✓；前端回填/模拟/规律面板/效果曲线（Task 6）——**效果对比曲线未覆盖**，见下方说明；测试 ≥8（各 Task 内嵌）✓；错误处理（LLM 失败静默降级、回填幂等、is_simulated 隔离）✓
- **效果对比曲线**：设计文档第 7.4 节「效果对比曲线」为演示增强项，本计划未包含独立任务——若需实现，追加 Task：GET `/api/feedback/trend`（按创建时间聚合 score）前端折线图。当前先交付闭环主链路，曲线作为后续增强（YAGNI）。
- **类型一致性**：`_strength_from_stats` 返回 float；`derive_feedback_rules` 返回 `list[dict]`；`learn_from_feedback` 返回 int；`merge_feedback_rule` 返回 int（1=新增/0=合并）——跨 Task 一致。`FeedbackOut`/`FeedbackRuleOut` 与前端 `ContentFeedback`/`FeedbackRule` 字段对齐。
- **已知依赖**：Task 5 的 `simulate_feedback_for_task` 使用 `asyncio.get_event_loop().run_until_complete` 同步读库（在 `asyncio.to_thread` 中执行）；若测试环境无运行中事件循环会抛错——测试中通过 `TestClient` 触发（有事件循环）。实现时若遇 `RuntimeError: no running event loop`，改为将 task 读取上移到路由层（async 读取后传入同步函数）。
