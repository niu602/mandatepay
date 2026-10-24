"""重置演示数据 —— 录 Demo 前把测试期产生的交易记录清干净。

为什么需要：约束引擎的预算口径是「已承诺支出」（下单即计入），测试期跑了几十笔
沙箱订单，预算会被占满，录 Demo 时新订单会直接被 REJECT —— 看起来像功能坏了。
所以录制前需要清场。

**默认只做 dry-run**，必须显式加 --confirm 才会真的删。
约束（constraints）默认保留，需要清请加 --reset-constraints。

运行：
    python scripts/reset_demo_data.py                # 只看会删什么
    python scripts/reset_demo_data.py --confirm      # 真删（保留约束）
    python scripts/reset_demo_data.py --confirm --reset-constraints
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from agent import store  # noqa: E402

TABLES = ("mandates", "webhook_events", "purchase_requests")


def main() -> int:
    ap = argparse.ArgumentParser(description="重置演示数据")
    ap.add_argument("--confirm", action="store_true", help="真正执行删除（不加则只 dry-run）")
    ap.add_argument("--reset-constraints", action="store_true", help="同时清空约束")
    args = ap.parse_args()

    with store._Conn() as db:
        cur = db.cur()
        counts = {}
        for t in TABLES:
            try:
                counts[t] = cur.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
            except Exception as exc:
                counts[t] = f"(查询失败: {exc})"
        try:
            counts["constraints"] = cur.execute("SELECT COUNT(*) n FROM constraints").fetchone()["n"]
        except Exception:
            counts["constraints"] = "?"

    print("当前数据量：")
    for k, v in counts.items():
        print(f"  {k:20s} {v}")

    targets = list(TABLES) + (["constraints"] if args.reset_constraints else [])
    print()
    print("将清空：", ", ".join(targets))
    if not args.reset_constraints:
        print("（保留 constraints —— 加 --reset-constraints 可一并清空）")

    if not args.confirm:
        print()
        print("ℹ️ 这是 dry-run。确认无误后加 --confirm 执行。")
        return 0

    with store._Conn() as db:
        cur = db.cur()
        for t in targets:
            try:
                cur.execute(f"DELETE FROM {t}")
                print(f"  ✅ 已清空 {t}")
            except Exception as exc:
                print(f"  ❌ 清空 {t} 失败: {exc}")

    print()
    with store._Conn() as db:
        for t in TABLES:
            n = db.cur().execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
            print(f"  {t:20s} 剩余 {n}")
    print("\n✅ 演示数据已重置，可以开始录制。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
