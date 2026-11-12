"""W-09 · Mandate 审计记录付款人身份（PayerID / Payer Email）。

覆盖工单 §四 的四类用例：
1. capture 响应里提取 payer（含实测的主路径、两种兜底路径、缺失/畸形响应）
2. store 落库 + `_aggregate` 透传 + COALESCE 不覆盖
3. 旧 SQLite schema 幂等迁移
4. `/api/mandates/confirm` 端点（TestClient）写入 / 兜底 / 空值

⚠️ 字段路径以实测为准：2026-11-12 沙箱订单 6S3278047H2709334 显示
`payment_source` 在**订单顶层**，结构是
`payment_source.paypal.{account_id,email_address}`，没有 `paypal.payer` 层；
工单正文给的 `purchase_units[0].payment_source.paypal.payer` 只作为兜底路径保留。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from agent import paypal_tools, store
from agent.models import MandateLayer, MandateRecord


@pytest.fixture
def fake_tool(monkeypatch):
    """替换 Toolkit 的 capture_order 工具，注入预设返回值。"""

    class _FakeTool:
        def __init__(self, raw):
            self._raw = raw
            self.calls = []

        def invoke(self, payload):
            self.calls.append(payload)
            return self._raw

    def _install(raw):
        tool = _FakeTool(raw)
        monkeypatch.setattr(paypal_tools, "_get_tool", lambda name: tool)
        return tool

    return _install


def _capture_payload(**overrides):
    """构造一个最小可用的 Toolkit 包装响应。"""
    raw = {
        "id": "ORDER-PAYER-1",
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": "CAP-PAYER-1"}]}}],
    }
    raw.update(overrides)
    return json.dumps({"status": "COMPLETED", "amount": "USD 10.00", "raw": raw})


def _unique_order(prefix="ORDER-PAYER-TEST"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ── 1. payer 提取 ────────────────────────────────────────────────────

def test_capture_extracts_payer_from_top_level_payment_source(fake_tool):
    """实测主路径：顶层 payment_source.paypal.{account_id,email_address}。"""
    fake_tool(_capture_payload(payment_source={"paypal": {
        "account_id": "SRM9NMH3AN8QU",
        "email_address": "sb-buyer@personal.example.com",
    }}))
    res = paypal_tools.capture_order("ORDER-PAYER-1")
    assert res["payer_id"] == "SRM9NMH3AN8QU"
    assert res["payer_email"] == "sb-buyer@personal.example.com"
    assert res["capture_id"] == "CAP-PAYER-1", "新字段不能影响既有 capture_id 提取"


def test_capture_falls_back_to_legacy_top_level_payer(fake_tool):
    """顶层旧字段 payer.{payer_id,email_address} 也要认。"""
    fake_tool(_capture_payload(payer={
        "payer_id": "LEGACY-PID", "email_address": "legacy@example.com",
    }))
    res = paypal_tools.capture_order("ORDER-PAYER-1")
    assert res["payer_id"] == "LEGACY-PID"
    assert res["payer_email"] == "legacy@example.com"


def test_capture_falls_back_to_nested_payer_in_purchase_unit(fake_tool):
    """工单正文里的路径（某些响应形态）作为第三候选保留。"""
    fake_tool(_capture_payload(purchase_units=[{
        "payment_source": {"paypal": {"payer": {
            "account_id": "NESTED-PID", "email_address": "nested@example.com",
        }}},
        "payments": {"captures": [{"id": "CAP-NESTED"}]},
    }]))
    res = paypal_tools.capture_order("ORDER-PAYER-1")
    assert res["payer_id"] == "NESTED-PID"
    assert res["payer_email"] == "nested@example.com"


def test_capture_without_payer_returns_empty_strings(fake_tool):
    """测试卡直付订单（payment_source=card）没有付款人 —— 不崩、返回空串。"""
    fake_tool(_capture_payload(payment_source={"card": {"last_digits": "0191"}}))
    res = paypal_tools.capture_order("ORDER-PAYER-1")
    assert res["payer_id"] == ""
    assert res["payer_email"] == ""
    assert res["status"] == "COMPLETED"


@pytest.mark.parametrize("raw", [
    {},
    {"status": "COMPLETED"},
    {"purchase_units": []},
    {"payment_source": "not-a-dict", "purchase_units": "not-a-list"},
    {"payer": None, "payment_source": {"paypal": None}},
])
def test_capture_never_breaks_on_malformed_payer_shape(fake_tool, raw):
    """捕获链路不允许因为一个展示用字段崩掉（工单红线：防御式取值）。"""
    fake_tool(_capture_payload(**raw))
    res = paypal_tools.capture_order("ORDER-PAYER-1")
    assert isinstance(res["payer_id"], str)
    assert isinstance(res["payer_email"], str)


# ── 2. store 落库 / 聚合 / COALESCE ──────────────────────────────────

def _new_mandate(order_id, **kwargs):
    return MandateRecord(
        layer=MandateLayer.PAYMENT,
        content="pay", intent="intent", cart="cart", payment="pay",
        order_id=order_id, **kwargs,
    )


def _find(order_id):
    return next(
        (r for r in store.list_mandates(limit=1000) if r.get("order_id") == order_id),
        None,
    )


def test_store_round_trips_payer_fields_through_aggregate():
    """save_mandates → list_mandates / _aggregate 都能读出两字段，且透传到 layers。"""
    oid = _unique_order()
    m = _new_mandate(oid, payer_id="PID-STORE", payer_email="store@example.com")
    store.save_mandate(m)

    row = _find(oid)
    assert row is not None
    assert row["payer_id"] == "PID-STORE"
    assert row["payer_email"] == "store@example.com"
    # 审计页 layerBox 读的是 layers.payment，不能只放在交易顶层
    assert row["layers"]["payment"]["payer_id"] == "PID-STORE"
    assert row["layers"]["payment"]["payer_email"] == "store@example.com"


def test_update_mandate_status_coalesce_keeps_existing_payer():
    """不传 payer 参数、或传空串，都不能把已写入的身份清掉。"""
    oid = _unique_order()
    m = _new_mandate(oid, payer_id="PID-ORIG", payer_email="orig@example.com")
    store.save_mandate(m)

    store.update_mandate_status(m.id, "COMPLETED")
    assert _find(oid)["payer_id"] == "PID-ORIG"

    store.update_mandate_status(m.id, "COMPLETED", payer_id="", payer_email="")
    assert _find(oid)["payer_id"] == "PID-ORIG"
    assert _find(oid)["payer_email"] == "orig@example.com"

    store.update_mandate_status(m.id, "COMPLETED", payer_id="PID-NEW", payer_email="new@example.com")
    assert _find(oid)["payer_id"] == "PID-NEW"
    assert _find(oid)["payer_email"] == "new@example.com"


def test_update_mandate_by_order_accepts_payer_without_breaking_existing_callers():
    """webhook 路径：不传 payer 时行为与改造前一致；传了才写。"""
    oid = _unique_order()
    m = _new_mandate(oid)
    store.save_mandate(m)

    assert store.update_mandate_by_order(oid, "COMPLETED") >= 1
    assert _find(oid)["payer_id"] == ""

    store.update_mandate_by_order(oid, "COMPLETED", payer_id="PID-WH", payer_email="wh@example.com")
    assert _find(oid)["payer_id"] == "PID-WH"
    assert _find(oid)["payer_email"] == "wh@example.com"


# ── 3. 旧库迁移 ──────────────────────────────────────────────────────

def test_old_sqlite_schema_gets_payer_columns_idempotently(tmp_path, monkeypatch):
    """用改造前的 mandates 表结构建库，_upgrade_schema 自动加列且旧行可查。"""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE mandates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mandate_uuid TEXT UNIQUE NOT NULL,
            trace_id TEXT NOT NULL DEFAULT '',
            layer TEXT NOT NULL DEFAULT 'intent',
            content TEXT NOT NULL DEFAULT '',
            intent TEXT NOT NULL,
            cart TEXT NOT NULL,
            payment TEXT NOT NULL,
            status TEXT NOT NULL,
            decision TEXT,
            order_id TEXT,
            capture_id TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO mandates (mandate_uuid, intent, cart, payment, status, created_at)
        VALUES ('legacy-row', 'old intent', 'old cart', 'old payment',
                'COMPLETED', '2026-01-01T00:00:00Z');
    """)
    con.commit()
    con.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    with store._Conn() as c:
        cols = {r["name"] for r in c.cur().execute("PRAGMA table_info(mandates)").fetchall()}
    assert {"payer_id", "payer_email"} <= cols, "旧库必须自动加出两列"

    with store._Conn() as c:
        row = c.cur().execute(
            "SELECT payer_id, payer_email, intent FROM mandates WHERE mandate_uuid='legacy-row'"
        ).fetchone()
    assert row["intent"] == "old intent"
    assert row["payer_id"] == "" and row["payer_email"] == ""

    # 幂等：重复进入不报错
    with store._Conn():
        pass


# ── 4. confirm 端点 ──────────────────────────────────────────────────

def _install_capture_stub(monkeypatch, **fields):
    def _stub(order_id):
        return {
            "order_id": order_id,
            "status": "COMPLETED",
            "capture_id": "CAP-STUB",
            "payer_id": fields.get("payer_id", ""),
            "payer_email": fields.get("payer_email", ""),
        }
    monkeypatch.setattr(paypal_tools, "capture_order", _stub)


def test_confirm_endpoint_writes_payer_from_capture_response(browser_client, monkeypatch):
    """审批台路径（无 PayerID 入参）：捕获响应里的付款人写入三层 Mandate。"""
    oid = _unique_order("ORDER-CONFIRM")
    m = _new_mandate(oid)
    store.save_mandate(m)
    _install_capture_stub(monkeypatch, payer_id="PID-CONFIRM", payer_email="confirm@example.com")

    browser_client.get("/")  # 拿 W-08 会话 cookie
    resp = browser_client.post(
        "/api/mandates/confirm",
        json={"order_id": oid, "mandate_uuid": m.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payer_id"] == "PID-CONFIRM"
    assert resp.json()["payer_email"] == "confirm@example.com"
    row = _find(oid)
    assert row["status"] == "COMPLETED"
    assert row["payer_id"] == "PID-CONFIRM"
    assert row["layers"]["payment"]["payer_email"] == "confirm@example.com"


def test_confirm_endpoint_uses_body_payer_id_as_fallback(browser_client, monkeypatch):
    """/paypal/return 回跳路径：响应缺 payer 时用 URL 带回来的 PayerID 兜底。"""
    oid = _unique_order("ORDER-RETURN")
    m = _new_mandate(oid)
    store.save_mandate(m)
    _install_capture_stub(monkeypatch)  # 响应里 payer 为空

    browser_client.get("/")
    resp = browser_client.post(
        "/api/mandates/confirm",
        json={"order_id": oid, "mandate_uuid": m.id, "payer_id": "PID-FROM-URL"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payer_id"] == "PID-FROM-URL"
    assert _find(oid)["payer_id"] == "PID-FROM-URL"


def test_confirm_endpoint_without_any_payer_is_safe(browser_client, monkeypatch):
    """测试卡/旧订单：完全没有付款人信息时不崩，字段保持空串。"""
    oid = _unique_order("ORDER-NOPAYER")
    m = _new_mandate(oid)
    store.save_mandate(m)
    _install_capture_stub(monkeypatch)

    browser_client.get("/")
    resp = browser_client.post(
        "/api/mandates/confirm", json={"order_id": oid, "mandate_uuid": m.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payer_id"] == ""
    assert resp.json()["payer_email"] == ""
    assert _find(oid)["status"] == "COMPLETED"
    assert _find(oid)["payer_id"] == ""


# ── 5. 前端文案仍受 i18n 托管 ────────────────────────────────────────

def test_audit_payer_line_is_i18n_managed():
    """新付款人行必须带 data-en/data-zh，且只在 Payment 层、payer_id 非空时渲染。"""
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "static" / "audit.html").read_text(
        encoding="utf-8-sig"
    )
    assert "cls === 'payment' && obj.payer_id" in html
    assert 'data-en="${en}" data-zh="${zh}"' in html
    assert "payerHtml" in html
