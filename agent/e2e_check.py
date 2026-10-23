"""支付闭环校验脚本：create → (人 approve) → capture → assert COMPLETED。

设计依据（HANDOFF / 交接指示 §九.4 混合闭环方法论）：
不要追求"全自动"或"纯人工"二选一。正确做法是混合闭环——
机器驱动可控部分（创建订单、捕获、断言），人只点最后一步"完成购买"。

为什么不做浏览器自动化接管弹窗：
PAYER_ACTION_REQUIRED 是产品安全设计，不是 bug。MandatePay 的命题就是
"AI 不能偷偷花钱"，全自动扣款与核心叙事冲突；且沙箱弹窗开合不稳定，
自动化成本是无底洞。详见 README「PAYER_ACTION_REQUIRED 不是 bug」。

两种跑法：

**A. 混合闭环（产品主流程，Demo 走这条）** —— 机器驱动可控部分，人只点最后一步：
    # 1. 创建订单，拿到 approval_url 给人在沙箱里付款
    python -m agent.e2e_check --create --amount 10 --item "Notion Pro"

    # 2. 人付款完成后，捕获并断言 COMPLETED
    python -m agent.e2e_check --capture <ORDER_ID>

    # 3. 一步到位（创建后等待你在浏览器付款，回车继续）
    python -m agent.e2e_check --interactive --amount 10

**B. 全自动回归（--auto，仅测试用）** —— 对应方法论里的 "simulate approve"：
    用沙箱测试卡在 API 层完成付款，一条命令跑完
    `create → simulate approve → assert COMPLETED`，不需要人点 UI。
    python -m agent.e2e_check --auto --amount 10
"""
from __future__ import annotations

import argparse
import sys

from .paypal_tools import capture_order, create_order


def step_create(amount: float, item: str) -> str | None:
    """第 1 步：创建订单，返回 order_id。"""
    print(f"[1/3] 创建订单: {item} × ${amount:.2f}")
    res = create_order(item, amount)
    order_id = res["order_id"]
    print(f"      order_id = {order_id}")
    print(f"      status   = {res['status']}")
    print(f"      approval_url = {res['approval_url']}")
    return order_id


def step_capture(order_id: str) -> bool:
    """第 2-3 步：捕获订单并断言 COMPLETED。"""
    print(f"\n[2/3] 捕获订单: {order_id}")
    try:
        res = capture_order(order_id)
    except Exception as exc:
        print(f"      ❌ 捕获失败: {exc}")
        print("      提示：订单可能还未被买家 approve（需先去 approval_url 付款）")
        return False

    status = res.get("status")
    capture_id = res.get("capture_id")
    print(f"      status     = {status}")
    print(f"      capture_id = {capture_id}")

    print(f"\n[3/3] 断言 COMPLETED")
    if status == "COMPLETED":
        print(f"      ✅ 闭环成功 —— order={order_id} capture={capture_id}")
        return True
    print(f"      ❌ 断言失败，当前状态={status}（期望 COMPLETED）")
    return False


def step_auto(amount: float, item: str) -> bool:
    """全自动回归：create(测试卡直付 = create+approve 合并) → assert COMPLETED。

    这是混合闭环方法论里 "simulate approve" 的 API 化实现：PayPal 在订单创建时用测试卡
    直接扣款，返回的 status 即 COMPLETED，不需要人去 approval_url 点确认，也不需要再 capture。
    仅用于回归测试；产品主流程仍是「Agent 下单 + 人确认」。
    """
    from app.paypal_client import create_order_with_test_card

    print(f"[1/2] 用测试卡创建订单并直接付款: {item} × ${amount:.2f}")
    print("      （payment_source.card = 沙箱测试卡，等价于 simulate approve）")
    try:
        res = create_order_with_test_card(amount)
    except Exception as exc:  # noqa: BLE001
        print(f"      ❌ 创建/付款失败: {exc}")
        return False

    order_id = res.get("id")
    status = res.get("status")
    print(f"      order_id = {order_id}")
    print(f"      status   = {status}")

    capture_id = None
    try:
        capture_id = res["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError):
        pass

    print("\n[2/2] 断言 COMPLETED")
    if status == "COMPLETED":
        print(f"      ✅ 闭环成功 —— order={order_id} capture={capture_id}")
        print("      （无需再 capture：测试卡直付在创建时已完成扣款）")
        return True
    print(f"      ❌ 断言失败，当前状态={status}（期望 COMPLETED）")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="MandatePay 支付闭环校验")
    ap.add_argument("--create", action="store_true", help="创建订单")
    ap.add_argument("--capture", metavar="ORDER_ID", help="捕获指定订单并断言 COMPLETED")
    ap.add_argument("--interactive", action="store_true", help="创建订单→等人付款→捕获断言")
    ap.add_argument("--auto", action="store_true", help="全自动：测试卡直付并断言 COMPLETED（仅测试用）")
    ap.add_argument("--amount", type=float, default=10.0)
    ap.add_argument("--item", default="MandatePay Test Item")
    args = ap.parse_args()

    if args.auto:
        return 0 if step_auto(args.amount, args.item) else 1

    if args.capture:
        return 0 if step_capture(args.capture) else 1

    if args.create:
        oid = step_create(args.amount, args.item)
        if not oid:
            return 1
        print("\n下一步：在浏览器打开上面的 approval_url，用沙箱测试买家账号登录付款，")
        print(f"然后运行： python -m agent.e2e_check --capture {oid}")
        return 0

    if args.interactive:
        oid = step_create(args.amount, args.item)
        if not oid:
            return 1
        print("\n>>> 请在浏览器打开 approval_url，用测试买家账号完成付款 <<<")
        input("付款完成后按回车继续捕获...")
        return 0 if step_capture(oid) else 1

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
