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


def _public_base() -> str:
    """站点对外可访问的根地址（PayPal 批准后回跳用）。

    ⚠️ 为什么必须显式传，不能靠 Toolkit 默认值（踩过的坑，记在这里）：
    `paypal_agent_toolkit/shared/orders/parameters.py` 里
    `CreateOrderParameters` 的 `return_url` / `cancel_url` 默认值被**写死**成
    `https://example.com/returnUrl` / `https://example.com/cancelUrl`，且
    `model_config = ConfigDict(validate_default=True)` —— 我们不传，Pydantic
    就会把这个占位值填进去，`payload_util.parse_order_details` 再写进
    `payment_source.paypal.experience_context`。

    结果：买家在 PayPal 点「完成购买」后被 302 到 example.com，
    而那是一个 IANA 保留的**文档示例域名**，页面上只有一段
    「该域名仅用于文档示例，并非一项服务」的多语声明 —— 支付其实已经成功，
    但用户看到的是个假页面，退出时也看不到本站的结果页。

    本地首页（JS SDK 弹窗 + onApprove 直接 capture）不会暴露这个问题，
    因为浏览器从没被 302 过；只有整页 checkout（approval_url）才会踩到。

    取值顺序（先显式、再平台自动注入、最后本机兜底）：
      1. `PUBLIC_BASE_URL`            —— 显式配置，最可靠
      2. `WEBSITE_HOSTNAME`          —— Azure App Service 自动注入，无需手工配
      3. `RENDER_EXTERNAL_URL`       —— Render 自动注入（历史备用平台）
      4. `http://127.0.0.1:8888`     —— 本地开发兜底
    这样**忘配环境变量也不会把买家甩到 example.com**：部署到 Azure 上会自动
    解析成 https://<app>.azurewebsites.net。
    """
    explicit = (os.getenv("PUBLIC_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    host = (os.getenv("WEBSITE_HOSTNAME") or "").strip()  # Azure App Service
    if host:
        return f"https://{host}".rstrip("/")

    render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if render_url:
        return render_url.rstrip("/")

    return "http://127.0.0.1:8888"


def create_order(item_name: str, amount_usd: float, quantity: int = 1) -> dict:
    """创建一笔 PayPal 订单，返回包含 id / status / approval_url 的字典。

    ⚠️ 关于金额（踩过坑，记在这里）：
    Toolkit 的入参 schema **强制要求** `items[].item_total`（删掉会报 Field required），
    但它内部的 `parse_order_details` **只用 `item_cost`**，按
    `sum(item_cost × quantity)` 重算 subtotal / item_total —— 我们传进去的
    `item_total` 数值实际不参与计算。

    所以这里传的是**单价** item_cost = 总价 ÷ 数量，并让 item_total 与
    Toolkit 的重算结果保持一致（unit × quantity），避免两边数字打架。

    副作用：单价先四舍五入到分再乘数量，quantity>1 时实收总额与传入的
    amount_usd 会有分位漂移（例：总价 $10、数量 3 → item_cost 3.33 → 实收 $9.99）。
    本项目所有调用点都是 quantity=1（买一件），总额恰好精确。
    若将来要支持多件，必须改成"按分分配余数"，而不是简单除法。
    """
    tool = _get_tool("create_order")
    unit = round(amount_usd / quantity, 2)
    computed_total = round(unit * quantity, 2)
    base = _public_base()
    raw = tool.invoke(
        {
            "currency_code": "USD",
            "items": [
                {
                    "name": item_name,
                    "item_cost": unit,
                    "item_total": computed_total,  # schema 必填；值对齐 Toolkit 的重算结果
                    "quantity": quantity,
                }
            ],
            # 必须显式传：Toolkit 的默认值是 example.com 占位域（见 _public_base 注释）
            "return_url": f"{base}/paypal/return",
            "cancel_url": f"{base}/paypal/cancel",
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

    # ⚠️ Toolkit 的 capture_order 返回的是**包装结构**：
    #   {"message":..., "status":..., "amount":"USD 10.00", "raw": {完整捕获响应}}
    # 真实的 purchase_units/captures 在 raw 里。直接读顶层会拿到 None
    # → capture_id 恒为空 → 三层 Mandate 审计链丢失捕获 id。
    # 这里做兼容：有 raw 就从 raw 取，没有就按未包装的结构读。
    payload = result.get("raw") if isinstance(result.get("raw"), dict) else result
    captures = (
        payload.get("purchase_units", [{}])[0]
        .get("payments", {})
        .get("captures", [])
    )
    capture_id = captures[0].get("id") if captures else None
    return {
        "order_id": order_id,
        "status": payload.get("status") or result.get("status"),
        "capture_id": capture_id,
        "amount": result.get("amount"),  # 包装层已格式化成 "USD 10.00"
        "raw": payload,
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
    """列出订阅计划；传 product_id 则只列该供应商的计划。

    注意：PayPal 的 list 返回**精简视图**，不含 billing_cycles/定价。
    要拿价格必须再调 show_subscription_plan(plan_id) 取完整详情。
    """
    tool = _get_tool("list_subscription_plans")
    raw = tool.invoke({"product_id": product_id} if product_id else {})
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data.get("plans", []) or []


def show_subscription_plan(plan_id: str) -> dict:
    """取订阅计划完整详情 —— 含 billing_cycles[].pricing_scheme 定价与计费周期。"""
    tool = _get_tool("show_subscription_plan_details")
    raw = tool.invoke({"plan_id": plan_id})
    return json.loads(raw) if isinstance(raw, str) else raw
