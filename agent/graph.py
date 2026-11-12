"""MandatePay — LangGraph 状态机（真实 PayPal Agent Toolkit 版本）。

七节点：parse → validate → insight → explain → approve → pay → verify
- pay_node 对 AUTO_APPROVE 调用 PayPal Toolkit create_order 创建真实订单
  （沙箱买家需去 approval_url 登录付款，之后由 /api/capture-order/{id} 捕获）
- REQUIRE_HUMAN 同样创建真实订单，但状态保持 PENDING_HUMAN，等待用户人工确认
- REJECT 不创建订单
- insight_node / explain_node 是本周新增的两个 **AI 决策参与点**：
  每笔采购都会先用 LLM 生成「消费洞察」，再生成「决策解释+建议」，
  让 AI 不只在入口解析一句话，而是参与每一次决策。
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
from .models import (
    Constraint,
    MandateRecord,
    MandateStatus,
    PurchaseRequest,
    build_mandate_trio,
    _now_iso,
)
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
    # 本周新增：AI 洞察/解释 + 供其使用的花费快照
    spend: dict          # {"monthly_usd": float, "daily_usd": float}
    reason: str | None   # 引擎判定原因（供 explain 使用）
    insight: str         # 消费洞察一句话
    explanation: str     # 决策解释 + 可操作建议
    lang: str            # 界面语言 both/en/zh —— 决定 reason 与 AI 文案的语言（W-15）
    # AI 推理轨迹（Demo 展示"AI 怎么想的"）
    reasoning: str       # 模型的自然语言推理
    tool_trace: list[str]  # 工具调用轨迹
    parse_note: str      # 解析方式说明（LLM / 规则降级）


# ── 节点 ───────────────────────────────────────────────
def parse_node(state: AgentState) -> dict:
    """节点 1：解析自然语言 → 约束 / 采购请求，并写库。

    模型可能一次同时给出「更新约束 + 下单」（如"预算看着办"这类表述），
    两者都要落库，采购请求优先驱动后续流程。
    """
    msgs = state.get("messages") or []
    user_msg = msgs[-1].content if msgs else ""
    parsed = parse(user_msg)

    out: dict = {
        "reasoning": parsed.reasoning,
        "tool_trace": list(parsed.trace),
        "parse_note": parsed.note,
    }

    if parsed.constraint is not None:
        store.save_constraint(parsed.constraint, _now_iso())
        out["constraint"] = parsed.constraint

    if parsed.intent is Intent.SET_CONSTRAINT:
        out["decision"] = "PARSED_CONSTRAINT"
        return out
    if parsed.intent is Intent.PURCHASE_REQUEST:
        store.save_request(parsed.request, _now_iso())
        out["request"] = parsed.request
        out["decision"] = "PARSED_REQUEST"
        return out
    out["error"] = parsed.note
    return out


def validate_node(state: AgentState) -> dict:
    """节点 2：约束校验。

    约束的唯一真相源是数据库（store.load_latest_constraint）。
    不能只依赖 state["constraint"] —— 那是 LangGraph 检查点在「同一会话内」
    先发过 SET_CONSTRAINT 时才有的，换会话 / 用独立接口设约束 / 服务重启后
    都会丢，导致「未设定约束」误拒。DB 兜底保证任何来源设的约束都生效。

    滚动预算/日累计阈值需要「已花费」统计，由 store 聚合后注入 SpendContext，
    保持约束引擎纯函数（无 I/O）。
    """
    from agent.constraints import SpendContext

    req = state.get("request")
    if req is None:
        return {"error": "没有采购请求可校验"}
    constraint = state.get("constraint") or store.load_latest_constraint()

    now = _now_iso()
    month_start = now[:7] + "-01T00:00:00+00:00"  # 本月 1 号 00:00 UTC
    day_start = now[:10] + "T00:00:00+00:00"       # 今天 00:00 UTC
    # 预算口径用「已承诺支出」（下单即计入），不是「已到账」——
    # 否则 Agent 能连下多笔待支付订单把预算绕过（见 store.sum_spent_amount 注释）。
    spend = SpendContext(
        monthly_usd=store.sum_spent_amount(month_start),
        daily_usd=store.sum_spent_amount(day_start),
    )

    result = evaluate(req, constraint, spend=spend, lang=state.get("lang") or "zh")
    return {
        "decision": result.decision.value,
        "error": None if result.decision is not Decision.REJECT else result.reason,
        "reason": result.reason,
        "constraint": constraint,
        "spend": {"monthly_usd": spend.monthly_usd, "daily_usd": spend.daily_usd},
    }


def insight_node(state: AgentState) -> dict:
    """节点 3（AI）：消费洞察 —— 每笔采购前先让 LLM 讲清财务处境。"""
    from .ai_insight import generate_insight

    req = state.get("request")
    if req is None:
        return {}
    spend = state.get("spend") or {}
    text = generate_insight(
        req,
        state.get("constraint"),
        float(spend.get("monthly_usd", 0.0)),
        state.get("decision", ""),
        state.get("lang") or "zh",
    )
    return {"insight": text}


def explain_node(state: AgentState) -> dict:
    """节点 4（AI）：决策解释 —— 把判定翻译成可行动建议（尤其擅长"为什么被拒"）。"""
    from .ai_insight import generate_explanation

    req = state.get("request")
    if req is None:
        return {}
    spend = state.get("spend") or {}
    text = generate_explanation(
        req,
        state.get("constraint"),
        state.get("decision", ""),
        state.get("reason"),
        float(spend.get("monthly_usd", 0.0)),
        state.get("lang") or "zh",
    )
    return {"explanation": text}


def approve_node(state: AgentState) -> dict:
    """节点 3：审批 —— 生成三层 Mandate 审计记录。"""
    req = state["request"]
    decision = state["decision"]

    status = {
        "AUTO_APPROVE": MandateStatus.APPROVED,
        "REQUIRE_HUMAN": MandateStatus.PENDING_HUMAN,
        "REJECT": MandateStatus.REJECTED,
    }.get(decision, MandateStatus.REJECTED)

    # 一笔交易落三条：Intent / Cart / Payment 各一条，共享 trace_id
    trio = build_mandate_trio(
        intent=f"采购 {req.item}（供应商 {req.vendor}）",
        cart=f"{req.item} × ${req.amount_usd:.2f} ({req.currency})",
        amount_usd=req.amount_usd,
        vendor=req.vendor,
        payment=(
            "PayPal Sandbox · "
            + (
                "自动扣款"
                if decision == "AUTO_APPROVE"
                else ("人工确认后扣款" if decision == "REQUIRE_HUMAN" else "未扣款")
            )
        ),
        status=status,
        decision=Decision(decision) if decision in Decision._value2member_map_ else None,
    )
    store.save_mandates(trio)
    out: dict = {"mandate": trio[-1]}  # 以 Payment 层作代表行
    if decision == "REJECT":
        # 必须清掉上一轮 checkpoint 残留的 payment_result，否则 API 会返回
        # 「decision=REJECT 却带着上一笔订单」的误导性结果（MemorySaver 会保留旧 state）。
        out["payment_result"] = {"status": "SKIPPED", "reason": "被约束引擎拒绝，未创建订单"}
    return out


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
    return "insight"


def route_after_insight(state: AgentState) -> str:
    return "explain"


def route_after_explain(state: AgentState) -> str:
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
    builder.add_node("insight", insight_node)
    builder.add_node("explain", explain_node)
    builder.add_node("approve", approve_node)
    builder.add_node("pay", pay_node)
    builder.add_node("verify", verify_node)

    builder.set_entry_point("parse")
    builder.add_conditional_edges("parse", route_after_parse, {"validate": "validate", END: END})
    builder.add_conditional_edges("validate", route_after_validate, {"insight": "insight"})
    builder.add_conditional_edges("insight", route_after_insight, {"explain": "explain"})
    builder.add_conditional_edges("explain", route_after_explain, {"approve": "approve"})
    builder.add_conditional_edges("approve", route_after_approve, {"pay": "pay", END: END})
    builder.add_edge("pay", "verify")
    builder.add_edge("verify", END)

    return builder.compile(checkpointer=MemorySaver(serde=_SERDE))


graph = build_graph()
