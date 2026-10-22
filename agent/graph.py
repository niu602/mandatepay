"""MandatePay — LangGraph 状态机（真实 PayPal Agent Toolkit 版本）。

五节点：parse → validate → approve → pay → verify
- pay_node 对 AUTO_APPROVE 调用 PayPal Toolkit create_order 创建真实订单
  （沙箱买家需去 approval_url 登录付款，之后由 /api/capture-order/{id} 捕获）
- REQUIRE_HUMAN 同样创建真实订单，但状态保持 PENDING_HUMAN，等待用户人工确认
- REJECT 不创建订单
"""
from __future__ import annotations

import operator
import os
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph

from .constraints import Decision, evaluate
from .intent import Intent, parse
from .models import Constraint, MandateRecord, MandateStatus, PurchaseRequest, _now_iso
from .paypal_tools import create_order as paypal_create_order
from . import store


# ── State ──────────────────────────────────────────────
class AgentState(TypedDict, total=False):
    constraint: Constraint | None
    request: PurchaseRequest | None
    messages: Annotated[list, operator.add]
    decision: str
    mandate: MandateRecord | None
    payment_result: dict | None
    error: str | None


# ── 节点 ───────────────────────────────────────────────
def parse_node(state: AgentState) -> dict:
    """节点 1：解析自然语言 → 约束 / 采购请求，并写库。"""
    msgs = state.get("messages") or []
    user_msg = msgs[-1].content if msgs else ""
    parsed = parse(user_msg)

    if parsed.intent is Intent.SET_CONSTRAINT:
        store.save_constraint(parsed.constraint, _now_iso())
        return {"constraint": parsed.constraint, "decision": "PARSED_CONSTRAINT"}
    if parsed.intent is Intent.PURCHASE_REQUEST:
        store.save_request(parsed.request, _now_iso())
        return {"request": parsed.request, "decision": "PARSED_REQUEST"}
    return {"error": parsed.note}


def validate_node(state: AgentState) -> dict:
    """节点 2：约束校验。"""
    req = state.get("request")
    if req is None:
        return {"error": "没有采购请求可校验"}
    result = evaluate(req, state.get("constraint"))
    return {"decision": result.decision.value, "error": None if result.decision is not Decision.REJECT else result.reason}


def approve_node(state: AgentState) -> dict:
    """节点 3：审批 —— 生成三层 Mandate 审计记录。"""
    req = state["request"]
    decision = state["decision"]

    status = {
        "AUTO_APPROVE": MandateStatus.APPROVED,
        "REQUIRE_HUMAN": MandateStatus.PENDING_HUMAN,
        "REJECT": MandateStatus.REJECTED,
    }.get(decision, MandateStatus.REJECTED)

    mandate = MandateRecord(
        intent=f"采购 {req.item}（供应商 {req.vendor}）",
        cart=f"{req.item} × ${req.amount_usd:.2f} ({req.currency})",
        payment=f"PayPal Sandbox · {'自动扣款' if decision == 'AUTO_APPROVE' else ('人工确认后扣款' if decision == 'REQUIRE_HUMAN' else '未扣款')}",
        status=status,
        decision=Decision(decision) if decision in Decision._value2member_map_ else None,
    )
    store.save_mandate(mandate)
    return {"mandate": mandate}


def pay_node(state: AgentState) -> dict:
    """节点 4：执行支付。

    - AUTO_APPROVE: 通过 PayPal Agent Toolkit 创建真实沙箱订单
    - REQUIRE_HUMAN: 同样创建真实订单，但等待买家批准/人工确认
    - REJECT: 不会进入此节点（路由直接 END）
    """
    if state.get("decision") == "REJECT":
        return {"payment_result": {"status": "SKIPPED", "reason": "被约束引擎拒绝"}}

    req = state["request"]
    try:
        result = paypal_create_order(req.item, req.amount_usd, quantity=1)
    except Exception as exc:
        return {"error": f"PayPal 创建订单失败: {exc}", "payment_result": {"status": "FAILED"}}

    payment_result = {
        "order_id": result["order_id"],
        "amount": req.amount_usd,
        "currency": req.currency,
        "status": result["status"],  # PAYER_ACTION_REQUIRED
        "approval_url": result["approval_url"],
    }
    return {"payment_result": payment_result}


def verify_node(state: AgentState) -> dict:
    """节点 5：验证支付结果，更新 Mandate 状态。

    真实沙箱订单创建后状态是 PAYER_ACTION_REQUIRED，买家需登录付款；
    所以这里只把「已捕获」的订单标为 COMPLETED，其余保持原状态。
    """
    mandate = state.get("mandate")
    if mandate is None:
        return {"error": "没有 mandate 记录"}

    payment = state.get("payment_result") or {}
    order_id = payment.get("order_id")
    if payment.get("status") == "COMPLETED":
        updated = mandate.model_copy(
            update={"status": MandateStatus.COMPLETED, "order_id": order_id}
        )
        store.update_mandate_status(mandate.id, MandateStatus.COMPLETED.value, order_id=order_id)
        return {"mandate": updated}
    # PAYER_ACTION_REQUIRED / PENDING_HUMAN / REJECT：保持原状态，仅回写 order_id
    if order_id:
        store.update_mandate_status(mandate.id, mandate.status.value, order_id=order_id)
    return {"mandate": mandate.model_copy(update={"order_id": order_id})}


# ── 路由 ───────────────────────────────────────────────
def route_after_parse(state: AgentState) -> str:
    d = state.get("decision")
    if d == "PARSED_REQUEST":
        return "validate"
    return END


def route_after_validate(state: AgentState) -> str:
    return "approve"


def route_after_approve(state: AgentState) -> str:
    if state.get("decision") == "REJECT":
        return END
    return "pay"


# ── 构建 Graph ─────────────────────────────────────────
_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ("agent.models", "Constraint"),
        ("agent.models", "PurchaseRequest"),
        ("agent.models", "MandateStatus"),
        ("agent.models", "Decision"),
        ("agent.models", "MandateRecord"),
    ]
)


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("parse", parse_node)
    builder.add_node("validate", validate_node)
    builder.add_node("approve", approve_node)
    builder.add_node("pay", pay_node)
    builder.add_node("verify", verify_node)

    builder.set_entry_point("parse")
    builder.add_conditional_edges("parse", route_after_parse, {"validate": "validate", END: END})
    builder.add_conditional_edges("validate", route_after_validate, {"approve": "approve"})
    builder.add_conditional_edges("approve", route_after_approve, {"pay": "pay", END: END})
    builder.add_edge("pay", "verify")
    builder.add_edge("verify", END)

    return builder.compile(checkpointer=MemorySaver(serde=_SERDE))


graph = build_graph()
