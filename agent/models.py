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
    """用户给 Agent 设定的花钱约束（Intent Mandate 的载体）。"""

    monthly_budget_usd: float = Field(gt=0, description="月度预算上限，美元")
    single_tx_threshold_usd: float = Field(gt=0, description="单笔超过此金额需人工确认，美元")
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


class MandateRecord(BaseModel):
    """三层 Mandate 审计记录。

    一笔完整交易会落三条记录，分别对应三层授权：
    intent 记录「可以买什么」、cart 记录「这次买什么」、payment 记录「钱怎么扣」。
    这是评委最爱看的审计页面数据源。
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    intent: str = Field(description="Intent: 用户想买什么")
    cart: str = Field(description="Cart: 这次具体买了什么")
    payment: str = Field(description="Payment: 钱怎么扣")
    status: MandateStatus = MandateStatus.APPROVED
    decision: Decision | None = None  # 约束引擎给的原始结论，便于追溯
    order_id: str | None = None  # 真实支付成功后的 PayPal order id
    capture_id: str | None = None  # 捕获 id
    created_at: str = Field(default_factory=_now_iso)
