"""D9 建库脚本：用 PayPal Toolkit 创建示例供应商与 SaaS 订阅计划，并落本地目录。

示例：Notion / Figma 两个供应商，各建一个月度订阅计划。
按供应商名称幂等：已存在则跳过创建，避免重复堆积。

运行：python scripts/setup_catalog.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认可能是 GBK，中文输出会乱码；强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from agent import paypal_tools, store

# 示例目录：供应商 → 订阅计划
CATALOG = [
    {
        "vendor": "Notion",
        "description": "All-in-one workspace",
        "plan_name": "Notion Plus",
        "amount_usd": 10.0,
        "interval_unit": "MONTH",
    },
    {
        "vendor": "Figma",
        "description": "Collaborative design tool",
        "plan_name": "Figma Professional",
        "amount_usd": 15.0,
        "interval_unit": "MONTH",
    },
]


def main() -> int:
    existing = {v["name"] for v in store.list_vendors()}

    for item in CATALOG:
        vendor = item["vendor"]
        if vendor in existing:
            print(f"⏭  供应商「{vendor}」已存在，跳过")
            continue

        prod = paypal_tools.create_product(vendor, item["description"])
        pid = prod.get("product_id")
        if not pid:
            print(f"❌ 创建供应商「{vendor}」失败：{prod}")
            continue
        store.save_vendor(pid, vendor, item["description"])
        print(f"✅ 供应商「{vendor}」已创建：{pid}")

        plan = paypal_tools.create_subscription_plan(
            product_id=pid,
            name=item["plan_name"],
            amount_usd=item["amount_usd"],
            interval_unit=item["interval_unit"],
            description=f"{vendor} 月度订阅",
        )
        plan_id = plan.get("plan_id")
        if not plan_id:
            print(f"❌ 创建计划「{item['plan_name']}」失败：{plan}")
            continue
        store.save_subscription_plan(
            plan_id, pid, item["plan_name"], item["amount_usd"],
            interval_unit=item["interval_unit"], description=f"{vendor} 月度订阅",
        )
        print(f"   └ 计划「{item['plan_name']}」${item['amount_usd']:.2f}/{item['interval_unit'].lower()}：{plan_id}")

    print("\n=== 本地目录 ===")
    print("供应商：")
    for v in store.list_vendors():
        print(f"  - {v['name']} ({v['paypal_product_id']})")
    print("订阅计划：")
    for p in store.list_subscription_plans():
        print(f"  - {p['name']} ${p['amount_usd']:.2f}/{p['interval_unit'].lower()} (vendor={p['paypal_product_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
