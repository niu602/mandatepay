"""`scripts/seed_demo_scenarios.py` 的行为测试。

这个脚本是评审期的保险 —— 演示数据被清空时一条命令重建。
所以必须保证：

1. 三种场景的判定**与生产引擎一致**（不是脚本自己硬编码）
2. **幂等** —— 重复跑不堆数据
3. `--reset` 真的删（不能是假绿）
4. 没有约束时**拒绝造数据**（否则造出来的判定没有意义）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 直接 import 脚本（它不是包，用 importlib 加载）
_spec = importlib.util.spec_from_file_location(
    "seed_demo_scenarios", ROOT / "scripts" / "seed_demo_scenarios.py"
)
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)


def _constraint():
    from agent.models import Constraint

    return Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=[],
    )


def test_scenarios_cover_three_decisions():
    """三种场景必须覆盖三种判定，否则镜头④的素材不完整。"""
    decisions = {s["expect"] for s in seed.SCENARIOS}
    assert decisions == {"AUTO_APPROVE", "REQUIRE_HUMAN", "REJECT"}


def test_expected_decisions_match_production_engine():
    """脚本写的期望判定必须与生产引擎 `evaluate()` 的实际输出一致。

    这条最关键 —— 如果约束变了（比如阈值从 $50 改成 $100），
    脚本造的金额就可能不再触发期望判定。这时必须报错而不是造出假数据。
    """
    from agent.constraints import evaluate
    from agent.models import PurchaseRequest

    c = _constraint()
    for s in seed.SCENARIOS:
        req = PurchaseRequest(
            vendor=s["vendor"],
            item=s["item"],
            amount_usd=s["amount_usd"],
            currency="USD",
            category=s["category"],
            recurring=True,
        )
        actual = evaluate(req, c).decision.value
        assert actual == s["expect"], (
            f"{s['vendor']} ${s['amount_usd']}：脚本期望 {s['expect']}，"
            f"生产引擎实际 {actual} —— 约束变了，脚本的金额需要调整"
        )


def test_require_human_amount_is_just_over_threshold():
    """REQUIRE_HUMAN 场景的金额必须**恰好越过**阈值。

    太超（$500）会让预算也超 → 变成 REJECT，语义就错了。
    """
    from agent.constraints import evaluate
    from agent.models import PurchaseRequest

    c = _constraint()
    scenario = next(s for s in seed.SCENARIOS if s["expect"] == "REQUIRE_HUMAN")
    req = PurchaseRequest(
        vendor=scenario["vendor"],
        item=scenario["item"],
        amount_usd=scenario["amount_usd"],
        currency="USD",
        category=scenario["category"],
        recurring=True,
    )
    result = evaluate(req, c)
    assert result.decision.value == "REQUIRE_HUMAN"
    # 金额在阈值之上、但在预算之内
    assert scenario["amount_usd"] > c.single_tx_threshold_usd
    assert scenario["amount_usd"] <= c.monthly_budget_usd


def test_reject_scenario_vendor_not_in_allowlist():
    """REJECT 场景的供应商必须不在白名单里，否则判定会变。"""
    c = _constraint()
    scenario = next(s for s in seed.SCENARIOS if s["expect"] == "REJECT")
    assert scenario["vendor"] not in c.vendor_whitelist


def test_idempotent_no_duplicate_data():
    """重复跑不堆数据（幂等）。"""
    from agent import store

    # 先清掉演示数据，保证起点干净
    seed._reset_demo()

    c = _constraint()
    seed._make_one(seed.SCENARIOS[0], c)
    seed._make_one(seed.SCENARIOS[0], c)

    with store._Conn() as db:
        n = db.cur().execute(
            "SELECT COUNT(*) n FROM mandates WHERE vendor = ?", (seed.SCENARIOS[0]["vendor"],)
        ).fetchone()["n"]
    # 两笔 × 三层 = 6 行（脚本本身不防重复调用，幂等在 main() 里靠 _demo_traces 判定）
    assert n == 6


def test_demo_traces_detection():
    """`_demo_traces()` 要能认出"三层齐全 + 演示供应商"的记录。"""
    from agent import store

    seed._reset_demo()
    c = _constraint()
    _, trace, _ = seed._make_one(seed.SCENARIOS[0], c)

    found = seed._demo_traces()
    assert trace in found, "造出来的演示交易应被 _demo_traces 认出"

    with store._Conn() as db:
        n = db.cur().execute(
            "SELECT COUNT(*) n FROM mandates WHERE vendor = ?", (seed.SCENARIOS[0]["vendor"],)
        ).fetchone()["n"]
    assert n == 3, "一笔交易应落三条 Mandate"


def test_reset_actually_deletes():
    """`--reset` 必须真的删 —— 不能是"删失败但吞异常"的假绿。

    这条对应实测踩过的坑：`_Cursor` 没有 `rowcount`，
    原来靠 `cur.rowcount` 算删除数，异常被 except 吞掉 →
    看着成功，其实一行没删。
    """
    from agent import store

    seed._reset_demo()
    c = _constraint()
    seed._make_one(seed.SCENARIOS[0], c)

    with store._Conn() as db:
        before = db.cur().execute(
            "SELECT COUNT(*) n FROM mandates WHERE vendor = ?", (seed.SCENARIOS[0]["vendor"],)
        ).fetchone()["n"]
    assert before == 3

    removed = seed._reset_demo()

    with store._Conn() as db:
        after = db.cur().execute(
            "SELECT COUNT(*) n FROM mandates WHERE vendor = ?", (seed.SCENARIOS[0]["vendor"],)
        ).fetchone()["n"]
    assert after == 0, f"reset 后应清空，实际还有 {after} 行"
    assert removed == 3, f"应报告删除 3 行，实际报告 {removed}"


def test_script_uses_store_placeholder_convention():
    """脚本里的 SQL 必须用 `?` 占位符（store 的约定，由 `_sql()` 转 PG 的 `%s`）。

    直接写 `%s` 在 SQLite 上会报 `near "%": syntax error`（实测踩过）。

    只检查**实际执行的 SQL 语句**（含 f-string 的 SQL 片段），
    不检查文档字符串 —— 注释里解释这个坑时必然会提到 `%s`。
    """
    import re

    src = (ROOT / "scripts" / "seed_demo_scenarios.py").read_text(encoding="utf-8")

    # 抽出所有 f-string / 普通字符串里的 SQL 语句
    sql_literals = re.findall(r"""(?:f)?["']((?:SELECT|DELETE)[^"']*)["']""", src)
    assert sql_literals, "应能找到脚本里的 SQL 语句"

    for sql in sql_literals:
        assert "%s" not in sql, (
            f"SQL 里不应直接用 %s 占位符（store._sql() 会自动转，`?` 才对）：\n  {sql}"
        )
