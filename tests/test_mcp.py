"""MCP 接入层的单元测试（全部离线，不碰网络）。

要保护的核心契约：

1. **花钱型工具必须在发起调用前被拦** —— 这是"MCP 不能替代 REST/Toolkit"的技术根据
2. **只读工具不被供应商白名单误伤** —— 约束引擎管的是钱，只读查询不涉及钱
3. **工具白名单是硬闸门** —— 未知工具一律拒绝
4. **MCP 客户端参数格式必须是 `--key=value`** —— 分离写法会让 server 崩
5. **工具短名 → 协议层函数名的映射** —— 传错会得到 -32602
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import mcp_client, mcp_guarded  # noqa: E402
from agent.constraints import SpendContext  # noqa: E402
from agent.models import Constraint  # noqa: E402


# ──────────────────────────────────────────────────────
# 1. 工具白名单 / 黑名单
# ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tool",
    ["orders.capture", "payments.createRefund", "subscriptions.cancel", "pay_order"],
)
def test_money_moving_tools_are_blocked(tool):
    """花钱型工具必须在**发起 MCP 调用之前**被拦下。"""
    allowed, reason = mcp_guarded.check_tool_allowed(tool)
    assert not allowed, f"{tool} 不该被放行"
    assert "动钱" in reason or "白名单" in reason


def test_read_only_tool_is_allowed():
    allowed, _ = mcp_guarded.check_tool_allowed("invoices.list")
    assert allowed


def test_unknown_tool_is_rejected():
    allowed, reason = mcp_guarded.check_tool_allowed("totally.made.up")
    assert not allowed
    assert "白名单" in reason


def test_blocked_list_covers_the_dangerous_ones():
    """必须覆盖官方 MCP 目录里所有会动钱的工具。

    官方 @paypal/mcp@1.8.1 的 28 个工具中，能改变资金状态的：
    orders.capture / payments.createRefund / subscriptions.cancel /
    subscriptions.create / subscriptionPlans.create / invoices.send /
    disputes.create / checkout_cart / pay_order
    """
    dangerous = {
        "orders.capture",
        "payments.createRefund",
        "subscriptions.cancel",
        "subscriptions.create",
        "subscriptionPlans.create",
        "invoices.send",
        "disputes.create",
        "checkout_cart",
        "pay_order",
    }
    assert dangerous <= set(mcp_guarded.BLOCKED_MONEY_MOVING), (
        f"未覆盖的危险工具：{dangerous - set(mcp_guarded.BLOCKED_MONEY_MOVING)}"
    )


def test_no_tool_is_both_allowed_and_blocked():
    """同一个工具不能既在白名单又在黑名单里（那等于没拦）。"""
    overlap = set(mcp_guarded.ALLOWED_TOOLS) & set(mcp_guarded.BLOCKED_MONEY_MOVING)
    assert not overlap, f"工具同时在允许与禁止清单：{overlap}"


# ──────────────────────────────────────────────────────
# 2. 只读工具的闸门语义
# ──────────────────────────────────────────────────────

@pytest.fixture
def fake_mcp(monkeypatch):
    """把 MCP 客户端换成桩 —— 测试绝不能起真实进程（实测 19 秒 vs 2 秒）。

    桩返回一段真实的 MCP 响应结构，顺带验证 payload 解析。
    """
    calls: list[dict] = []

    class _FakeClient:
        def __init__(self, tools=None, access_token=None, **kw):
            calls.append({"tools": tools, "token": access_token})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def call_tool(self, name, args=None, timeout=90.0):
            calls.append({"called": name, "args": args})
            return {
                "content": [
                    {
                        "type": "text",
                        "text": '{"total_items": 1, "items": [{"id": "INV2-MOCK"}]}',
                    }
                ]
            }

    monkeypatch.setattr(mcp_client, "PayPalMcpClient", _FakeClient)
    return calls


def test_readonly_tool_not_blocked_by_vendor_allowlist(fake_mcp):
    """只读查询不该被供应商白名单拦住。

    实测踩过：只读工具硬套 evaluate() → 占位供应商名不在白名单 → REJECT
    → 只读查询根本发不出去。约束引擎管的是「钱花给谁」，
    而查询不问钱给谁。
    """
    constraint = Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=[],
    )
    out = mcp_guarded.run_constrained_tool(
        "invoices.list",
        {"page_size": 1},
        constraint,
        SpendContext(monthly_usd=0.0, daily_usd=0.0),
        access_token="fake-token-not-used",
    )
    assert out.decision != "BLOCKED", out.reason
    assert out.decision != "REJECT", out.reason
    # 而且确实发出了 MCP 调用（用协议层的函数名）
    assert fake_mcp, "应发起 MCP 调用"
    assert fake_mcp[-1]["called"] == "list_invoices"


def test_readonly_tool_returns_read_only_decision(fake_mcp):
    constraint = Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=[],
    )
    out = mcp_guarded.run_constrained_tool(
        "invoices.list",
        {"page_size": 1},
        constraint,
        SpendContext(monthly_usd=0.0, daily_usd=0.0),
        access_token="fake",
    )
    assert out.decision == "READ_ONLY"
    assert out.result["total_items"] == 1


def test_blocked_tool_never_touches_the_network():
    """被拦的工具**不能**产生任何 MCP 调用（连进程都不该起）。"""
    constraint = Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=[],
    )
    out = mcp_guarded.run_constrained_tool(
        "orders.capture",
        {},
        constraint,
        SpendContext(monthly_usd=0.0, daily_usd=0.0),
        access_token="fake",
    )
    assert not out.allowed
    assert out.result is None
    assert out.error is None


# ──────────────────────────────────────────────────────
# 3. MCP 客户端协议细节
# ──────────────────────────────────────────────────────

def test_client_uses_equals_form_arguments():
    """必须用 `--key=value`，不能用 `--key value`。

    实测 @paypal/mcp@1.8.1 的 parseArgs：
        const [key, value] = arg.slice(2).split('=');
    分离写法 → value 是 undefined → `value.toLowerCase()` 抛
    "Cannot read properties of undefined (reading 'toLowerCase')"，
    报错完全看不出是参数格式问题。
    """
    c = mcp_client.PayPalMcpClient(tools=["invoices.list"], access_token="x")
    cmd = c._cmd()
    assert "--tools=invoices.list" in cmd, cmd
    assert "--paypal-environment=SANDBOX" in cmd, cmd
    # 不能有分离写法
    assert not any(a in ("--tools", "--paypal-environment", "--access-token") for a in cmd)


def test_tool_name_resolution():
    """CLI 短名 → MCP 协议层函数名。"""
    assert mcp_client.resolve_tool_name("invoices.list") == "list_invoices"
    assert mcp_client.resolve_tool_name("transactions.list") == "list_transactions"
    # 已经是完整名则原样返回
    assert mcp_client.resolve_tool_name("list_invoices") == "list_invoices"


def test_tool_name_mapping_matches_allowlist():
    """允许清单里的每个短名都必须有对应的协议层映射。"""
    for short in mcp_guarded.ALLOWED_TOOLS:
        assert short in mcp_client.TOOL_NAME_BY_SHORT, (
            f"{short} 缺少协议层函数名映射"
        )


def test_parse_tool_payload_unwraps_mcp_content():
    """MCP 返回 `{"content":[{"type":"text","text":"<json>"}]}`，要解出内层 JSON。"""
    raw = {
        "content": [
            {"type": "text", "text": '{"total_items": 1, "items": [{"id": "INV2-X"}]}'}
        ]
    }
    text = "".join(b["text"] for b in raw["content"])
    parsed = mcp_guarded.parse_tool_payload(text)
    assert parsed["total_items"] == 1
    assert parsed["items"][0]["id"] == "INV2-X"


def test_parse_tool_payload_handles_plain_text():
    assert mcp_guarded.parse_tool_payload("not json") == "not json"
    assert mcp_guarded.parse_tool_payload("") is None
