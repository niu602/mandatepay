"""约束引擎：纯函数校验一笔采购请求是否符合用户约束。

设计原则：
- 无副作用、无 I/O，可独立单元测试；
- 返回结构化结果（decision + reason），供状态机路由与审计日志复用；
- 校验顺序固定：供应商白名单 → 品类白名单 → 月度预算 → 单笔阈值。
"""
from __future__ import annotations

from .models import Constraint, Decision, PurchaseRequest


class EvaluationResult:
    """校验结果：decision + 人类可读原因。"""

    def __init__(self, decision: Decision, reason: str):
        self.decision = decision
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"EvaluationResult({self.decision.value}, {self.reason!r})"


def evaluate(request: PurchaseRequest, constraint: Constraint | None) -> EvaluationResult:
    """对一笔采购请求做约束校验。

    返回三态结论：
    - AUTO_APPROVE   全通过，Agent 可自动执行
    - REQUIRE_HUMAN  超过单笔阈值，需人工确认
    - REJECT         违反白名单或预算，直接拒绝
    """
    if constraint is None:
        return EvaluationResult(Decision.REJECT, "未设定约束，拒绝执行")

    # 1. 供应商白名单
    if constraint.vendor_whitelist and request.vendor not in constraint.vendor_whitelist:
        return EvaluationResult(
            Decision.REJECT,
            f"供应商「{request.vendor}」不在白名单 {constraint.vendor_whitelist}",
        )

    # 2. 品类白名单
    if (
        request.category
        and constraint.category_whitelist
        and request.category not in constraint.category_whitelist
    ):
        return EvaluationResult(
            Decision.REJECT,
            f"品类「{request.category}」不在白名单 {constraint.category_whitelist}",
        )

    # 3. 月度预算上限
    if request.amount_usd > constraint.monthly_budget_usd:
        return EvaluationResult(
            Decision.REJECT,
            f"${request.amount_usd:.2f} 超出月度预算 ${constraint.monthly_budget_usd:.2f}",
        )

    # 4. 单笔阈值 → 需人工确认
    if request.amount_usd > constraint.single_tx_threshold_usd:
        return EvaluationResult(
            Decision.REQUIRE_HUMAN,
            f"${request.amount_usd:.2f} 超过单笔阈值 ${constraint.single_tx_threshold_usd:.2f}，需人工确认",
        )

    return EvaluationResult(Decision.AUTO_APPROVE, "符合全部约束，可自动执行")
