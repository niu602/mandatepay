"""W-15 · AI 推理链条的语言一致性。

截图缺陷：界面切到 en 模式后，UI 标签是英文，但
Insight / Explanation / Engine reason 三处内容仍是中文 —— 后端不知道界面语言。

修复口径（重要）：
- `lang` 默认 ``"zh"``，既有调用点、测试、审计数据里的中文文案**零改动**
- 只有显式 ``lang="en"`` 才切英文
- LLM 被要求写英文却仍吐中文时，语言守卫丢弃它并回退英文模板
  （沿用 W-14「模板不冒充 AI 输出」的 from_llm 约定）
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent import ai_insight
from agent.constraints import SpendContext, evaluate
from agent.models import Constraint, Decision, PurchaseRequest

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _constraint() -> Constraint:
    return Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        daily_threshold_usd=100.0,
        vendor_whitelist=["Notion", "Figma"],
        category_whitelist=["SaaS"],
    )


def _req(amount: float = 10.0, vendor: str = "Notion") -> PurchaseRequest:
    return PurchaseRequest(vendor=vendor, item="Notion Pro", amount_usd=amount, category="SaaS")


# ── 1. 引擎 reason ──────────────────────────────────────────────────

def test_evaluate_en_reason_is_english_for_all_verdicts():
    """三态 + 未设约束，en 模式的 reason 都不能夹汉字。"""
    cases = [
        (_req(10.0), _constraint(), Decision.AUTO_APPROVE, "All constraints"),
        (_req(80.0), _constraint(), Decision.REQUIRE_HUMAN, "per-transaction"),
        (_req(10.0), None, Decision.REJECT, "No constraints"),
        (_req(10.0, vendor="Slack"), _constraint(), Decision.REJECT, "allowlist"),
    ]
    for req, con, expected_decision, keyword in cases:
        r = evaluate(req, con, spend=SpendContext(), lang="en")
        assert r.decision is expected_decision
        assert not _CJK.search(r.reason), f"en 模式 reason 夹了中文：{r.reason!r}"
        assert keyword.lower() in r.reason.lower(), r.reason


def test_evaluate_default_lang_stays_chinese():
    """默认行为不变 —— 既有测试与审计数据都依赖中文 reason。"""
    r = evaluate(_req(), _constraint())
    assert r.decision is Decision.AUTO_APPROVE
    assert "符合全部约束" in r.reason
    r2 = evaluate(_req(), _constraint(), lang="both")
    assert "符合全部约束" in r2.reason


def test_evaluate_en_budget_and_daily_reasons_are_english():
    r = evaluate(_req(210.0), _constraint(), lang="en")
    assert r.decision is Decision.REJECT
    assert "monthly budget" in r.reason
    assert not _CJK.search(r.reason)

    over_daily = evaluate(
        _req(60.0), _constraint(), spend=SpendContext(daily_usd=95.0), lang="en"
    )
    assert over_daily.decision is Decision.REQUIRE_HUMAN
    assert not _CJK.search(over_daily.reason), over_daily.reason


# ── 2. 消费洞察 ────────────────────────────────────────────────────

def test_insight_en_template_is_english(monkeypatch):
    monkeypatch.setattr(ai_insight, "_llm_text", lambda *a, **kw: None)
    text, from_llm = ai_insight.generate_insight_with_source(
        _req(), _constraint(), 165.0, "AUTO_APPROVE", lang="en"
    )
    assert from_llm is False
    assert not _CJK.search(text), text
    assert "$165" in text and "will pass" in text


def test_insight_en_discards_chinese_llm_output(monkeypatch):
    """截图缺陷的核心回归：LLM 又吐中文时，en 模式必须回退英文模板。"""
    monkeypatch.setattr(
        ai_insight, "_llm_text",
        lambda *a, **kw: "余额 $25.00，本笔 $10.00 请求将导致总支出 $175.00，通过。",
    )
    text, from_llm = ai_insight.generate_insight_with_source(
        _req(), _constraint(), 165.0, "AUTO_APPROVE", lang="en"
    )
    assert from_llm is False, "语言不一致的 LLM 输出不能冒充 AI 洞察"
    assert not _CJK.search(text), text
    assert "will pass" in text


def test_insight_en_accepts_english_llm_output(monkeypatch):
    monkeypatch.setattr(
        ai_insight, "_llm_text",
        lambda *a, **kw: "Spent $165 of $200 this month; this $10 purchase passes.",
    )
    text, from_llm = ai_insight.generate_insight_with_source(
        _req(), _constraint(), 165.0, "AUTO_APPROVE", lang="en"
    )
    assert from_llm is True
    assert text.startswith("Spent $165")


def test_insight_default_lang_still_accepts_chinese(monkeypatch):
    """zh / both 模式保持原样：中文 LLM 输出应被接受。"""
    zh = "本月已花 $165/$200，这笔 $10 会通过。"
    monkeypatch.setattr(ai_insight, "_llm_text", lambda *a, **kw: zh)
    text, from_llm = ai_insight.generate_insight_with_source(
        _req(), _constraint(), 165.0, "AUTO_APPROVE"
    )
    assert from_llm is True and text == zh


# ── 3. 决策解释 ────────────────────────────────────────────────────

def test_explanation_en_template_is_english(monkeypatch):
    monkeypatch.setattr(ai_insight, "_llm_text", lambda *a, **kw: None)
    text, from_llm = ai_insight.generate_explanation_with_source(
        _req(), _constraint(), "AUTO_APPROVE", "All constraints satisfied", 165.0, lang="en"
    )
    assert from_llm is False
    assert not _CJK.search(text), text
    assert "All constraints satisfied" in text


def test_explanation_en_discards_chinese_llm_output(monkeypatch):
    monkeypatch.setattr(
        ai_insight, "_llm_text",
        lambda *a, **kw: "结论：自动通过。原因：未超预算且未超单笔阈值。建议：无需操作。",
    )
    text, from_llm = ai_insight.generate_explanation_with_source(
        _req(), _constraint(), "AUTO_APPROVE", "All constraints satisfied", 165.0, lang="en"
    )
    assert from_llm is False
    assert not _CJK.search(text), text


def test_explanation_en_accepts_english_llm_output(monkeypatch):
    monkeypatch.setattr(
        ai_insight, "_llm_text",
        lambda *a, **kw: "Approved: all constraints are satisfied and no action is needed.",
    )
    text, from_llm = ai_insight.generate_explanation_with_source(
        _req(), _constraint(), "AUTO_APPROVE", "All constraints satisfied", 165.0, lang="en"
    )
    assert from_llm is True and "Approved" in text


def test_explanation_en_reject_template_has_no_chinese(monkeypatch):
    monkeypatch.setattr(ai_insight, "_llm_text", lambda *a, **kw: None)
    text, _ = ai_insight.generate_explanation_with_source(
        _req(), _constraint(), "REJECT", "Vendor 'Slack' is not in the allowlist", 165.0, lang="en"
    )
    assert "Rejected" in text and not _CJK.search(text)


# ── 4. 端点接线 ────────────────────────────────────────────────────

def test_parse_endpoint_forwards_lang_to_graph(browser_client, monkeypatch):
    """/api/agent/parse 必须把 lang 塞进 LangGraph 初始 state。"""
    import agent.graph as g

    captured = {}

    def fake_invoke(state, config):
        captured.update(state)
        return {"decision": "PARSED_CONSTRAINT"}

    monkeypatch.setattr(g.graph, "invoke", fake_invoke)
    browser_client.get("/")
    r = browser_client.post(
        "/api/agent/parse", json={"text": "budget 200 dollars", "lang": "en"}
    )
    assert r.status_code == 200, r.text
    assert captured.get("lang") == "en"


def test_requests_endpoint_en_reason_is_english(browser_client, monkeypatch):
    """结构化快速路径也要跟随 lang（不能只修自然语言路径）。"""
    import agent.paypal_tools as pt
    from agent import store
    from agent.models import _now_iso

    monkeypatch.setattr(
        pt, "create_order",
        lambda *a, **kw: {"order_id": "FAKE-ORDER", "status": "PAYER_ACTION_REQUIRED",
                          "approval_url": "https://example.invalid"},
    )
    monkeypatch.setattr(ai_insight, "_llm_text", lambda *a, **kw: None)
    store.save_constraint(_constraint(), _now_iso())

    browser_client.get("/")
    r = browser_client.post(
        "/api/agent/requests",
        json={"vendor": "Notion", "item": "Notion Pro", "amount_usd": 10.0,
              "category": "SaaS", "lang": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "AUTO_APPROVE"
    assert not _CJK.search(body["reason"]), body["reason"]
    assert "All constraints" in body["reason"]
    # 模板兜底不冒充 AI 输出（既有 T4 约定）
    assert body["insight"] is None and body["explanation"] is None


def test_graph_ai_nodes_pass_lang_through(monkeypatch):
    """LangGraph 的 insight / explain 节点必须把 lang 交给生成函数。"""
    import agent.graph as g

    seen = {}

    def fake_insight(*args):
        seen["insight_lang"] = args[-1]
        return "Spent $165 of $200 this month; this $10 purchase passes."

    def fake_explain(*args):
        seen["explain_lang"] = args[-1]
        return "Approved: all constraints are satisfied."

    monkeypatch.setattr(ai_insight, "generate_insight", fake_insight)
    monkeypatch.setattr(ai_insight, "generate_explanation", fake_explain)

    state = {
        "request": _req(), "constraint": _constraint(),
        "spend": {"monthly_usd": 165.0}, "decision": "AUTO_APPROVE",
        "reason": "All constraints satisfied", "lang": "en",
    }
    assert "purchase passes" in g.insight_node(state)["insight"]
    assert "Approved" in g.explain_node(state)["explanation"]
    assert seen == {"insight_lang": "en", "explain_lang": "en"}


# ── 5. 前端确实把语言传上来 ────────────────────────────────────────

def test_constraints_page_sends_lang_with_both_calls():
    html = (Path(__file__).resolve().parent.parent / "static" / "constraints.html").read_text(
        encoding="utf-8-sig"
    )
    assert html.count("lang: window.MPLang ? MPLang.get() : \"both\"") == 2, (
        "setConstraint 与 tryPurchase 两次 /api/agent/parse 都要带 lang"
    )
