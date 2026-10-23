"""约束引擎单元测试 —— D2 完成标志「单元测试通过」的落点。

覆盖三类约束：预算（monthly_budget_usd）、白名单（vendor / category）、阈值（single_tx_threshold_usd）。
外加校验顺序与边界值，保证状态机路由不会被边界样本打穿。

运行：python -m pytest tests/ -v
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.constraints import evaluate
from agent.models import Constraint, Decision, PurchaseRequest


@pytest.fixture
def constraint() -> Constraint:
    """基准约束：月预算 $200、单笔阈值 $50、供应商 Notion/Figma、品类 SaaS。"""
    return Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=["SaaS"],
    )


def req(vendor="Notion", item="Notion Pro", amount=10.0, category="SaaS") -> PurchaseRequest:
    return PurchaseRequest(vendor=vendor, item=item, amount_usd=amount, category=category)


# ---------------------------------------------------------------- 基线：自动通过

def test_auto_approve_within_all_limits(constraint):
    """小额 + 白名单内 → AUTO_APPROVE。"""
    r = evaluate(req(amount=10.0), constraint)
    assert r.decision == Decision.AUTO_APPROVE
    assert "符合全部约束" in r.reason


# ---------------------------------------------------------------- 阈值：需人工确认

def test_require_human_above_single_tx_threshold(constraint):
    """超过单笔阈值（$50）但未超月预算（$200）→ REQUIRE_HUMAN，不是 REJECT。"""
    r = evaluate(req(amount=80.0), constraint)
    assert r.decision == Decision.REQUIRE_HUMAN
    assert "需人工确认" in r.reason


def test_amount_exactly_at_threshold_is_auto_approve(constraint):
    """边界：金额 == 单笔阈值，判定为「未超过」→ AUTO_APPROVE。"""
    r = evaluate(req(amount=50.0), constraint)
    assert r.decision == Decision.AUTO_APPROVE


def test_amount_one_cent_above_threshold_is_require_human(constraint):
    """边界：阈值 + 0.01 → REQUIRE_HUMAN。"""
    r = evaluate(req(amount=50.01), constraint)
    assert r.decision == Decision.REQUIRE_HUMAN


# ---------------------------------------------------------------- 预算：直接拒绝

def test_reject_above_monthly_budget(constraint):
    """超过月度预算 → REJECT，即便供应商合法。"""
    r = evaluate(req(amount=250.0), constraint)
    assert r.decision == Decision.REJECT
    assert "超出月度预算" in r.reason


def test_amount_exactly_at_budget_passes(constraint):
    """边界：金额 == 月预算，判定为「未超出」→ 走阈值分支（$200 > $50 → REQUIRE_HUMAN）。"""
    r = evaluate(req(amount=200.0), constraint)
    assert r.decision == Decision.REQUIRE_HUMAN


# ---------------------------------------------------------------- 白名单：直接拒绝

def test_reject_vendor_not_in_whitelist(constraint):
    """供应商不在白名单 → REJECT（哪怕金额很小）。"""
    r = evaluate(req(vendor="Slack", amount=15.0), constraint)
    assert r.decision == Decision.REJECT
    assert "Slack" in r.reason and "不在白名单" in r.reason


def test_reject_category_not_in_whitelist(constraint):
    """品类不在白名单 → REJECT。"""
    r = evaluate(req(vendor="Notion", amount=10.0, category="Hardware"), constraint)
    assert r.decision == Decision.REJECT
    assert "Hardware" in r.reason


def test_empty_whitelist_means_no_restriction():
    """白名单为空 = 不限制该维度。"""
    c = Constraint(monthly_budget_usd=200.0, single_tx_threshold_usd=50.0)
    r = evaluate(req(vendor="任意供应商", amount=10.0, category="任意品类"), c)
    assert r.decision == Decision.AUTO_APPROVE


def test_missing_category_skips_category_check(constraint):
    """请求未带品类 → 跳过品类校验（不误杀）。"""
    r = evaluate(req(category=""), constraint)
    assert r.decision == Decision.AUTO_APPROVE


# ---------------------------------------------------------------- 校验顺序

def test_vendor_check_precedes_budget_check(constraint):
    """同时违反白名单与预算 → 优先报白名单（顺序固定：供应商 → 品类 → 预算 → 阈值）。"""
    r = evaluate(req(vendor="Slack", amount=999.0), constraint)
    assert r.decision == Decision.REJECT
    assert "不在白名单" in r.reason
    assert "预算" not in r.reason


def test_category_check_precedes_budget_check(constraint):
    """品类越界 + 超预算 → 优先报品类。"""
    r = evaluate(req(vendor="Notion", category="Hardware", amount=999.0), constraint)
    assert r.decision == Decision.REJECT
    assert "品类" in r.reason


# ---------------------------------------------------------------- 无约束

def test_reject_when_no_constraint_set():
    """未设定约束 → 拒绝执行（AI 不能在无授权时花钱）。"""
    r = evaluate(req(), None)
    assert r.decision == Decision.REJECT
    assert "未设定约束" in r.reason


# ---------------------------------------------------------------- 模型自身校验

def test_constraint_rejects_non_positive_budget():
    """月预算必须 > 0。"""
    with pytest.raises(ValidationError):
        Constraint(monthly_budget_usd=0, single_tx_threshold_usd=50.0)


def test_request_rejects_non_positive_amount():
    """金额必须 > 0。"""
    with pytest.raises(ValidationError):
        PurchaseRequest(vendor="Notion", item="X", amount_usd=0)


def test_decision_values_are_stable_strings():
    """Decision 是 str Enum，落库/JSON 序列化后取值稳定。"""
    assert Decision.AUTO_APPROVE == "AUTO_APPROVE"
    assert Decision.REQUIRE_HUMAN == "REQUIRE_HUMAN"
    assert Decision.REJECT == "REJECT"
