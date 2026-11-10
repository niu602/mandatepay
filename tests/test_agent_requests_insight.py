"""`/api/agent/requests` 的 AI 洞察/解释接线测试（W-14）。

这条路径的定位：**结构化快速路径** —— 判定只由确定性引擎 `evaluate()` 决定，
AI 只负责"把判定翻译成人话"。

测试保护三件事：
1. **AI 答了 → insight/explanation 真的进响应**（不是摆设字段）；
2. **AI 挂了 → 降级为 null（T4 形态），且判定完全不受影响**；
3. **模板兜底不冒充 AI 输出** —— 固定模板不算 insight。

全程不联网：LLM 相关函数被 monkeypatch 掉；`create_order` 也被打桩，
任何意外的下单调用都会立刻失败（测试里绝不能创建真实沙箱订单）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MandatePayTest/1.0"

T4_NOTE = (
    "Structured fast path — deterministic engine only. "
    "Use /api/agent/parse for the full agent chain."
)


@pytest.fixture(autouse=True)
def _no_real_paypal_order(monkeypatch):
    """任何真实下单调用都让测试失败（绝不能创建沙箱订单）。"""
    import agent.paypal_tools as pt

    def _boom(*_a, **_kw):
        raise AssertionError("测试里不允许创建真实 PayPal 订单")

    monkeypatch.setattr(pt, "create_order", _boom)


def _client_with_session() -> tuple[TestClient, str]:
    """返回带会话 cookie 的 client 与 cookie 头。"""
    c = TestClient(app)
    page = c.get("/", headers={"user-agent": UA})
    raw = page.headers.get("set-cookie", "")
    cookie = raw.split(";", 1)[0]
    return c, cookie


def _post(c: TestClient, cookie: str, **over):
    body = {"vendor": "NotAWhitelistedVendor", "item": "Slack Pro", "amount_usd": 15}
    body.update(over)
    return c.post(
        "/api/agent/requests",
        headers={"user-agent": UA, "cookie": cookie, "content-type": "application/json"},
        json=body,
    )


def test_insight_is_injected_when_llm_answers(monkeypatch):
    """AI 真的答了 → insight / explanation 进响应。"""
    import agent.ai_insight as ai

    monkeypatch.setattr(
        ai, "generate_insight_with_source",
        lambda *a, **kw: ("本月已花 $30/$200，这笔 $15 会被拦截。", True),
    )
    monkeypatch.setattr(
        ai, "generate_explanation_with_source",
        lambda *a, **kw: ("Slack 不在白名单里，把它加进去就能放行。", True),
    )

    c, cookie = _client_with_session()
    r = _post(c, cookie)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["insight"] == "本月已花 $30/$200，这笔 $15 会被拦截。"
    assert body["explanation"] == "Slack 不在白名单里，把它加进去就能放行。"
    assert body["note"] != T4_NOTE, "有 AI 输出时 note 应说明这一点"
    assert "AI insight" in body["note"]


def test_llm_failure_degrades_to_null_and_keeps_decision(monkeypatch):
    """LLM 抛异常 → insight/explanation 保持 null，判定一字不变。"""
    import agent.ai_insight as ai

    def _boom(*_a, **_kw):
        raise RuntimeError("LLM 不可用")

    monkeypatch.setattr(ai, "generate_insight_with_source", _boom)
    monkeypatch.setattr(ai, "generate_explanation_with_source", _boom)

    c, cookie = _client_with_session()
    r = _post(c, cookie)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["insight"] is None, "LLM 失败时 insight 必须降级为 null"
    assert body["explanation"] is None, "LLM 失败时 explanation 必须降级为 null"
    assert body["note"] == T4_NOTE, "降级后应回到 T4 的标记形态"
    # 判定链不受 AI 影响：仍然是引擎给出的结论
    assert body["decision"] == "REJECT"
    assert body["reason"]
    assert body["payment_result"]["status"] == "SKIPPED", "被拒的请求不应下单"


def test_template_fallback_is_not_reported_as_ai_output(monkeypatch):
    """模板兜底（from_llm=False）不能冒充 AI 输出 → 仍然返回 null。"""
    import agent.ai_insight as ai

    monkeypatch.setattr(
        ai, "generate_insight_with_source",
        lambda *a, **kw: ("固定模板文案（不是 AI 写的）", False),
    )
    monkeypatch.setattr(
        ai, "generate_explanation_with_source",
        lambda *a, **kw: ("固定模板解释（不是 AI 写的）", False),
    )

    c, cookie = _client_with_session()
    r = _post(c, cookie)
    body = r.json()

    assert body["insight"] is None, "模板兜底不应被当作 AI 洞察"
    assert body["explanation"] is None
    assert body["note"] == T4_NOTE


def test_response_always_has_the_three_marker_fields(monkeypatch):
    """无论 AI 成功与否，响应里都有 insight / explanation / note 三个字段。"""
    import agent.ai_insight as ai

    monkeypatch.setattr(
        ai, "generate_insight_with_source", lambda *a, **kw: ("洞察", True))
    monkeypatch.setattr(
        ai, "generate_explanation_with_source", lambda *a, **kw: ("解释", True))

    c, cookie = _client_with_session()
    body = _post(c, cookie).json()

    for key in ("insight", "explanation", "note"):
        assert key in body, f"响应缺少字段 {key}"
    assert body["insight"] == "洞察"
    assert body["explanation"] == "解释"


def test_slow_llm_does_not_hang_the_endpoint(monkeypatch):
    """LLM 卡住 → 到预算就返回 null，不能把接口挂死。

    实测背景：Z.ai 免费档从 Azure 出网会被限流，单次 insight 调用能拖 60s~4min。
    同步接口不能无限期挂着（Azure 请求超时是 230s），所以有 `AGENT_INSIGHT_TIMEOUT_SEC`
    预算（默认 25s），到点就降级为 null。
    """
    import time

    import agent.ai_insight as ai

    def _slow(*_a, **_kw):
        time.sleep(30)
        return ("太慢了", True)

    monkeypatch.setenv("AGENT_INSIGHT_TIMEOUT_SEC", "1")
    monkeypatch.setattr(ai, "generate_insight_with_source", _slow)
    monkeypatch.setattr(ai, "generate_explanation_with_source", _slow)

    c, cookie = _client_with_session()
    t0 = time.time()
    r = _post(c, cookie)
    elapsed = time.time() - t0

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["insight"] is None, "超时后必须降级为 null"
    assert body["explanation"] is None
    assert body["decision"] == "REJECT", "判定不受 AI 超时影响"
    assert elapsed < 10, f"接口被 AI 拖了 {elapsed:.1f}s —— 超时预算没生效"
