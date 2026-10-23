"""约束引擎：纯函数校验一笔采购请求是否符合用户约束。

设计原则：
- 无副作用、无 I/O，可独立单元测试；
- 返回结构化结果（decision + reason），供状态机路由与审计日志复用；
- 校验顺序固定：供应商白名单 → 品类白名单 → 月度滚动预算 → 单笔/日累计双阈值。

预算统计通过 `SpendContext` 传入（由调用方从 DB 聚合），保持本模块纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Constraint, Decision, PurchaseRequest


@dataclass
class SpendContext:
    """已花费统计（由调用方聚合后注入，本模块不碰 DB）。

    - monthly_usd：本月已完成支付累计（用于滚动预算判断）
    - daily_usd  ：今日已完成支付累计（用于日累计阈值判断）
    """

    monthly_usd: float = 0.0
    daily_usd: float = 0.0


class EvaluationResult:
    """校验结果：decision + 人类可读原因。"""

    def __init__(self, decision: Decision, reason: str):
        self.decision = decision
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"EvaluationResult({self.decision.value}, {self.reason!r})"


def evaluate(
    request: PurchaseRequest,
    constraint: Constraint | None,
    spend: SpendContext | None = None,
) -> EvaluationResult:
    """对一笔采购请求做约束校验。

    返回三态结论：
    - AUTO_APPROVE   全通过，Agent 可自动执行
    - REQUIRE_HUMAN  超过单笔/日累计阈值，需人工确认
    - REJECT         违反白名单或滚动预算，直接拒绝

    预算/阈值判断基于「已花费 + 本次请求」的合计：
    - 月度滚动预算（硬上限）：monthly_usd + amount > budget → REJECT
    - 日累计阈值（软上限）：daily_usd + amount > daily_threshold → REQUIRE_HUMAN
    - 单笔阈值（软上限）：amount > single_tx_threshold → REQUIRE_HUMAN
    """
    if constraint is None:
        return EvaluationResult(Decision.REJECT, "未设定约束，拒绝执行")

    spend = spend or SpendContext()

    # 1. 供应商白名单（硬上限）
    if constraint.vendor_whitelist and request.vendor not in constraint.vendor_whitelist:
        return EvaluationResult(
            Decision.REJECT,
            f"供应商「{request.vendor}」不在白名单 {constraint.vendor_whitelist}",
        )

    # 2. 品类白名单（硬上限）
    if (
        request.category
        and constraint.category_whitelist
        and request.category not in constraint.category_whitelist
    ):
        return EvaluationResult(
            Decision.REJECT,
            f"品类「{request.category}」不在白名单 {constraint.category_whitelist}",
        )

    # 3. 月度滚动预算（硬上限）：本月已花 + 本次 > 预算 → 拒
    if spend.monthly_usd + request.amount_usd > constraint.monthly_budget_usd:
        return EvaluationResult(
            Decision.REJECT,
            f"本月已花 ${spend.monthly_usd:.2f} + 本次 ${request.amount_usd:.2f} "
            f"超出月度预算 ${constraint.monthly_budget_usd:.2f}",
        )

    # 4. 单笔阈值（软上限）→ 需人工确认
    if request.amount_usd > constraint.single_tx_threshold_usd:
        return EvaluationResult(
            Decision.REQUIRE_HUMAN,
            f"${request.amount_usd:.2f} 超过单笔阈值 ${constraint.single_tx_threshold_usd:.2f}，需人工确认",
        )

    # 5. 日累计阈值（软上限）→ 需人工确认
    if constraint.daily_threshold_usd > 0 and spend.daily_usd + request.amount_usd > constraint.daily_threshold_usd:
        return EvaluationResult(
            Decision.REQUIRE_HUMAN,
            f"今日已花 ${spend.daily_usd:.2f} + 本次 ${request.amount_usd:.2f} "
            f"超过日累计阈值 ${constraint.daily_threshold_usd:.2f}，需人工确认",
        )

    return EvaluationResult(Decision.AUTO_APPROVE, "符合全部约束，可自动执行")
