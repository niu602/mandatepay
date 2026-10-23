"""PayPal REST API 客户端（Orders v2）。

最小实现：获取访问令牌 → 创建订单 → 捕获订单。
参考官方文档：https://developer.paypal.com/api/rest/
"""
import uuid

import requests

from . import config


def get_access_token() -> str:
    """用 Client ID/Secret 换取 OAuth2 访问令牌。"""
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v1/oauth2/token",
        headers={
            "Accept": "application/json",
            "Accept-Language": "en_US",
        },
        data={"grant_type": "client_credentials"},
        auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_order(amount: float, currency: str = "USD") -> dict:
    """创建一笔 INTENT=CAPTURE 的订单，返回 PayPal 订单对象（含 id 与 approve 链接）。"""
    token = get_access_token()
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": currency,
                    "value": f"{amount:.2f}",
                }
            }
        ],
    }
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v2/checkout/orders",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def capture_order(order_id: str) -> dict:
    """买家在沙箱中完成付款后，服务端捕获订单，返回支付结果。"""
    token = get_access_token()
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def create_order_with_test_card(amount: float, currency: str = "USD") -> dict:
    """用沙箱测试卡在 API 层直接完成付款（**仅供回归测试**）。

    与普通 create_order 的区别：请求体带 `payment_source.card`，PayPal 会用测试卡直接扣款，
    订单创建返回的 status 即为 **COMPLETED**，不需要人去 approval_url 点确认，也不需要再 capture。

    这就是混合闭环方法论里 "simulate approve" 的 API 化实现：
    让 `create → simulate approve → capture → assert COMPLETED` 这条链路可以一条命令自动跑完。

    注意：
    - 必须带 `PayPal-Request-Id` 幂等头，否则返回 PAYPAL_REQUEST_ID_REQUIRED（400）；
    - 仅用于测试脚本。产品主流程仍然是「Agent 下单 + 人确认」，不做浏览器自动化。
    """
    token = get_access_token()
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {"amount": {"currency_code": currency, "value": f"{amount:.2f}"}}
        ],
        "payment_source": {
            "card": {
                "number": "4333134069790191",  # 沙箱 VISA 测试卡
                "expiry": "2031-11",
                "security_code": "123",
                "name": "Test Buyer",
                "billing_address": {
                    "address_line_1": "123 Main St",
                    "admin_area_2": "San Jose",
                    "admin_area_1": "CA",
                    "postal_code": "95131",
                    "country_code": "US",
                },
            }
        },
    }
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v2/checkout/orders",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def verify_webhook_signature(
    *,
    transmission_id: str,
    transmission_sig: str,
    transmission_time: str,
    cert_url: str,
    auth_algo: str,
    webhook_id: str,
    webhook_event: dict,
) -> dict:
    """调用 PayPal 官方接口校验 Webhook 签名。

    POST /v1/notifications/verify-webhook-signature
    成功返回 {"verification_status": "SUCCESS"}，失败为 "FAILURE"。

    官方接口会自行做 CRC32(body) + transmission_id + transmission_time
    + webhook_id 拼串，并用 cert_url 里的证书公钥验签，比本地手写更可靠。
    """
    token = get_access_token()
    payload = {
        "auth_algo": auth_algo,
        "cert_url": cert_url,
        "transmission_id": transmission_id,
        "transmission_sig": transmission_sig,
        "transmission_time": transmission_time,
        "webhook_id": webhook_id,
        "webhook_event": webhook_event,
    }
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        # 400 通常是请求体字段不对（如 webhook_id 与该应用不匹配）
        return {
            "verification_status": "FAILURE",
            "http_status": resp.status_code,
            "detail": resp.text[:500],
        }
    data = resp.json()
    data["http_status"] = resp.status_code
    return data


def list_webhooks() -> list[dict]:
    """列出该应用在 PayPal 后台注册的所有 Webhook（用于取 webhook_id）。"""
    token = get_access_token()
    resp = requests.get(
        f"{config.PAYPAL_API_BASE}/v1/notifications/webhooks",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("webhooks", [])
