#!/usr/bin/env python
"""一键造三笔演示数据，覆盖三种判定（AUTO_APPROVE / REQUIRE_HUMAN / REJECT）。

为什么需要这个脚本
------------------
评审期（12-01~12-15）线上库必须有一组**恰好覆盖三种判定**的演示交易，
给 W-14 镜头④（审计链）当素材。

风险点：
1. 有人跑了 `reset_demo_data.py --confirm` → 数据被清空
2. 演示当天手滑在页面上造了一堆杂单→ 判定分布不干净
3. 备份/迁移后数据没带过来

这个脚本把"重造"变成一条命令，且**幂等**（重复跑不会重复堆数据）。

三种场景与预期判定
------------------
| 供应商 | 金额 | 判定 | 理由 |
|---|---|---|---|
| Notion | $10 | AUTO_APPROVE | 在白名单内、远低于 $50 阈值 |
| Figma | $80 | REQUIRE_HUMAN | 在白名单内、超 $50 单笔阈值 → 停下问人 |
| Slack  | $15 | REJECT | 不在供应商白名单 → 直接拦 |

判定由生产引擎 `agent/constraints.py` 的 `evaluate()` 给出，
不是这里硬编码 —— 所以如果约束变了，脚本造出来的判定也会跟着变，
**不会造出与线上约束不一致的假数据**。

用法
----
```bash
# 干跑（只看会造什么，不写库）
python scripts/seed_demo_scenarios.py

# 真造
python scripts/seed_demo_scenarios.py --confirm

# 先清掉现有的演示交易再重造（幂等重建）
python scripts/seed_demo_scenarios.py --confirm --reset

# 指定其他库（默认读 .env 的 DATABASE_URL）
DATABASE_URL=postgresql://... python scripts/seed_demo_scenarios.py --confirm
```

退出码：0 = 成功；1 = 有场景没造出预期判定（数据不一致，需排查）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

# ── 三种场景（金额刻意贴着阈值与白名单边界，让判定有说服力）──────────
SCENARIOS = [
    {
        "vendor": "Notion",
        "item": "Notion Pro",
        "amount_usd": 10.0,
        "category": "Productivity",
        "expect": "AUTO_APPROVE",
        "why": "在白名单内、远低于 $50 阈值 → 自动放行",
    },
    {
        "vendor": "Figma",
        "item": "Figma Enterprise",
        "amount_usd": 80.0,
        "category": "Design",
        "expect": "REQUIRE_HUMAN",
        "why": "在白名单内、超 $50 单笔阈值 → 停下问人",
    },
    {
        "vendor": "Slack",
        "item": "Slack Pro",
        "amount_usd": 15.0,
        "category": "Productivity",
        "expect": "REJECT",
        "why": "不在供应商白名单 → 直接拦下",
    },
]


def _demo_traces() -> set[str]:
    """找出已存在的演示交易 trace_id（用于幂等判断）。

    判据：三层 Mandate 齐全且 vendor 是我们这三个演示供应商之一的。
    这样不会把评委/用户自己在页面上造的同名订单误判成演示数据。
    """
    from agent import store

    demo_vendors = {s["vendor"] for s in SCENARIOS}
    found: set[str] = set()
    with store._Conn() as db:
        rows = db.cur().execute(
            "SELECT trace_id, vendor, layer FROM mandates"
        ).fetchall()
    by_trace: dict[str, set[str]] = {}
    vendor_of: dict[str, str] = {}
    for r in rows:
        by_trace.setdefault(r["trace_id"], set()).add(r["layer"])
        vendor_of[r["trace_id"]] = r.get("vendor") or ""
    for tid, layers in by_trace.items():
        # 三层齐全 + 供应商是演示供应商 → 认定为演示数据
        if {"intent", "cart", "payment"} <= layers and vendor_of[tid] in demo_vendors:
            found.add(tid)
    return found


def _reset_demo() -> int:
    """删掉现有演示交易（只删演示供应商的，保留其他数据）。

    注意两点（都是踩过的坑）：
    1. 按 `agent/store.py` 的约定，SQL 一律用 `?` 占位符书写，
       由 store 的 `_sql()` 按后端自动转成 PG 的 `%s`。
       直接写 `%s` 在 SQLite 上会报 `near "%": syntax error`。
    2. `store._Cursor` **没有 `rowcount`**（它只抹平 lastrowid / 占位符）。
       所以不能靠 `cur.rowcount` 算删了几行 —— 改用「删前查数、删后再查数」相减，
       而且**不吞异常**：吞掉会变成假绿（看着成功，其实一行没删）。
    """
    from agent import store

    demo_vendors = [s["vendor"] for s in SCENARIOS]
    marks = ",".join(["?"] * len(demo_vendors))

    with store._Conn() as db:
        cur = db.cur()

        before = cur.execute(
            f"SELECT COUNT(*) n FROM mandates WHERE vendor IN ({marks})", demo_vendors
        ).fetchone()["n"]

        traces = [
            r["trace_id"]
            for r in cur.execute(
                f"SELECT DISTINCT trace_id FROM mandates WHERE vendor IN ({marks})",
                demo_vendors,
            ).fetchall()
        ]
        if traces:
            tmarks = ",".join(["?"] * len(traces))
            cur.execute(f"DELETE FROM mandates WHERE trace_id IN ({tmarks})", traces)

        # purchase_requests 没有 trace_id 列（按 vendor 删）
        cur.execute(f"DELETE FROM purchase_requests WHERE vendor IN ({marks})", demo_vendors)

        after = cur.execute(
            f"SELECT COUNT(*) n FROM mandates WHERE vendor IN ({marks})", demo_vendors
        ).fetchone()["n"]

    removed = before - after
    print(f"  已清理旧演示数据: mandates {before} → {after} 行（删除 {removed} 行）")
    return removed


def _make_one(scenario: dict, constraint) -> tuple[str, str, str | None]:
    """造一笔，返回 (实际判定, trace_id, 错误信息)。

    判定与落库路径与线上 `/api/agent/requests` **完全一致**：
    `evaluate()` → `build_mandate_trio()` → `store.save_mandates(trio)`。
    不经过 LLM/自然语言解析 —— 演示数据不该受端点过载影响。

    **与线上端点的唯一差别**：不调用 PayPal 下单。
    理由：演示数据要的是「三层授权记录」这个审计素材，不是真的扣款；
    而真下单会在 PayPal 沙箱后台留一堆垃圾订单，评委翻后台时反而困惑。
    线上端点会为非 REJECT 的场景建真实订单，这里刻意不建。
    """
    from agent import store
    from agent.constraints import evaluate
    from agent.models import MandateStatus, PurchaseRequest, _now_iso, build_mandate_trio

    req = PurchaseRequest(
        vendor=scenario["vendor"],
        item=scenario["item"],
        amount_usd=scenario["amount_usd"],
        currency="USD",
        category=scenario["category"],
        recurring=True,
    )
    store.save_request(req, _now_iso())

    result = evaluate(req, constraint)
    decision = result.decision.value

    status_map = {
        "AUTO_APPROVE": MandateStatus.APPROVED,
        "REQUIRE_HUMAN": MandateStatus.PENDING_HUMAN,
        "REJECT": MandateStatus.REJECTED,
    }
    trio = build_mandate_trio(
        intent=f"采购 {req.item}（供应商 {req.vendor}）",
        cart=f"{req.item} × ${req.amount_usd:.2f} ({req.currency})",
        payment=(
            "PayPal Sandbox · "
            + (
                "自动扣款"
                if decision == "AUTO_APPROVE"
                else ("人工确认后扣款" if decision == "REQUIRE_HUMAN" else "未扣款")
            )
        ),
        amount_usd=req.amount_usd,
        vendor=req.vendor,
        status=status_map[decision],
        decision=result.decision,
    )
    store.save_mandates(trio)
    return decision, trio[-1].trace_id, None


def main() -> int:
    ap = argparse.ArgumentParser(description="一键造三笔覆盖三种判定的演示数据")
    ap.add_argument("--confirm", action="store_true", help="真正写库（不加则只干跑）")
    ap.add_argument("--reset", action="store_true", help="先删掉现有演示交易再重造")
    args = ap.parse_args()

    from agent import store
    from agent.constraints import evaluate

    print("=" * 74)
    print("演示数据 seed —— 三种判定各一笔")
    print("=" * 74)
    print(f"目标后端: {store.backend_name()}")
    if not args.confirm:
        print("（干跑模式，加 --confirm 才写库）")

    constraint = store.load_latest_constraint()
    if constraint is None:
        print("\n❌ 库里没有生效的约束 —— 先跑 `python scripts/deploy_init.sh` 或手动设约束")
        print("   没有约束，三种场景的判定会全部退化成默认行为，演示数据失去意义。")
        return 1

    print(f"\n当前约束: 预算 ${constraint.monthly_budget_usd} / "
          f"单笔阈值 ${constraint.single_tx_threshold_usd} / "
          f"白名单 {constraint.vendor_whitelist or '（空）'}")

    existing = _demo_traces()
    if existing:
        print(f"\n已存在的演示交易: {len(existing)} 笔（trace: {', '.join(sorted(existing)[:3])}…）")
        if not args.reset:
            print("→ 已存在且未指定 --reset：**跳过**（幂等，不重复堆数据）")
            print("   想重建请加 --reset")
            return 0

    if args.reset and args.confirm:
        n = _reset_demo()
        print(f"\n已清理旧演示数据: {n} 行")

    if not args.confirm:
        print("\n将要造的数据：")
        for s in SCENARIOS:
            print(f"  · {s['vendor']:8s} ${s['amount_usd']:>7.2f}  预期 {s['expect']:14s} {s['why']}")
        print("\n（干跑结束，加 --confirm 执行）")
        return 0

    print()
    ok = True
    for s in SCENARIOS:
        decision, trace, err = _make_one(s, constraint)
        if err:
            print(f"  ❌ {s['vendor']:8s} ${s['amount_usd']:>7.2f} → {err}")
            ok = False
            continue
        match = decision == s["expect"]
        flag = "✅" if match else "⚠️ "
        print(f"  {flag} {s['vendor']:8s} ${s['amount_usd']:>7.2f} → {decision:14s} "
              f"trace={trace[:8]}…")
        if not match:
            print(f"      预期 {s['expect']}，实际 {decision} —— 约束可能变了，检查是否需要调整金额")
            ok = False

    # 复核：库里三种判定是否齐
    print("\n=== 复核：线上库判定分布 ===")
    from collections import Counter

    with store._Conn() as db:
        rows = db.cur().execute(
            "SELECT decision, COUNT(*) n FROM mandates WHERE layer='intent' GROUP BY decision"
        ).fetchall()
    dist = Counter({r["decision"]: r["n"] for r in rows})
    for d in ("AUTO_APPROVE", "REQUIRE_HUMAN", "REJECT"):
        print(f"  {d:16s} {dist.get(d, 0)} 笔")
    missing = [d for d in ("AUTO_APPROVE", "REQUIRE_HUMAN", "REJECT") if not dist.get(d)]
    if missing:
        print(f"\n❌ 缺少判定: {missing}")
        ok = False
    else:
        print("\n✅ 三种判定齐全，镜头④素材就绪")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
