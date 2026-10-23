"""把旧的单条 Mandate 记录拆成三层（Intent / Cart / Payment）记录。

背景：D5 原始要求是「每次交易写入 Intent/Cart/Payment 三个 mandate 记录」，
早期实现偷懒成「一条记录塞三个字段」（models.py 的注释甚至写的就是三条，与实现不符）。
本脚本把历史数据补齐为三条，使库内形态统一。

设计：
- 幂等：只处理 `trace_id = ''` 的旧记录，跑多少次都不会重复拆。
- 不删数据：原记录原地补 `trace_id` / `layer` / `content`，另插两条同 trace 的记录。
- 三层共享 status / decision / order_id / capture_id / created_at，保证聚合视图与拆分前完全一致。

用法：
    python scripts/migrate_mandate_layers.py            # 执行
    python scripts/migrate_mandate_layers.py --dry-run  # 只看会改多少条
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import store  # noqa: E402


LAYERS = ("intent", "cart", "payment")


def main() -> int:
    ap = argparse.ArgumentParser(description="Mandate 三层结构迁移")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = ap.parse_args()

    with store._Conn() as db:
        cur = db.cur()
        legacy = cur.execute(
            "SELECT * FROM mandates WHERE trace_id = '' OR trace_id IS NULL ORDER BY id"
        ).fetchall()

    if not legacy:
        print("✅ 无旧数据需要迁移（所有记录都已是三层结构）")
        return 0

    print(f"发现 {len(legacy)} 条旧记录（trace_id 为空）→ 将拆为 {len(legacy) * 3} 条三层记录")
    if args.dry_run:
        print("--dry-run：未写入")
        return 0

    done = 0
    for r in legacy:
        trace = str(uuid.uuid4())
        with store._Conn() as db:
            cur = db.cur()
            # 1) 原记录补 trace_id / layer / content（作为 intent 层）
            cur.execute(
                "UPDATE mandates SET trace_id=?, layer='intent', content=? WHERE id=?",
                (trace, r["intent"], r["id"]),
            )
            # 2) 另插 cart / payment 两层，字段快照照抄，mandate_uuid 各自唯一
            for layer, content in (("cart", r["cart"]), ("payment", r["payment"])):
                cur.execute(
                    """INSERT INTO mandates
                       (mandate_uuid, trace_id, layer, content, intent, cart, payment,
                        status, decision, order_id, capture_id, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        trace,
                        layer,
                        content,
                        r["intent"],
                        r["cart"],
                        r["payment"],
                        r["status"],
                        r["decision"],
                        r["order_id"],
                        r["capture_id"],
                        r["created_at"],
                    ),
                )
        done += 1

    print(f"✅ 已迁移 {done} 笔交易 → {done * 3} 条记录")

    with store._Conn() as db:
        total = db.cur().execute("SELECT COUNT(*) AS n FROM mandates").fetchone()["n"]
    print(f"当前 mandates 表共 {total} 条记录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
