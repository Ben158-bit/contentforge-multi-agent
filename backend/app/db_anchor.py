"""SQLite WAL 保活锚点——修掉「attempt to write a readonly database」的真凶。

## 故障现象

流水线跑到一半整任务失败，前端只显示「失败」，成本 0、耗时 0、所有阶段停在「等待」。
后端日志：

```
sqlite3.OperationalError: attempt to write a readonly database
  runner.py  _write_chunk → _upsert_stage
  db_sql.py  exec → c.execute(sql, params)
```

数据库文件不是只读（无只读属性、目录可写、独立进程写入正常），所以问题不在权限。

## 根因（已用对照实验坐实）

本项目由**多个线程/连接**读写同一个 WAL 库：API 层是 aiosqlite（事件循环线程），
流水线是 `asyncio.to_thread` 里的多个工作线程，走同步 `sqlite3`。
而 `db_sql.exec()` 是**每条语句开一次连接、执行完就关**。

SQLite 在 WAL 模式下的行为是：**当最后一个连接关闭时，它会 checkpoint 并删除
`-wal` / `-shm` 两个旁路文件**。此刻若另一个线程正在新建连接，它重建共享内存文件
会失败，SQLite 直接返回 `SQLITE_READONLY`（`attempt to write a readonly database`）。
任务并发度越高（`max_concurrent_tasks=3` + 前端高频轮询），撞上的概率越大。

对照实验（8 线程各自「开连接→写→关连接」跑 8 秒，均在同一份库上）：

| 条件 | `readonly database` 次数 | 结束后目录 |
|---|---|---|
| 无锚点 | **4** | 只剩 `contentforge.db`（旁路文件被删） |
| 持有一个常开锚点 | **0** | `-wal` / `-shm` 全程存在 |

## 对策

进程存活期间持有一个**永不关闭**的 keep-alive 连接。只要它活着，就永远不存在
「最后一个连接关闭」，旁路文件不会被删除，竞态窗口从根上消失——对 aiosqlite 与
同步 sqlite3 两条路径同时生效。

锚点连接**不持有任何事务**（只用一条只读 PRAGMA 触发旁路文件创建，
Python 的 sqlite3 只在 DML 前才隐式 BEGIN），因此不阻塞其它连接读写或 checkpoint。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 锚点创建/释放是低频操作，用可重入锁即可；读路径只做一次字典查找。
_lock = threading.RLock()
_anchors: dict[str, sqlite3.Connection] = {}


def hold(db_path: str | Path) -> sqlite3.Connection:
    """确保该库有一个存活的保活连接（幂等、线程安全）。"""
    path = Path(db_path)
    key = str(path.resolve())

    with _lock:
        existing = _anchors.get(key)
        if existing is not None:
            return existing

        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, timeout=10.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA busy_timeout=10000")
            # ⚠️ 这一步不能省：只跑 PRAGMA 的锚点是「空转」的。
            # PRAGMA 不会让 SQLite 打开 `-shm`，于是锚点既不持有旁路文件、
            # 也拦不住别的连接关闭时把它们删掉 —— 保护形同虚设。
            # 必须真正读一次库（sqlite_master 恒存在），锚点才会 open 并持有 `-shm`。
            # 实测：空库上只跑 PRAGMA，一次「开→写→关」后旁路文件被回收；
            # 加上这条 SELECT 后，旁路文件在后续开关连接中始终存活。
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.Error:
            # 建不起旁路文件（例如目录只读）时不要扩大故障：连接本身仍可用，
            # 只记明确告警——此时「关闭即删旁路文件」的竞态会回来。
            logger.warning(
                "WAL 保活锚点预热失败（%s）：旁路文件竞态防护未生效", key, exc_info=True
            )
        _anchors[key] = conn
        logger.debug("WAL 保活锚点已建立: %s", key)
        return conn


def release(db_path: str | Path) -> None:
    """释放指定库的锚点（测试与关停用）。"""
    key = str(Path(db_path).resolve())
    with _lock:
        conn = _anchors.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            logger.exception("关闭 WAL 保活锚点失败: %s", key)


def release_all() -> None:
    """释放所有锚点（应用关停用）。"""
    with _lock:
        keys = list(_anchors)
    for key in keys:
        release(key)
