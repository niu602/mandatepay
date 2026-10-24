"""MandatePay 领域模型。

三层 Mandate 是项目的杀手锏：
- Intent Mandate  —— 用户授权 Agent「可以买什么」（品类/供应商白名单、月度预算）
- Cart Mandate    —— 用户确认「这次具体买什么」（商品明细、锁定价格）
- Payment Mandate —— 用户授权「钱怎么扣」（付款方式、单笔限额、是否需二次确认）

这里定义的 Constraint / PurchaseRequest / MandateRecord 是约束引擎、
状态机、审计日志共用的单一数据源。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Constraint(BaseModel):
    """用户给 Agent 设定的花钱约束（Intent Mandate 的载体）。

    两类上限：
    - 硬上限（违反直接 REJECT）：月度滚动预算、品类/供应商白名单
    - 软上限（超过需人工确认 REQUIRE_HUMAN）：单笔阈值、日累计阈值
    """

    monthly_budget_usd: float = Field(gt=0, description="月度滚动预算上限，美元（统计当月已完成的花费）")
    single_tx_threshold_usd: float = Field(gt=0, description="单笔超过此金额需人工确认，美元")
    daily_threshold_usd: float = Field(default=0.0, ge=0, description="日累计阈值，美元；0 表示不限制（超过则需人工确认）")
    vendor_whitelist: list[str] = Field(default_factory=list, description="供应商名称白名单")
    category_whitelist: list[str] = Field(default_factory=list, description="品类白名单")


class PurchaseRequest(BaseModel):
    """Agent 收到的一笔采购请求（Cart Mandate 的载体）。"""

    vendor: str = Field(description="供应商名称")
    item: str = Field(description="商品/服务名")
    amount_usd: float = Field(gt=0, description="金额，美元")
    currency: str = "USD"
    category: str = ""  # 品类，用于品类白名单校验
    recurring: bool = False  # 是否订阅制


class Decision(str, Enum):
    """约束校验的三种结论。"""

    AUTO_APPROVE = "AUTO_APPROVE"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    REJECT = "REJECT"


class MandateStatus(str, Enum):
    APPROVED = "APPROVED"
    PENDING_HUMAN = "PENDING_HUMAN"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


class MandateLayer(str, Enum):
    """三层 Mandate 的层级标识 —— 一笔交易落三条记录，各占一层。"""

    INTENT = "intent"  # 用户授权「可以买什么」
    CART = "cart"  # 用户确认「这次具体买什么」
    PAYMENT = "payment"  # 用户授权「钱怎么扣」


class MandateRecord(BaseModel):
    """单条 Mandate 审计记录（三层之一）。

    一笔完整交易会落**三条**记录，分别对应 Intent / Cart / Payment 三层授权，
    用同一个 `trace_id` 串联。这是 D5 的完成标志，也是评委最爱看的审计页数据源。

    `intent` / `cart` / `payment` 三个字段是三层内容的完整快照，三条记录各自冗余保存，
    目的是让旧查询（SELECT *）与前端渲染无需感知分层。
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="交易级关联 id，三层共享")
    layer: MandateLayer = MandateLayer.INTENT
    content: str = Field(default="", description="本层授权内容")
    intent: str = Field(default="", description="Intent: 用户想买什么")
    cart: str = Field(default="", description="Cart: 这次具体买了什么")
    payment: str = Field(default="", description="Payment: 钱怎么扣")
    status: MandateStatus = MandateStatus.APPROVED
    decision: Decision | None = None  # 约束引擎给的原始结论，便于追溯
    order_id: str | None = None  # 真实支付成功后的 PayPal order id
    capture_id: str | None = None  # 捕获 id
    amount_usd: float = 0.0  # 本笔交易金额，供月度/日累计预算统计
    vendor: str = ""  # 供应商，供预算仪表盘按供应商聚合
    created_at: str = Field(default_factory=_now_iso)


def build_mandate_trio(
    intent: str,
    cart: str,
    payment: str,
    status: MandateStatus = MandateStatus.APPROVED,
    decision: Decision | None = None,
    trace_id: str | None = None,
    amount_usd: float = 0.0,
    vendor: str = "",
) -> list[MandateRecord]:
    """按三层授权构造三条 Mandate 记录（共享同一 trace_id）。

    拆成三条而不是一条记录塞三个字段，是为了让每层可独立追溯状态与时间戳 ——
    这正是 MandatePay「三层授权」叙事的技术落点。
    """
    trace = trace_id or str(uuid.uuid4())
    return [
        MandateRecord(
            trace_id=trace, layer=MandateLayer.INTENT, content=intent,
            intent=intent, cart=cart, payment=payment, status=status, decision=decision,
            amount_usd=amount_usd, vendor=vendor,
        ),
        MandateRecord(
            trace_id=trace, layer=MandateLayer.CART, content=cart,
            intent=intent, cart=cart, payment=payment, status=status, decision=decision,
            amount_usd=amount_usd, vendor=vendor,
        ),
        MandateRecord(
            trace_id=trace, layer=MandateLayer.PAYMENT, content=payment,
            intent=intent, cart=cart, payment=payment, status=status, decision=decision,
            amount_usd=amount_usd, vendor=vendor,
        ),
    ]
