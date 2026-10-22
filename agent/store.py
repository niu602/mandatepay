"""持久化层：Constraint / PurchaseRequest / MandateRecord 落盘。

支持两种后端，**通过 DATABASE_URL 切换**：

    DATABASE_URL=postgresql://user:pass@host:5432/dbname   → PostgreSQL
    DATABASE_URL 留空 / sqlite:///path/to.db                → SQLite（默认，零配置）

切换只需改环境变量，业务代码（graph.py / main.py）不用动。
所有 SQL 用 `?` 占位符书写，由 `_sql()` 按后端自动转换成 `%s`（PG）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from .models import Constraint, MandateRecord, PurchaseRequest

# 直接跑脚本（scripts/init_db.py 等）时也能读到项目根的 .env
load_dotenv()

# ── 后端选择 ────────────────────────────────────────────
_DEFAULT_SQLITE = Path(__file__).resolve().parent.parent / "mandatepay.db"


def _dsn() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def backend_name() -> str:
    """当前生效的后端名，用于日志与 /api/health。"""
    return "postgresql" if _dsn().startswith(("postgres://", "postgresql://")) else "sqlite"


def _effective_dsn() -> str:
    """Heroku 风格 postgres:// 归一化为 psycopg 需要的 postgresql://。"""
    dsn = _dsn()
    if dsn.startswith("postgres://"):
        return "postgresql://" + dsn[len("postgres://"):]
    return dsn


# ── 建表语句 ────────────────────────────────────────────
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS constraints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monthly_budget_usd REAL NOT NULL,
    single_tx_threshold_usd REAL NOT NULL,
    vendor_whitelist TEXT NOT NULL,
    category_whitelist TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL,
    item TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    currency TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    recurring INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mandates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mandate_uuid TEXT UNIQUE NOT NULL,
    intent TEXT NOT NULL,
    cart TEXT NOT NULL,
    payment TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    order_id TEXT,
    capture_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    payload TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
"""

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS constraints (
    id BIGSERIAL PRIMARY KEY,
    monthly_budget_usd DOUBLE PRECISION NOT NULL,
    single_tx_threshold_usd DOUBLE PRECISION NOT NULL,
    vendor_whitelist TEXT NOT NULL,
    category_whitelist TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_requests (
    id BIGSERIAL PRIMARY KEY,
    vendor TEXT NOT NULL,
    item TEXT NOT NULL,
    amount_usd DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    recurring SMALLINT NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mandates (
    id BIGSERIAL PRIMARY KEY,
    mandate_uuid TEXT UNIQUE NOT NULL,
    intent TEXT NOT NULL,
    cart TEXT NOT NULL,
    payment TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    order_id TEXT,
    capture_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT UNIQUE,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    payload TEXT NOT NULL,
    verified SMALLINT NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
"""


def _is_pg() -> bool:
    return backend_name() == "postgresql"


def _sql(q: str) -> str:
    """SQLite 用 `?`，PostgreSQL 用 `%s`。"""
    return q.replace("?", "%s") if _is_pg() else q


class _Cursor:
    """抹平 sqlite3 / psycopg3 游标差异（lastrowid / 参数占位符）。"""

    def __init__(self, cur, is_pg: bool):
        self._cur = cur
        self._is_pg = is_pg

    def execute(self, q: str, params: tuple = ()):
        self._cur.execute(_sql(q), params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict]:
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        if self._is_pg:
            # psycopg3 无 lastrowid：调用方需用 RETURNING id + fetchone
            return None
        return self._cur.lastrowid


class _Conn:
    """连接上下文：SQLite 用 `with conn`，PG 用显式 commit/close。"""

    def __init__(self):
        self.is_pg = _is_pg()
        if self.is_pg:
            import psycopg

            self._conn = psycopg.connect(_effective_dsn(), row_factory=psycopg.rows.dict_row)
        else:
            dsn = _dsn()
            path = dsn[len("sqlite:///"):] if dsn.startswith("sqlite:///") else str(_DEFAULT_SQLITE)
            self._conn = sqlite3.connect(path)
            self._conn.row_factory = sqlite3.Row

    def __enter__(self):
        if self.is_pg:
            self._conn.execute(_PG_SCHEMA)
        else:
            self._conn.executescript(_SQLITE_SCHEMA)
        return self

    def __exit__(self, *exc):
        try:
            if self.is_pg:
                if exc[0] is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
            else:
                if exc[0] is None:
                    self._conn.commit()
        finally:
            self._conn.close()
        return False

    def cur(self) -> _Cursor:
        return _Cursor(self._conn.cursor(), self.is_pg)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ── Constraint ─────────────────────────────────────────
def save_constraint(c: Constraint, created_at: str | None = None) -> int:
    ts = created_at or _now()
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            row = cur.execute(
                "INSERT INTO constraints (monthly_budget_usd, single_tx_threshold_usd, vendor_whitelist, category_whitelist, created_at) VALUES (?,?,?,?,?) RETURNING id",
                (
                    c.monthly_budget_usd,
                    c.single_tx_threshold_usd,
                    json.dumps(c.vendor_whitelist, ensure_ascii=False),
                    json.dumps(c.category_whitelist, ensure_ascii=False),
                    ts,
                ),
            ).fetchone()
            return row["id"]
        cur.execute(
            "INSERT INTO constraints (monthly_budget_usd, single_tx_threshold_usd, vendor_whitelist, category_whitelist, created_at) VALUES (?,?,?,?,?)",
            (
                c.monthly_budget_usd,
                c.single_tx_threshold_usd,
                json.dumps(c.vendor_whitelist, ensure_ascii=False),
                json.dumps(c.category_whitelist, ensure_ascii=False),
                ts,
            ),
        )
        return cur.lastrowid


def load_latest_constraint() -> Constraint | None:
    with _Conn() as db:
        row = db.cur().execute("SELECT * FROM constraints ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return Constraint(
        monthly_budget_usd=row["monthly_budget_usd"],
        single_tx_threshold_usd=row["single_tx_threshold_usd"],
        vendor_whitelist=json.loads(row["vendor_whitelist"]),
        category_whitelist=json.loads(row["category_whitelist"]),
    )


# ── PurchaseRequest ────────────────────────────────────
def save_request(r: PurchaseRequest, created_at: str | None = None) -> int:
    ts = created_at or _now()
    params = (r.vendor, r.item, r.amount_usd, r.currency, r.category, int(r.recurring), ts)
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            row = cur.execute(
                "INSERT INTO purchase_requests (vendor, item, amount_usd, currency, category, recurring, created_at) VALUES (?,?,?,?,?,?,?) RETURNING id",
                params,
            ).fetchone()
            return row["id"]
        cur.execute(
            "INSERT INTO purchase_requests (vendor, item, amount_usd, currency, category, recurring, created_at) VALUES (?,?,?,?,?,?,?)",
            params,
        )
        return cur.lastrowid


# ── MandateRecord ──────────────────────────────────────
def save_mandate(m: MandateRecord) -> int:
    params = (
        m.id,
        m.intent,
        m.cart,
        m.payment,
        m.status.value,
        m.decision.value if m.decision else None,
        m.order_id,
        m.capture_id,
        m.created_at,
    )
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            row = cur.execute(
                """INSERT INTO mandates (mandate_uuid, intent, cart, payment, status, decision, order_id, capture_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (mandate_uuid) DO UPDATE SET
                     intent=EXCLUDED.intent, cart=EXCLUDED.cart, payment=EXCLUDED.payment,
                     status=EXCLUDED.status, decision=EXCLUDED.decision,
                     order_id=EXCLUDED.order_id, capture_id=EXCLUDED.capture_id
                   RETURNING id""",
                params,
            ).fetchone()
            return row["id"]
        cur.execute(
            "INSERT OR REPLACE INTO mandates (mandate_uuid, intent, cart, payment, status, decision, order_id, capture_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            params,
        )
        return cur.lastrowid


def update_mandate_status(
    mandate_uuid: str,
    status: str,
    order_id: str | None = None,
    capture_id: str | None = None,
) -> None:
    with _Conn() as db:
        db.cur().execute(
            "UPDATE mandates SET status=?, order_id=COALESCE(?, order_id), capture_id=COALESCE(?, capture_id) WHERE mandate_uuid=?",
            (status, order_id, capture_id, mandate_uuid),
        )


def update_mandate_by_order(
    order_id: str,
    status: str,
    capture_id: str | None = None,
) -> int:
    """按 PayPal order_id 定位 mandate 并更新 —— Webhook 回调用。返回受影响行数。"""
    with _Conn() as db:
        cur = db.cur()
        cur.execute(
            "UPDATE mandates SET status=?, capture_id=COALESCE(?, capture_id) WHERE order_id=?",
            (status, capture_id, order_id),
        )
        return cur._cur.rowcount


def find_mandate_by_order(order_id: str) -> dict | None:
    with _Conn() as db:
        return db.cur().execute("SELECT * FROM mandates WHERE order_id=?", (order_id,)).fetchone()


def list_mandates(limit: int = 100) -> list[dict]:
    with _Conn() as db:
        return db.cur().execute("SELECT * FROM mandates ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ── Webhook 事件（D6 审计用）─────────────────────────────
def save_webhook_event(
    event_id: str | None,
    event_type: str,
    resource_id: str | None,
    payload: dict,
    verified: bool,
) -> int:
    params = (event_id, event_type, resource_id, json.dumps(payload, ensure_ascii=False), int(verified), _now())
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            row = cur.execute(
                """INSERT INTO webhook_events (event_id, event_type, resource_id, payload, verified, received_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT (event_id) DO NOTHING
                   RETURNING id""",
                params,
            ).fetchone()
            return row["id"] if row else 0
        cur.execute(
            "INSERT OR IGNORE INTO webhook_events (event_id, event_type, resource_id, payload, verified, received_at) VALUES (?,?,?,?,?,?)",
            params,
        )
        return cur.lastrowid or 0


def list_webhook_events(limit: int = 50) -> list[dict]:
    with _Conn() as db:
        return db.cur().execute("SELECT * FROM webhook_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
