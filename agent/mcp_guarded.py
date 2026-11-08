"""MCP 能力接入 MandatePay 的约束层。

为什么不能让 Agent 直接调 MCP 工具
----------------------------------
PayPal 官方 MCP Server 暴露 28 个工具，其中**大量能直接动钱**：

    payments.createRefund   退款（钱回来）
    subscriptions.cancel    取消订阅（停止付费）
    orders.capture          捕获已授权订单（钱出去）
    disputes.create         创建争议
    invoices.send           发出发票（催款）

如果 Agent 直接拿到这些工具，**约束引擎就被绕过了** ——
它会变成"AI 自己决定花钱多少"，那正是 MandatePay 要解决的问题。

所以本项目的做法是：

1. **只开只读工具**（`READ_ONLY_TOOLS`）—— 不花钱、不改状态
2. 每个 MCP 工具调用都**先过约束引擎**再执行
3. 花钱型工具即使将来开放，也必须走 `evaluate()` 判定

这就是"MCP 不能替代 REST / Agent Toolkit"的**技术根据**，
而不是一句"功能重叠"的空话 —— 见 README 的对照表。

安全边界（本模块的硬约束）
--------------------------
- 只允许 `ALLOWED_TOOLS` 白名单里的工具
- 只读工具清单本身也是白名单（新增工具需显式加进来）
- 每个调用都要带 `PurchaseRequest` 走 `evaluate()`，
  判定为 REJECT 时**不发起 MCP 调用**
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from agent.constraints import SpendContext, evaluate  # noqa: E402
from agent.models import Constraint, PurchaseRequest  # noqa: E402

# ── 工具白名单 ────────────────────────────────────────────
# 短名（CLI `--tools=` 用）→ 允许的 MCP 工具函数名
ALLOWED_TOOLS: dict[str, str] = {
    "invoices.list": "list_invoices",
}

# 明确禁止的工具（花钱 / 改状态）—— 记录下来是为了让"为什么不开"有据可查，
# 也防止有人顺手把它们加进白名单。
BLOCKED_MONEY_MOVING: dict[str, str] = {
    "orders.capture": "captures an authorised order — moves money out",
    "payments.createRefund": "refunds a captured payment — moves money back",
    "subscriptions.cancel": "cancels a subscription — stops future payments",
    "subscriptions.create": "creates a subscription — commits future spend",
    "subscriptionPlans.create": "creates a subscription plan — commits future spend",
    "invoices.send": "sends an invoice to a customer — creates a collection obligation",
    "disputes.create": "creates a dispute — affects funds",
    "checkout_cart": "finalises a cart and processes payment",
    "pay_order": "captures payment for an authorised order",
}

# 只读工具的「副作用」标记 —— 用于自检与文档
READ_ONLY = {"invoices.list"}


@dataclass
class McpCallOutcome:
    """一次受约束的 MCP 调用结果。"""

    allowed: bool
    decision: str
    reason: str
    tool: str
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "reason": self.reason,
            "tool": self.tool,
            "result": self.result,
            "error": self.error,
        }


def parse_tool_payload(text: str) -> Any:
    """MCP 工具返回的是 `{"content":[{"type":"text","text":"<json>"}]}`。

    这里把内层 JSON 解出来 —— 官方 server 把真实结果塞在 text 字段里（是 JSON 字符串）。
    """
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict) and "content" in data:
        parts = []
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        joined = "\n".join(parts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined
    return data


def check_tool_allowed(short_name: str) -> tuple[bool, str]:
    """检查工具是否在白名单里。返回 (允许, 原因)。"""
    if short_name in BLOCKED_MONEY_MOVING:
        return False, (
            f"工具 {short_name} 会动钱（{BLOCKED_MONEY_MOVING[short_name]}），"
            "必须先过约束引擎 —— 不允许从 MCP 直连"
        )
    if short_name not in ALLOWED_TOOLS:
        return False, (
            f"工具 {short_name} 不在白名单里。"
            f"当前允许：{', '.join(ALLOWED_TOOLS) or '（空）'}"
        )
    return True, "在白名单内"


def build_request_for_tool(short_name: str, args: dict) -> PurchaseRequest | None:
    """把工具参数映射成一个「采购请求」，用于走约束判定。

    只读查询类工具**不花钱**，所以用一个金额为 0 的请求 ——
    它只会过白名单与品类校验，不会被预算/阈值拦下，
    但仍然保证「工具在允许清单里」这条规则被统一执行。
    """
    if short_name == "invoices.list":
        # 发票查询不涉及金额，但 PurchaseRequest.amount_usd 有 pydantic 约束
        # `gt=0`（不能为 0）—— 所以用最小可表示值 0.01 元。
        # 它远低于任何阈值，只会让判定走「通过」分支，不影响只读查询。
        return PurchaseRequest(
            vendor="__mcp_readonly__",
            item=f"MCP read-only query: {short_name}",
            amount_usd=0.01,
            currency="USD",
            category="",
        )
    return None


def run_constrained_tool(
    short_name: str,
    args: dict,
    constraint: Constraint | None,
    spend: SpendContext | None = None,
    access_token: str | None = None,
) -> McpCallOutcome:
    """执行一次**受约束的** MCP 工具调用。

    顺序：白名单 → 约束判定 → 发起 MCP 调用。
    任何一步不过就不发起调用（不消耗 PayPal 配额，也不产生副作用）。
    """
    allowed, reason = check_tool_allowed(short_name)
    if not allowed:
        return McpCallOutcome(
            allowed=False, decision="BLOCKED", reason=reason, tool=short_name
        )

    # ── 闸门 ──────────────────────────────────────────────
    # 只读工具**不花钱** → 不走 evaluate()。
    #
    # 为什么：约束引擎校验的是「钱花给谁、花多少、够不够」，
    # 而只读查询一个都不涉及。硬套 evaluate() 会得到荒谬结果 ——
    # 实测踩过：用占位供应商名走一遍，被供应商白名单判 REJECT，
    # 结果只读查询根本发不出去。
    #
    # 所以只读工具的闸门是**工具白名单**（上面已查），
    # 花钱型工具的闸门才是 evaluate()。
    #
    # 这也正是「MCP 不能替代 REST / Agent Toolkit」的根据：
    # 前两者的每一次动钱都过约束引擎，MCP 直连不过。
    if short_name in READ_ONLY:
        verdict = None
        req = build_request_for_tool(short_name, args)
    else:
        req = build_request_for_tool(short_name, args)
        if req is None:
            return McpCallOutcome(
                allowed=False,
                decision="BLOCKED",
                reason=f"工具 {short_name} 没有对应的约束映射（拒绝放行）",
                tool=short_name,
            )
        verdict = evaluate(req, constraint, spend=spend)
        if verdict.decision.value == "REJECT":
            return McpCallOutcome(
                allowed=False,
                decision=verdict.decision.value,
                reason=f"约束引擎拒绝：{verdict.reason}",
                tool=short_name,
            )

    # 过了判定 → 真调 MCP
    from agent.mcp_client import PayPalMcpClient, resolve_tool_name

    try:
        with PayPalMcpClient(
            tools=[short_name], access_token=access_token
        ) as mcp:
            raw = mcp.call_tool(resolve_tool_name(short_name), args)
    except Exception as exc:  # noqa: BLE001
        return McpCallOutcome(
            allowed=True,
            decision=verdict.decision.value if verdict else "READ_ONLY",
            reason=f"约束通过，但 MCP 调用失败：{type(exc).__name__}: {exc}",
            tool=short_name,
            error=str(exc),
        )

    return McpCallOutcome(
        allowed=True,
        decision=verdict.decision.value if verdict else "READ_ONLY",
        reason=(
            f"约束判定 {verdict.decision.value}（{verdict.reason}）"
            if verdict
            else "只读工具，不涉及资金 —— 已过工具白名单"
        ),
        tool=short_name,
        result=parse_tool_payload(
            "".join(
                b.get("text", "")
                for b in raw.get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
        ),
    )
