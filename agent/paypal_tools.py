"""PayPal Agent Toolkit 的薄封装。

职责单一：把官方 toolkit 的 LangChain Tool 包一层同步调用接口，
让状态机 pay_node 可以直接用，而不必在节点里处理 toolkit 实例化细节。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from dotenv import load_dotenv
from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
from paypal_agent_toolkit.shared.configuration import Configuration, Context

load_dotenv()


@lru_cache(maxsize=1)
def _toolkit() -> PayPalToolkit:
    """延迟实例化：第一次调用时才读 env 并连接 PayPal。"""
    return PayPalToolkit(
        client_id=os.environ["PAYPAL_CLIENT_ID"],
        secret=os.environ["PAYPAL_CLIENT_SECRET"],
        configuration=Configuration(
            actions={
                "orders": {"create": True, "get": True, "capture": True},
                "products": {"create": True, "list": True},
                "subscriptionPlans": {"create": True, "list": True, "show": True},
            },
            context=Context(sandbox=True),
        ),
    )


def _get_tool(name: str):
    for tool in _toolkit().get_tools():
        if tool.name == name:
            return tool
    raise RuntimeError(f"PayPal Toolkit 未提供工具: {name}")


def create_order(item_name: str, amount_usd: float, quantity: int = 1) -> dict:
    """创建一笔 PayPal 订单，返回包含 id / status / approval_url 的字典。"""
    tool = _get_tool("create_order")
    raw = tool.invoke(
        {
            "currency_code": "USD",
            "items": [
                {
                    "name": item_name,
                    "item_cost": round(amount_usd / quantity, 2),
                    "item_total": round(amount_usd, 2),
                    "quantity": quantity,
                }
            ],
        }
    )
    # toolkit 返回的是 JSON 字符串
    order = json.loads(raw) if isinstance(raw, str) else raw

    approval_url = None
    for link in order.get("links", []):
        if link.get("rel") == "payer-action":
            approval_url = link["href"]
            break

    return {
        "order_id": order.get("id"),
        "status": order.get("status"),
        "approval_url": approval_url,
        "raw": order,
    }


def capture_order(order_id: str) -> dict:
    """捕获已批准的订单。买家在沙箱完成付款后调用。

    注意：订单未被买家 approve 时 PayPal 返回 422 且响应体为空，
    toolkit 会返回空字符串。这里转成明确异常，避免上层看到
    「Expecting value」这种无从下手的 JSON 解析错误。
    """
    tool = _get_tool("capture_order")
    raw = tool.invoke({"order_id": order_id})

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise RuntimeError(
            f"PayPal 返回空响应 —— 订单 {order_id} 很可能尚未被买家 approve。"
            f"请先打开 approval_url 用沙箱测试买家账号完成付款，再捕获。"
        )

    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        raise RuntimeError(
            f"PayPal 返回非 JSON 响应（订单 {order_id} 可能尚未 approve）"
        ) from None
    captures = (
        result.get("purchase_units", [{}])[0]
        .get("payments", {})
        .get("captures", [])
    )
    capture_id = captures[0].get("id") if captures else None
    return {
        "order_id": order_id,
        "status": result.get("status"),
        "capture_id": capture_id,
        "raw": result,
    }


# ── 供应商目录 / SaaS 订阅计划（P1 功能）──────────────────────────

def create_product(name: str, description: str, product_type: str = "SERVICE") -> dict:
    """在 PayPal 创建供应商（产品）。返回 {product_id, name, raw}。"""
    tool = _get_tool("create_product")
    raw = tool.invoke({"name": name, "description": description, "type": product_type})
    prod = json.loads(raw) if isinstance(raw, str) else raw
    return {"product_id": prod.get("id"), "name": name, "raw": prod}


def list_products() -> list[dict]:
    """列出 PayPal 中的供应商（产品）。"""
    tool = _get_tool("list_products")
    raw = tool.invoke({})
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data.get("products", []) or []


def create_subscription_plan(
    product_id: str,
    name: str,
    amount_usd: float,
    currency: str = "USD",
    interval_unit: str = "MONTH",
    interval_count: int = 1,
    description: str | None = None,
) -> dict:
    """为某供应商创建 SaaS 订阅计划（如 Notion Pro $10/月）。

    返回 {plan_id, name, raw}。billing_cycles / payment_preferences 按
    PayPal Subscriptions 标准结构构造（单一定价周期 + 自动续费）。
    """
    tool = _get_tool("create_subscription_plan")
    payload = {
        "product_id": product_id,
        "name": name,
        "description": description or name,
        "billing_cycles": [
            {
                "frequency": {"interval_unit": interval_unit, "interval_count": interval_count},
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": 0,
                "pricing_scheme": {
                    "fixed_price": {"value": f"{amount_usd:.2f}", "currency_code": currency}
                },
            }
        ],
        "payment_preferences": {
            "auto_bill_outstanding": True,
            "setup_fee": {"value": "0", "currency_code": currency},
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 3,
        },
    }
    raw = tool.invoke(payload)
    data = json.loads(raw) if isinstance(raw, str) else raw
    return {"plan_id": data.get("id"), "name": name, "raw": data}


def list_subscription_plans(product_id: str | None = None) -> list[dict]:
    """列出订阅计划；传 product_id 则只列该供应商的计划。"""
    tool = _get_tool("list_subscription_plans")
    raw = tool.invoke({"product_id": product_id} if product_id else {})
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data.get("plans", []) or []
