"""本地种子数据 —— 不需要 PayPal 凭证，15 分钟跑通用的关键一步。

为什么需要这个
--------------
`scripts/setup_catalog.py` 会通过 PayPal Agent Toolkit 在**沙箱真实创建**
供应商与订阅计划 —— 所以它必须有 PayPal 凭证。而陌生人 clone 下来时
往往还没配好凭证，跑它会失败（而且失败信息是 `JSONDecodeError`，
完全看不出是凭证问题）。

但**目录数据本身是静态的**：Notion $10/月、Figma $15/月。
所以这里提供一条不依赖网络的路径：直接写入本地库。

两者的关系
----------
| 脚本 | 需要 PayPal 凭证 | 写什么 |
|---|---|---|
| `scripts/setup_catalog.py` | ✅ 需要 | 在**沙箱真实创建**产品/计划（Agent 下单时能引用真实 paypal_product_id） |
| `scripts/seed_catalog_local.py`（本脚本） | ❌ 不需要 | 只写**本地库目录表**（让页面有数据可看、Agent 能查到价格） |

**本脚本是安全的第一步**：不碰沙箱、不产生任何订单、幂等可重复跑。
想进一步打通沙箱产品（有 paypal_product_id 可下单）时，再跑 setup_catalog.py。

运行：
    python scripts/seed_catalog_local.py
    python scripts/seed_catalog_local.py --show     # 只看当前目录
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

# 目录种子数据 —— 与 scripts/setup_catalog.py 保持一致
# paypal_product_id / paypal_plan_id 用本地占位 ID（沙箱真实 ID 由 setup_catalog.py 创建）
CATALOG = [
    {
        "product_id": "LOCAL-NOTION-PRO",
        "vendor": "Notion",
        "vendor_desc": "Collaborative workspace and docs tool",
        "vendor_category": "Productivity",
        "plan": {
            "plan_id": "LOCAL-NOTION-PRO-MONTHLY",
            "name": "Notion Pro (monthly)",
            "amount": 10.0,
            "description": "Notion Pro monthly subscription",
        },
    },
    {
        "product_id": "LOCAL-FIGMA-PRO",
        "vendor": "Figma",
        "vendor_desc": "Collaborative design tool",
        "vendor_category": "Design",
        "plan": {
            "plan_id": "LOCAL-FIGMA-PRO-MONTHLY",
            "name": "Figma Professional (monthly)",
            "amount": 15.0,
            "description": "Figma Professional monthly subscription",
        },
    },
]


def show() -> None:
    """打印当前目录。"""
    vendors = store.list_vendors()
    plans = store.list_subscription_plans()
    print(f"=== 当前目录（backend: {store.backend_name()}）===")
    print(f"  供应商 {len(vendors)} 个 / 订阅计划 {len(plans)} 个")
    for v in vendors:
        print(f"  · {v.get('name'):<10s} {v.get('description') or ''}")
    for p in plans:
        print(
            f"    └ {p.get('name'):<28s} ${float(p.get('amount_usd') or 0):.2f}"
            f" /{p.get('interval_count', 1)}{p.get('interval_unit', 'MONTH')}"
        )


def seed() -> None:
    """写入目录种子数据（幂等）。

    **只在库为空时写入** —— 避免和 `setup_catalog.py` 创建的沙箱真实目录重复
    （重复会让 Agent 查目录时看到两个 Notion，消歧变难）。
    """
    existing = store.list_vendors()
    if existing:
        print(f"=== 跳过：库里已有 {len(existing)} 个供应商 ===")
        print("      （可能已经跑过 setup_catalog.py 同步了沙箱真实目录，")
        print("        重复写入会让 Agent 查目录时看到重名供应商）")
        print()
        show()
        return

    print(f"=== 写入本地目录（backend: {store.backend_name()}）===")
    for item in CATALOG:
        store.save_vendor(
            paypal_product_id=item["product_id"],
            name=item["vendor"],
            description=item["vendor_desc"],
            category=item["vendor_category"],
        )
        p = item["plan"]
        store.save_subscription_plan(
            paypal_plan_id=p["plan_id"],
            paypal_product_id=item["product_id"],
            name=p["name"],
            amount_usd=p["amount"],
            currency="USD",
            interval_unit="MONTH",
            interval_count=1,
            description=p["description"],
        )
        print(f"  ✅ {item['vendor']:<8s} {p['name']:<28s} ${p['amount']:.2f}/MONTH")

    print()
    print("提示：本步骤只写本地库，**没有在 PayPal 沙箱创建任何东西**。")
    print("      想让 Agent 下单时能引用真实沙箱产品，跑：")
    print("        python scripts/setup_catalog.py      # 需要 PayPal 沙箱凭证")


def main() -> int:
    ap = argparse.ArgumentParser(description="本地目录种子（不依赖 PayPal 凭证）")
    ap.add_argument("--show", action="store_true", help="只显示当前目录，不写入")
    args = ap.parse_args()

    if args.show:
        show()
        return 0

    seed()
    print()
    show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
