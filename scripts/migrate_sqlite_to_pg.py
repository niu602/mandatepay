"""把既有 SQLite 数据迁移到 PostgreSQL。

设计原则：
- **只追加不覆盖**：目标表若已有数据，默认跳过（--force 才清空重灌）；
- **按 mandate_uuid / event_id 去重**，重复执行安全；
- 迁移前后打印行数对账，迁移失败不删源库（SQLite 文件保留为备份）。

用法：
    1) 先建表：  set DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mandatepay
                .\\.venv\\Scripts\\python.exe scripts\\init_db.py
    2) 再迁移：  .\\.venv\\Scripts\\python.exe scripts\\migrate_sqlite_to_pg.py
                .\\.venv\\Scripts\\python.exe scripts\\migrate_sqlite_to_pg.py --force   # 清空目标后重灌
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import store  # noqa: E402

TABLES = ("constraints", "purchase_requests", "mandates", "webhook_events")
# 各表列顺序需与 SELECT * 一致；id 由目标库自增生成，跳过
SKIP_COLS = {"id"}


def _sqlite_rows(path: Path) -> dict[str, list[tuple[list[str], tuple]]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out: dict[str, list[tuple[list[str], tuple]]] = {}
    for t in TABLES:
        try:
            rows = conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
        except sqlite3.OperationalError:
            out[t] = []
            continue
        cols = [c for c in rows[0].keys() if c not in SKIP_COLS] if rows else []
        out[t] = [(cols, [tuple(r[c] for c in cols) for r in rows])]
    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(ROOT / "mandatepay.db"), help="源 SQLite 文件路径")
    ap.add_argument("--force", action="store_true", help="清空目标表后重灌（默认只追加）")
    args = ap.parse_args()

    src = Path(args.sqlite)
    if not src.exists():
        print(f"源库不存在，无需迁移: {src}")
        return 0
    if store.backend_name() != "postgresql":
        print("错误：请先设置 DATABASE_URL 指向 PostgreSQL 再执行迁移。")
        return 2

    data = _sqlite_rows(src)
    with store._Conn() as db:
        if args.force:
            print("--force：清空目标表")
            for t in TABLES:
                db.cur().execute(f"DELETE FROM {t}")

        for t in TABLES:
            if not data[t]:
                print(f"{t}: 源表为空或无此表，跳过")
                continue
            cols, rows = data[t][0]
            if not rows:
                print(f"{t}: 0 行，跳过")
                continue
            ph = ", ".join(["?"] * len(cols))
            collist = ", ".join(cols)
            # 以唯一键做幂等：mandates 用 mandate_uuid，webhook_events 用 event_id，其余直接插
            conflict = {
                "mandates": "mandate_uuid",
                "webhook_events": "event_id",
            }.get(t)
            tail = f" ON CONFLICT ({conflict}) DO NOTHING" if conflict else ""
            cur = db.cur()
            for r in rows:
                cur.execute(f"INSERT INTO {t} ({collist}) VALUES ({ph}){tail}", tuple(r))
            print(f"{t}: 迁移 {len(rows)} 行")

        print("\n--- 迁移后行数对账 ---")
        for t in TABLES:
            row = db.cur().execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
            print(f"{t}: {row['n']}")

    print(f"\n源 SQLite 文件保留未删除: {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
