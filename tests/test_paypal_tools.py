"""PayPal Agent Toolkit 封装的解析层测试。

重点锁住两类**真实踩过的坑**：
1. Toolkit 的 `capture_order` 返回**包装结构** `{message, status, amount, raw}`，
   真实捕获数据在 `raw` 里 —— 早期版本直接读顶层 `purchase_units`，
   导致 capture_id 恒为 None，三层 Mandate 审计链丢失捕获 id。
2. Toolkit 的 `create_order` 入参 schema **强制要求** `items[].item_total`
   （删掉会报 Field required），但它内部只用 `item_cost` 重算总额 ——
   传的值必须与它的重算结果一致，否则两边数字打架。

运行：python -m pytest tests/ -v
"""
from __future__ import annotations

import json

import pytest

from agent import paypal_tools


class _FakeTool:
    """替换 Toolkit 工具，注入预设返回值。"""

    def __init__(self, raw):
        self._raw = raw
        self.calls: list[dict] = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self._raw


@pytest.fixture
def fake_tool(monkeypatch):
    def _install(raw):
        tool = _FakeTool(raw)
        monkeypatch.setattr(paypal_tools, "_get_tool", lambda name: tool)
        return tool
    return _install


# ---------------------------------------------------------------- capture_order

def test_capture_order_reads_capture_id_from_wrapped_payload(fake_tool):
    """Toolkit 包装结构：真实 captures 在 raw 里，capture_id 必须能取到。"""
    fake_tool(json.dumps({
        "message": "The PayPal order 8CN99 has been successfully captured.",
        "status": "COMPLETED",
        "amount": "USD 10.00",
        "raw": {
            "id": "8CN99",
            "status": "COMPLETED",
            "purchase_units": [{
                "payments": {"captures": [{
                    "id": "CAP-REAL-12345",
                    "amount": {"value": "10.00", "currency_code": "USD"},
                }]}
            }],
        },
    }))

    res = paypal_tools.capture_order("8CN99")
    assert res["status"] == "COMPLETED"
    assert res["capture_id"] == "CAP-REAL-12345", "包装结构下 capture_id 不能丢"
    assert res["amount"] == "USD 10.00"


def test_capture_order_still_supports_unwrapped_payload(fake_tool):
    """兼容未包装的原始结构（万一 Toolkit 改了返回形态也不能崩）。"""
    fake_tool(json.dumps({
        "id": "8CN99",
        "status": "COMPLETED",
        "purchase_units": [{
            "payments": {"captures": [{"id": "CAP-PLAIN-1"}]}
        }],
    }))

    res = paypal_tools.capture_order("8CN99")
    assert res["status"] == "COMPLETED"
    assert res["capture_id"] == "CAP-PLAIN-1"


def test_capture_order_raises_clear_error_on_empty_response(fake_tool):
    """订单未 approve 时 Toolkit 返回空串 → 给出可操作的报错，不是 JSON 解析错误。"""
    fake_tool("")
    with pytest.raises(RuntimeError, match="尚未被买家 approve"):
        paypal_tools.capture_order("8CN99")


# ---------------------------------------------------------------- create_order

def test_create_order_item_total_matches_toolkit_recomputation(fake_tool):
    """传入的 item_total 必须等于 Toolkit 内部按 item_cost×quantity 重算的值。"""
    tool = fake_tool(json.dumps({
        "id": "ORDER-1",
        "status": "PAYER_ACTION_REQUIRED",
        "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-1"}],
    }))

    paypal_tools.create_order("Notion Plus", 10.0, quantity=1)
    item = tool.calls[0]["items"][0]
    assert item["item_cost"] == 10.0
    # Toolkit 的 parse_order_details 只读 item_cost 并按 ×quantity 重算
    assert item["item_total"] == round(item["item_cost"] * item["quantity"], 2)


def test_create_order_keeps_required_item_total_field(fake_tool):
    """item_total 是 schema 必填项，不能省 —— 之前误删导致 Field required。"""
    tool = fake_tool(json.dumps({"id": "ORDER-2", "status": "PAYER_ACTION_REQUIRED", "links": []}))

    paypal_tools.create_order("X", 7.5)
    item = tool.calls[0]["items"][0]
    assert "item_total" in item
    assert "item_cost" in item
    assert item["quantity"] == 1


def test_create_order_extracts_approval_url(fake_tool):
    """approval_url 从 links[rel=payer-action] 里取。"""
    fake_tool(json.dumps({
        "id": "ORDER-3",
        "status": "PAYER_ACTION_REQUIRED",
        "links": [
            {"rel": "self", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORDER-3"},
            {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-3"},
        ],
    }))

    res = paypal_tools.create_order("X", 5.0)
    assert res["order_id"] == "ORDER-3"
    assert res["approval_url"] == "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-3"


# ------------------------------------------------- return_url / cancel_url

def test_create_order_sends_our_return_url_not_toolkit_placeholder(fake_tool, monkeypatch):
    """批准后必须回跳**本站**，不能落到 Toolkit 的 example.com 占位域。

    真实踩过的坑：`paypal_agent_toolkit/shared/orders/parameters.py` 里
    `return_url` 的默认值被写死成 `https://example.com/returnUrl`
    （且 validate_default=True），我们不传就用那个值 —— 买家付完款被 302 到
    IANA 的文档示例域名，页面上只有「本域名仅用于文档示例」的声明。
    """
    tool = fake_tool(json.dumps({"id": "ORDER-4", "status": "PAYER_ACTION_REQUIRED", "links": []}))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example.org/")  # 末尾斜杠要吃掉

    paypal_tools.create_order("X", 5.0)
    payload = tool.calls[0]
    assert payload["return_url"] == "https://demo.example.org/paypal/return"
    assert payload["cancel_url"] == "https://demo.example.org/paypal/cancel"
    assert "example.com/returnUrl" not in payload["return_url"]


def test_create_order_falls_back_to_localhost_without_public_base(fake_tool, monkeypatch):
    """没配 PUBLIC_BASE_URL、也不在任何托管平台上时，退回本机地址。"""
    tool = fake_tool(json.dumps({"id": "ORDER-5", "status": "PAYER_ACTION_REQUIRED", "links": []}))
    for var in ("PUBLIC_BASE_URL", "WEBSITE_HOSTNAME", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(var, raising=False)

    paypal_tools.create_order("X", 5.0)
    assert tool.calls[0]["return_url"] == "http://127.0.0.1:8888/paypal/return"


def test_create_order_uses_azure_injected_hostname(fake_tool, monkeypatch):
    """忘配 PUBLIC_BASE_URL 时，Azure 自动注入的 WEBSITE_HOSTNAME 兜住。

    这条测试锁的是「部署后没配环境变量就把买家甩到 127.0.0.1」这个更隐蔽的坑：
    Azure App Service 会注入 WEBSITE_HOSTNAME，直接拿来拼 https 即可。
    """
    tool = fake_tool(json.dumps({"id": "ORDER-6", "status": "PAYER_ACTION_REQUIRED", "links": []}))
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("WEBSITE_HOSTNAME", "mandatepay-zisen.azurewebsites.net")

    paypal_tools.create_order("X", 5.0)
    assert tool.calls[0]["return_url"] == "https://mandatepay-zisen.azurewebsites.net/paypal/return"
    assert tool.calls[0]["cancel_url"] == "https://mandatepay-zisen.azurewebsites.net/paypal/cancel"
