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


def _norm(s: str) -> str:
    """归一化名称：去首尾空格 + 转小写。

    用户手写「notion」、从别处粘贴「 Notion 」都应命中白名单里的「Notion」，
    否则用户会看到莫名其妙的误拒（压测实测类 A）。
    """
    return (s or "").strip().lower()


def _in_list(value: str, whitelist: list[str]) -> bool:
    """名称是否命中白名单（大小写/空格不敏感）。"""
    return _norm(value) in {_norm(w) for w in whitelist}


class EvaluationResult:
    """校验结果：decision + 人类可读原因。"""

    def __init__(self, decision: Decision, reason: str):
        self.decision = decision
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"EvaluationResult({self.decision.value}, {self.reason!r})"


def _msg(lang: str, zh: str, en: str) -> str:
    """按界面语言选引擎理由。

    默认中文 —— 所有既有调用点、测试与审计数据里的理由文案保持原样；
    只有前端显式传 ``lang="en"`` 时才切英文（W-15 三态切换的 en 模式）。
    """
    return en if lang == "en" else zh


def evaluate(
    request: PurchaseRequest,
    constraint: Constraint | None,
    spend: SpendContext | None = None,
    lang: str = "zh",
) -> EvaluationResult:
    """对一笔采购请求做约束校验。

    返回三态结论：
    - AUTO_APPROVE   全通过，Agent 可自动执行
    - REQUIRE_HUMAN  超过单笔/日累计阈值，或品类无法校验，需人工确认
    - REJECT         违反白名单或滚动预算，直接拒绝

    预算/阈值判断基于「已花费 + 本次请求」的合计：
    - 月度滚动预算（硬上限）：monthly_usd + amount > budget → REJECT
    - 日累计阈值（软上限）：daily_usd + amount > daily_threshold → REQUIRE_HUMAN
    - 单笔阈值（软上限）：amount > single_tx_threshold → REQUIRE_HUMAN

    金额一律按「分」取整后再比较：0.1+0.2 这类浮点尾数不应把恰好等于阈值的
    金额误判成超限（压测实测类 H）。
    """
    if constraint is None:
        return EvaluationResult(
            Decision.REJECT,
            _msg(lang, "未设定约束，拒绝执行", "No constraints configured — purchase rejected"),
        )

    spend = spend or SpendContext()
    amount = round(request.amount_usd, 2)  # 按分取整，消除浮点尾数

    # 1. 供应商白名单（硬上限，大小写/空格不敏感）
    if constraint.vendor_whitelist and not _in_list(request.vendor, constraint.vendor_whitelist):
        return EvaluationResult(
            Decision.REJECT,
            _msg(
                lang,
                f"供应商「{request.vendor}」不在白名单 {constraint.vendor_whitelist}",
                f"Vendor '{request.vendor}' is not in the allowlist {constraint.vendor_whitelist}",
            ),
        )

    # 2. 品类白名单（硬上限）；请求没带品类时留到软上限处理（不静默放行）
    if (
        request.category
        and constraint.category_whitelist
        and not _in_list(request.category, constraint.category_whitelist)
    ):
        return EvaluationResult(
            Decision.REJECT,
            _msg(
                lang,
                f"品类「{request.category}」不在白名单 {constraint.category_whitelist}",
                f"Category '{request.category}' is not in the allowlist {constraint.category_whitelist}",
            ),
        )

    # 3. 月度滚动预算（硬上限）：本月已花 + 本次 > 预算 → 拒
    if spend.monthly_usd + amount > constraint.monthly_budget_usd:
        return EvaluationResult(
            Decision.REJECT,
            _msg(
                lang,
                f"本月已花 ${spend.monthly_usd:.2f} + 本次 ${amount:.2f} "
                f"超出月度预算 ${constraint.monthly_budget_usd:.2f}",
                f"Spent ${spend.monthly_usd:.2f} this month + ${amount:.2f} now "
                f"exceeds the ${constraint.monthly_budget_usd:.2f} monthly budget",
            ),
        )

    # 4. 单笔阈值（软上限）→ 需人工确认
    if amount > constraint.single_tx_threshold_usd:
        return EvaluationResult(
            Decision.REQUIRE_HUMAN,
            _msg(
                lang,
                f"${amount:.2f} 超过单笔阈值 ${constraint.single_tx_threshold_usd:.2f}，需人工确认",
                f"${amount:.2f} exceeds the ${constraint.single_tx_threshold_usd:.2f} "
                f"per-transaction threshold — human confirmation required",
            ),
        )

    # 5. 日累计阈值（软上限）→ 需人工确认
    if constraint.daily_threshold_usd > 0 and spend.daily_usd + amount > constraint.daily_threshold_usd:
        return EvaluationResult(
            Decision.REQUIRE_HUMAN,
            _msg(
                lang,
                f"今日已花 ${spend.daily_usd:.2f} + 本次 ${amount:.2f} "
                f"超过日累计阈值 ${constraint.daily_threshold_usd:.2f}，需人工确认",
                f"Spent ${spend.daily_usd:.2f} today + ${amount:.2f} now exceeds the "
                f"${constraint.daily_threshold_usd:.2f} daily threshold — human confirmation required",
            ),
        )

    # 6. 品类未知 + 约束限定了品类 → 无法校验品类白名单。
    #    既不能静默放行（那等于没约束），也不宜直接拒绝（品类字段常常天然缺失，
    #    硬拒会误伤正常采购），折中为「需人工确认」。
    if constraint.category_whitelist and not request.category:
        return EvaluationResult(
            Decision.REQUIRE_HUMAN,
            _msg(
                lang,
                f"请求未指明品类，无法校验品类白名单 {constraint.category_whitelist}，需人工确认",
                f"Category missing — cannot verify against the allowlist "
                f"{constraint.category_whitelist}; human confirmation required",
            ),
        )

    return EvaluationResult(
        Decision.AUTO_APPROVE,
        _msg(lang, "符合全部约束，可自动执行", "All constraints satisfied — auto-approved"),
    )
