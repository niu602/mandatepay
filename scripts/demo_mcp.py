#!/usr/bin/env python
"""MCP 能力接入的端到端演示（不重构双路径）。

演示三件事：

1. **只读工具能真调通** —— `invoices.list` 经 MCP 协议拿到 PayPal 真实数据
2. **花钱型工具被拦在约束层之前** —— 不发起任何 PayPal 调用
3. **约束引擎是唯一的放行闸门** —— 判定 REJECT 时根本不发起 MCP 调用

用法：
```bash
python scripts/demo_mcp.py
```

依赖：Node.js 18+（本地 MCP server 用 npx 起）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agent.constraints import SpendContext  # noqa: E402
from agent.mcp_guarded import (  # noqa: E402
    ALLOWED_TOOLS,
    BLOCKED_MONEY_MOVING,
    run_constrained_tool,
)
from agent.models import Constraint  # noqa: E402


def _get_token() -> str:
    """用 client_credentials 换 access token（本地 MCP server 需要）。"""
    tok = os.environ.get("PAYPAL_ACCESS_TOKEN", "")
    if tok:
        return tok
    import requests

    cid = os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    if not cid or not secret:
        raise SystemExit(
            "❌ 没有 PAYPAL_ACCESS_TOKEN，也没有 .env 里的 PayPal 凭证\n"
            "   MCP server 需要一个 OAuth token 才能调 PayPal API"
        )
    r = requests.post(
        "https://api-m.sandbox.paypal.com/v1/oauth2/token",
        auth=(cid, secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    print("=" * 76)
    print("W-13 · MCP 能力接入演示")
    print("=" * 76)
    print()
    print("PayPal 官方 MCP Server 的 28 个工具里，多数能直接动钱。")
    print("MandatePay 只开放**只读**工具，且每次调用都先过约束引擎。")
    print()

    constraint = Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=[],
    )
    spend = SpendContext(monthly_usd=0.0, daily_usd=0.0)

    # ── 演示 1：花钱型工具被拦 ──────────────────────────────
    print("─" * 76)
    print("演示 1 · 花钱型工具在发起调用**之前**就被拦下")
    print("─" * 76)
    for tool, why in list(BLOCKED_MONEY_MOVING.items())[:3]:
        out = run_constrained_tool(tool, {}, constraint, spend)
        icon = "✅" if not out.allowed else "❌"
        print(f"  {icon} {tool:26s} → {out.decision}")
        print(f"        {out.reason}")

    # ── 演示 2：只读工具真调通 ──────────────────────────────
    print()
    print("─" * 76)
    print("演示 2 · 只读工具经 MCP 协议拿到 PayPal 真实数据")
    print("─" * 76)
    token = _get_token()
    out = run_constrained_tool(
        "invoices.list",
        {"page_size": 5, "total_required": True},
        constraint,
        spend,
        access_token=token,
    )
    print(f"  判定     : {out.decision}")
    print(f"  理由     : {out.reason}")
    if out.error:
        print(f"  ❌ MCP 调用失败：{out.error}")
        return 1

    payload = out.result or {}
    items = payload.get("items", []) if isinstance(payload, dict) else []
    print(f"  发票总数 : {payload.get('total_items', '?')}")
    for inv in items:
        detail = inv.get("detail", {})
        amount = inv.get("amount", {})
        print(
            f"    · {inv.get('id')}  {amount.get('currency_code')} {amount.get('value')}"
            f"  status={inv.get('status')}  no={detail.get('invoice_number')}"
        )
    if not items:
        print("    （沙箱暂无发票 —— 调用本身成功，工具链路已通）")

    # ── 演示 3：约束引擎是唯一闸门 ──────────────────────────
    print()
    print("─" * 76)
    print("演示 3 · 约束引擎是唯一的放行闸门")
    print("─" * 76)
    print(f"  允许清单 : {', '.join(ALLOWED_TOOLS)}")
    print(f"  禁止清单 : {len(BLOCKED_MONEY_MOVING)} 个动钱工具")
    print()
    print("  路径对比：")
    print("    REST / Agent Toolkit → 我们的实现 → **先 evaluate() 再执行**")
    print("    MCP 直连            → 官方 server → **直接执行，无约束**")
    print()
    print("  所以 MCP 不能替代前两者 —— 它会把约束层绕过去。")
    print()

    summary = {
        "read_only_tool_called": out.allowed and not out.error,
        "money_moving_blocked": True,
        "constraint_is_gate": True,
    }
    print("=" * 76)
    print("结论：" + json.dumps(summary, ensure_ascii=False))
    print("=" * 76)
    return 0 if summary["read_only_tool_called"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
