"""W-12：人工枚举攻击 fixture × 引擎真实判定（回归护栏）。

为什么要有这组 fixture
------------------------
`scripts/stress_constraints.py` 的随机压测给的是**覆盖率**，但随机样本不能
评审、不能复现、也不能在评审现场当场演示「这个攻击被挡住了」。

这组 fixture 是另一件事：**5 类攻击 × 4 例，expected decision 人工写死**
（先读 `agent/constraints.py` 的规格写期望，再跑引擎对账，不是跑完回填）。
它有两个作用：

1. **回归护栏** —— 以后谁动了约束引擎的判定顺序或阈值语义，这里立刻红；
2. **评审材料** —— 每条用例都写清了「攻击者想干什么」和「引擎必须给出什么结论」。

fixture 里还如实记了一条**已知敞口**（SPLIT-04：用户没设日累计阈值时，
拆单可以绕过单笔软上限）—— 装看不见的测试不是好测试。

全程离线：只调用纯函数 `evaluate()`，不联网、不调 LLM、不碰 PayPal。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.constraints import SpendContext, evaluate
from agent.models import Constraint, Decision, PurchaseRequest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "attack_cases.json"

# 期望的 5 类攻击 —— 少一类说明 fixture 覆盖被改薄了，这条会红
EXPECTED_CLASSES = {
    "拆单绕过单笔上限",
    "多笔累加绕过月预算",
    "未知品类绕过白名单",
    "别名大小写绕过 vendor",
    "退款后额度回滚",
}


def _load() -> dict:
    with FIXTURE.open(encoding="utf-8") as f:
        return json.load(f)


DATA = _load()
CASES = DATA["cases"]


def test_fixture_file_exists_and_is_not_empty():
    assert FIXTURE.exists(), f"缺少人工枚举 fixture: {FIXTURE}"
    assert len(CASES) >= 15, f"fixture 只有 {len(CASES)} 条，覆盖太薄"


def test_fixture_covers_five_attack_classes_with_three_to_four_cases_each():
    by_class: dict[str, int] = {}
    for c in CASES:
        by_class[c["attack_class"]] = by_class.get(c["attack_class"], 0) + 1
    assert set(by_class) == EXPECTED_CLASSES, f"攻击类别不符：{sorted(by_class)}"
    for cls, n in by_class.items():
        assert 3 <= n <= 4, f"{cls} 有 {n} 例，应为 3-4 例"


def test_fixture_expected_values_are_hand_written():
    """期望必须是写死的字面量，不能是引擎算出来的（防止"回填期望"）。"""
    for c in CASES:
        exp = c["expected"]
        assert exp["decision"] in {d.value for d in Decision}
        assert isinstance(exp["reason_contains"], str) and exp["reason_contains"]
        assert "why" in c and c["why"], f"{c['id']} 缺少攻击意图说明"
        assert "title" in c and "title_en" in c


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_engine_matches_hand_written_expectation(case):
    """引擎判定必须等于人工写死的期望（判定 + 理由片段）。"""
    constraint = Constraint(**case["constraint"])
    request = PurchaseRequest(**case["request"])
    spend = SpendContext(**(case.get("spend") or {}))

    result = evaluate(request, constraint, spend=spend)

    expected = case["expected"]
    assert result.decision.value == expected["decision"], (
        f"{case['id']} 期望 {expected['decision']}，实际 {result.decision.value}"
        f"｜引擎理由: {result.reason}｜场景: {case.get('title', '')}"
    )
    assert expected["reason_contains"] in result.reason, (
        f"{case['id']} 理由不含期望片段 {expected['reason_contains']!r}"
        f"｜实际理由: {result.reason}"
    )
