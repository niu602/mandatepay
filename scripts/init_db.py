"""按 DATABASE_URL 在目标后端建表（幂等，可重复执行）。

用法：
    # PostgreSQL
    set DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mandatepay
    .\\.venv\\Scripts\\python.exe scripts\\init_db.py

    # SQLite（默认）
    .\\.venv\\Scripts\\python.exe scripts\\init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import store  # noqa: E402


def main() -> int:
    backend = store.backend_name()
    print(f"目标后端: {backend}")
    if backend == "postgresql":
        dsn = store._effective_dsn()
        safe = dsn.split("@")[-1]  # 隐藏凭证，只打印 host/db
        print(f"连接目标: {safe}")
    else:
        # 打印**实际生效**的路径，不是硬编码的默认值 ——
        # DATABASE_URL 可能指向别处（如 sqlite:////tmp/x.db），打印默认值会误导人。
        print(f"数据库文件: {store._sqlite_path()}")

    # 触发一次连接即完成建表（_Conn.__enter__ 执行 schema）
    with store._Conn() as db:
        tables = db.cur().execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            if db.is_pg
            else "SELECT name AS table_name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = sorted(t["table_name"] for t in tables)
    print("已就绪的表:", ", ".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
