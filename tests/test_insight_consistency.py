"""洞察与判定的一致性守卫（W-14 · A3）。

为什么需要这个
--------------
实测过的演示隐患：LLM 给一张 **REJECT** 的单子写洞察时，句子里冒出"会通过"，
而页面同一行的判定徽章写着 REJECT —— 矛盾摆在一行里，评委一眼就能看见。

修法：LLM 文本回来后做一次**轻量**校验（只看结论词，不调 LLM、零延迟），
与判定矛盾就丢弃 LLM 文本、改用模板兜底（模板的 verdict 由判定映射而来，
与徽章永远一致）。

测试覆盖三种判定各自的矛盾回退，外加：
- 结论一致的文本**必须**保留（守卫不能变成"一律不用 AI"）；
- 否定式表达（"这笔不会通过"）**不能**被误判为矛盾。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import ai_insight  # noqa: E402
from agent.models import Constraint, PurchaseRequest  # noqa: E402

CONSTRAINT = Constraint(
    monthly_budget_usd=200.0,
    single_tx_threshold_usd=50.0,
    vendor_whitelist=["Notion", "Figma"],
)
REQUEST = PurchaseRequest(vendor="Slack", item="Slack Pro", amount_usd=15.0, category="SaaS")


@pytest.fixture
def llm_says(monkeypatch):
    """把 LLM 的回答固定成指定文本。"""
    def _set(text):
        monkeypatch.setattr(ai_insight, "_llm_text", lambda system, user: text)
        return text
    return _set


def _template_for(decision: str) -> str:
    """模板兜底里该出现的结论词（与徽章一致）。"""
    return {
        "AUTO_APPROVE": "会通过",
        "REQUIRE_HUMAN": "需你确认",
        "REJECT": "会被拦截",
    }[decision]


# ── 三种判定各自的矛盾回退 ────────────────────────────────────

def test_reject_insight_saying_will_pass_falls_back_to_template(llm_says):
    """REJECT + 文本说"会通过" → 丢弃 LLM 文本，改用模板。"""
    llm_says("本月预算充足，这笔 Slack Pro $15 会通过。")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "REJECT"
    )
    assert from_llm is False, "与判定矛盾的文本不能当作 AI 洞察"
    assert "会被拦截" in text, "模板里的结论词必须与徽章一致"
    assert "会通过" not in text.replace("会被拦截", "")


def test_auto_approve_insight_saying_will_be_blocked_falls_back(llm_says):
    """AUTO_APPROVE + 文本说"会被拦截" → 回退模板。"""
    llm_says("Slack 不在白名单，这笔 $15 会被拦截，需要人工处理。")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "AUTO_APPROVE"
    )
    assert from_llm is False
    assert "会通过" in text


def test_require_human_insight_saying_will_pass_falls_back(llm_says):
    """REQUIRE_HUMAN + 文本说"会通过" → 回退模板（需你确认）。"""
    llm_says("本月已花 $25，这笔 $15 会通过。")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "REQUIRE_HUMAN"
    )
    assert from_llm is False
    assert "需你确认" in text


def test_require_human_insight_saying_will_be_blocked_falls_back(llm_says):
    """REQUIRE_HUMAN + 文本说"会被拦截" → 回退模板。"""
    llm_says("这笔 $15 会被拦截，因为超过了阈值。")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "REQUIRE_HUMAN"
    )
    assert from_llm is False
    assert "需你确认" in text


# ── 守卫不能误伤 ──────────────────────────────────────────────

def test_consistent_insight_is_kept(llm_says):
    """结论一致的文本必须保留 —— 守卫不是"一律不用 AI"。"""
    llm_says("本月已花 $25（预算 $200），这笔 $15 会被拦截，余额 $175。")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "REJECT"
    )
    assert from_llm is True
    assert text.startswith("本月已花 $25")


def test_negated_phrase_is_not_treated_as_contradiction(llm_says):
    """否定式表达（"不会通过"）不算矛盾。"""
    llm_says("Slack 不在白名单，这笔不会通过，需要你调整白名单。")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "REJECT"
    )
    assert from_llm is True, "否定句被误判成矛盾了"
    assert "不会通过" in text


def test_english_contradiction_is_caught(llm_says):
    """英文结论词同样要拦（"approved" / "rejected"）。"""
    llm_says("Budget is fine, this purchase will be approved.")
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "REJECT"
    )
    assert from_llm is False
    assert "会被拦截" in text


def test_graph_path_uses_the_same_guard(llm_says):
    """图节点走的 generate_insight 也要受守卫保护（演示走的就是这条）。"""
    llm_says("这笔会通过，没问题。")
    text = ai_insight.generate_insight(REQUEST, CONSTRAINT, 25.0, "REJECT")
    assert "会被拦截" in text, "图路径的洞察必须与徽章一致"


def test_no_llm_answer_uses_template(llm_says):
    """LLM 不可用 → 模板（from_llm=False）。"""
    llm_says(None)
    text, from_llm = ai_insight.generate_insight_with_source(
        REQUEST, CONSTRAINT, 25.0, "AUTO_APPROVE"
    )
    assert from_llm is False
    assert _template_for("AUTO_APPROVE") in text
