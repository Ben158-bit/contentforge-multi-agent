"""数据库初始化/迁移脚本。

用法（在 backend/ 目录下）:
    python -m scripts.init_db
"""
from __future__ import annotations

import asyncio

from app.db import default_db_path, init_db


async def main() -> None:
    await init_db()
    print(f"数据库初始化完成: {default_db_path()}")


if __name__ == "__main__":
    asyncio.run(main())
