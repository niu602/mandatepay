"""回填 mandates.vendor / amount_usd —— 预算仪表盘与审计页需要它们。

背景：`vendor` 与 `amount_usd` 两列是 D11 为「按供应商分布 / 预算统计」加的，
加列前创建的 mandate 这两列为空，审计页会显示 $0.00、分布图只能显示「未标注」。
这些历史行的文本格式是固定的，可以可靠反解：
- `intent` 格式 `采购 <商品>（供应商 <vendor>）` → 正则取 vendor
- `cart`   格式 `<商品> × $<amount> (<currency>)`      → 正则取 amount

幂等：可重复运行，只更新空值。

运行：python scripts/backfill_mandate_vendor.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from agent import store  # noqa: E402

# 匹配 intent 里的「（供应商 XXX）」
_VENDOR_RE = re.compile(r"供应商\s*([^）\s]+)\s*[）)]")
# 匹配 cart 里的「× $12.34」
_AMOUNT_RE = re.compile(r"×\s*\$\s*(\d+(?:\.\d+)?)")


def main() -> int:
    ap = argparse.ArgumentParser(description="回填 mandates.vendor / amount_usd")
    ap.add_argument("--dry-run", action="store_true", help="只统计不改库")
    args = ap.parse_args()

    with store._Conn() as db:
        rows = db.cur().execute(
            "SELECT id, intent, cart, vendor, amount_usd FROM mandates "
            "WHERE COALESCE(vendor,'')='' OR COALESCE(amount_usd,0)=0"
        ).fetchall()

    print(f"待回填行数：{len(rows)}")
    if not rows:
        print("✅ 无需回填")
        return 0

    v_updates: list[tuple[str, int]] = []
    a_updates: list[tuple[float, int]] = []
    for r in rows:
        m = _VENDOR_RE.search(r.get("intent") or "")
        if m and not (r.get("vendor") or "").strip():
            v_updates.append((m.group(1), r["id"]))
        a = _AMOUNT_RE.search(r.get("cart") or "")
        if a and not float(r.get("amount_usd") or 0):
            try:
                a_updates.append((float(a.group(1)), r["id"]))
            except ValueError:
                pass

    print(f"可解析供应商：{len(v_updates)} 行；可解析金额：{len(a_updates)} 行")
    if v_updates:
        print(f"  例：→ {v_updates[0][0]}")
    if a_updates:
        print(f"  例：→ ${a_updates[0][0]:.2f}")

    if not v_updates and not a_updates:
        print("ℹ️ 没有可解析的行（格式不匹配），保持原样")
        return 0

    if args.dry_run:
        print("（dry-run，未写库）")
        return 0

    with store._Conn() as db:
        cur = db.cur()
        for v, rid in v_updates:
            cur.execute("UPDATE mandates SET vendor=? WHERE id=?", (v, rid))
        for amt, rid in a_updates:
            cur.execute("UPDATE mandates SET amount_usd=? WHERE id=?", (amt, rid))

    print(f"✅ 已回填 vendor {len(v_updates)} 行、amount_usd {len(a_updates)} 行")

    with store._Conn() as db:
        left = db.cur().execute(
            "SELECT COUNT(*) n FROM mandates WHERE COALESCE(vendor,'')='' OR COALESCE(amount_usd,0)=0"
        ).fetchone()
        print(f"剩余未回填行：{left['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
