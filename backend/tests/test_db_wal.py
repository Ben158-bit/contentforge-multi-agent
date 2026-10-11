"""回归：WAL 旁路文件竞态 → `attempt to write a readonly database`。

**真凶不是权限**：数据库文件没有只读属性、目录可写、独立进程写入正常。
真凶是 WAL 模式下的这条规则——「**最后一个**连接关闭时，SQLite checkpoint
并删除 `-wal` / `-shm`」。本项目每条语句都开关一次连接，且多个流水线线程
（`asyncio.to_thread`）与 API 层的 aiosqlite 并发读写同一库，于是「A 线程关最后一个
连接、删掉旁路文件」与「B 线程新建连接、要重建旁路文件」撞在一起，
SQLite 直接返回 `SQLITE_READONLY`，表现为整任务失败（成本 0、阶段全停在「等待」）。

修复：`db_anchor` 持有进程级保活连接，旁路文件永不被删。复盘与对照实验数据见
`app/db_anchor.py`。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from collections import Counter

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="contentforge-wal-test-"))

import pytest  # noqa: E402

from app import db_anchor, db_sql  # noqa: E402
from app.config import get_settings  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """把同步 SQL 层指向一个临时库，不碰真实 data 目录。"""
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    yield tmp_path / "contentforge.db"
    db_anchor.release_all()


def _churn(db_path, *, statements: int, keep_anchor: bool) -> None:
    """每条语句开一次连接、写完即关——与 `db_sql.exec` 完全一致的模式。"""
    for _ in range(statements):
        c = sqlite3.connect(db_path, timeout=10.0)
        try:
            c.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
            c.commit()
        finally:
            c.close()


def test_anchor_keeps_the_wal_sidecar_files_alive(tmp_path):
    """**确定性**根因回归：有锚点时，连接开关不再回收 `-wal` / `-shm`。

    这是本 bug 的精确复现条件，与概率无关：
    - 无锚点 → 最后一条连接关闭即回收旁路文件（竞态窗口由此产生）；
    - 有锚点 → 永不存在「最后一个连接关闭」，窗口消失。
    """
    db = tmp_path / "a.db"
    sqlite3.connect(db).close()  # 建库

    # 1) 无锚点：一次「开→PRAGMA WAL→关」之后旁路文件被回收
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode=WAL").fetchone()
    c.commit()
    c.close()
    assert not (tmp_path / "a.db-wal").exists(), "无锚点时应回收 -wal（这就是竞态来源）"
    assert not (tmp_path / "a.db-shm").exists()

    # 2) 持锚点：反复开关连接，旁路文件始终存在
    db_anchor.hold(db)
    for _ in range(3):
        _churn(db, statements=1, keep_anchor=True)
        assert (tmp_path / "a.db-wal").exists(), "锚点存活时 -wal 不应被回收"
        assert (tmp_path / "a.db-shm").exists(), "锚点存活时 -shm 不应被回收"

    # 3) 释放锚点后回收行为恢复，证明起作用的是锚点本身
    db_anchor.release(db)
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode=WAL").fetchone()
    c.commit()
    c.close()
    assert not (tmp_path / "a.db-wal").exists(), "释放锚点后应恢复回收"


def test_sync_layer_keeps_sidecar_files_between_statements(temp_db):
    """同步 SQL 层（db_sql.exec）自己就要保证旁路文件不被回收。"""
    db_sql.exec("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY, v INTEGER)")
    assert (temp_db.parent / "contentforge.db-wal").exists()
    assert (temp_db.parent / "contentforge.db-shm").exists()


def test_concurrent_writers_no_longer_hit_readonly_database(temp_db):
    """并发冒烟：多线程高频开关连接写同一库，不应再出现 readonly database。

    修复前这段会稳定复现（8 线程 8 秒 4 次，见 `db_anchor.py` 复盘表）；
    单次时长缩短到 0.8s 后是**冒烟**性质——真正的确定性守卫是上面那条锚点测试。
    """
    db_sql.exec("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY, v INTEGER)")

    errors: Counter[str] = Counter()
    writes = Counter()
    stop = threading.Event()

    def worker(n: int) -> None:
        while not stop.is_set():
            try:
                db_sql.exec("INSERT OR REPLACE INTO probe (id, v) VALUES (?, ?)", (n, n))
                db_sql.exec("DELETE FROM probe WHERE id = ?", (n,))
                writes["ok"] += 1
            except Exception as exc:  # noqa: BLE001 - 故意收全部，逐一分类断言
                errors[f"{type(exc).__name__}: {exc}"] += 1

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(8)]
    for t in threads:
        t.start()
    stop.wait(0.8)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert writes["ok"] > 0, "压测没有产生任何成功写入，用例本身失效"
    assert not errors, f"并发写入出现异常（应为 0）: {dict(errors)}"


def test_hold_is_idempotent_and_release_is_scoped(tmp_path):
    db = tmp_path / "b.db"
    first = db_anchor.hold(db)
    assert db_anchor.hold(db) is first, "hold 必须幂等，不能每次新建连接"

    db_anchor.release(db)
    assert db_anchor.hold(db) is not first, "释放后应能重新建立锚点"
    db_anchor.release_all()
    # 幂等：重复释放 / 未持有就释放不应抛异常
    db_anchor.release_all()
    db_anchor.release(db)
