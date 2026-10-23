"""D9 验证：Verify 节点 + Webhook 异步状态同步。

证明链路：Agent 创建订单（PAYER_ACTION_REQUIRED）→ 写 order_id 到三层 Mandate
→ PayPal 真实回调 PAYMENT.CAPTURE.COMPLETED → Webhook 处理器把三层 Mandate 翻成 COMPLETED。

不在 .env 里改配置：本脚本在自己的进程内设置 PAYPAL_WEBHOOK_SKIP_VERIFY=true，
用 FastAPI TestClient 真实走一遍 HTTP 链路（签名校验被跳过，但业务处理路径与生产一致）。

运行：python scripts/verify_async_sync.py
"""
from __future__ import annotations

import os
import sys

# 让脚本能 import 项目内模块（agent / app）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PAYPAL_WEBHOOK_SKIP_VERIFY"] = "true"  # 仅本进程生效，不动 .env

from fastapi.testclient import TestClient  # noqa: E402

from agent import store  # noqa: E402
from app.main import app  # noqa: E402

BASE = "http://test"


def _dummy_sig_headers() -> dict:
    # 跳过验签后，处理器仍要求 transmission-* 头存在（顺序在前），给占位值即可
    return {
        "paypal-transmission-id": "verify-async-001",
        "paypal-transmission-sig": "ZmFrZQ==",
        "paypal-transmission-time": "2026-10-23T00:00:00Z",
        "paypal-cert-url": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-fake",
        "paypal-auth-algo": "SHA256withRSA",
    }


def main() -> int:
    client = TestClient(app)

    # 1) 设定一个允许自动通过的约束
    client.post(
        "/api/agent/constraints",
        json={
            "monthly_budget_usd": 1000,
            "single_tx_threshold_usd": 1000,
            "daily_threshold_usd": 1000,
            "vendor_whitelist": ["Notion"],
            "category_whitelist": [],
        },
    )

    # 2) 走结构化下单接口（不经 LLM，确定性更强），拿到订单与三层 Mandate
    r = client.post(
        "/api/agent/requests",
        json={"vendor": "Notion", "item": "Notion Pro", "amount_usd": 10.0, "currency": "USD"},
    )
    assert r.status_code == 200, f"下单失败: {r.status_code} {r.text}"
    body = r.json()
    order_id = body["payment_result"]["order_id"]
    trace_id = body["mandate"]["trace_id"]
    print(f"① 订单已创建：order_id={order_id}")
    print(f"   初始 Mandate 状态 = {body['mandate']['status']}（应为 APPROVED/PAYER_ACTION_REQUIRED 阶段）")

    # 3) 模拟 PayPal 回调 PAYMENT.CAPTURE.COMPLETED，携带我们的 order_id
    event = {
        "id": "WH-ASYNC-VERIFY-001",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-ASYNC-VERIFY-001",
            "supplementary_data": {"related_ids": {"order_id": order_id}},
        },
    }
    r2 = client.post("/api/webhook", json=event, headers=_dummy_sig_headers())
    print(f"② Webhook 投递：HTTP={r2.status_code} body={r2.json()}")

    # 4) 回查 Mandate，确认三层都被翻成 COMPLETED
    rows = store.list_mandate_layers(trace_id)
    assert rows, "未找到该交易的 Mandate 记录"
    statuses = {row["layer"]: row["status"] for row in rows}
    print(f"③ 回查三层 Mandate 状态：{statuses}")
    ok = all(s == "COMPLETED" for s in statuses.values())
    if ok:
        print("✅ 异步同步正确：Webhook 到达后，三层 Mandate 全部标记为 COMPLETED")
        return 0
    print("❌ 异步同步异常：存在非 COMPLETED 层", statuses)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
