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


def _sqlite_path() -> str:
    """解析 SQLite 文件路径（跨平台）。

    `DATABASE_URL` 支持三种写法：
      - 空 → 用默认的 `mandatepay.db`（仓库根）
      - `sqlite:///relative/path.db` → 相对路径（相对仓库根）
      - `sqlite:////abs/path.db` / `sqlite:///D:/abs/path.db` → 绝对路径

    **踩过的坑**：`sqlite:////tmp/x.db`（四个斜杠，Unix 绝对路径的惯用写法）
    剥掉 `sqlite:///` 后剩下 `/tmp/x.db` —— 在 Windows 上这指的是
    **当前盘符的根目录**（比如 `C:` 盘下的 tmp 目录），不存在就报
    `unable to open database file`，错误信息完全看不出是路径问题。

    处理方式：
      - 绝对路径（以 `/` 或盘符开头）→ 去掉开头的 `/`，转成平台路径
      - Windows 风格的 `D:/...` → 去掉开头的 `/`（`/D:/...` 是无效路径）
      - 相对路径 → 相对仓库根解析（保证从任何 cwd 运行都指向同一个库）
    """
    dsn = _dsn()
    if not dsn.startswith("sqlite:///"):
        return str(_DEFAULT_SQLITE)

    raw = dsn[len("sqlite:///"):]
    if not raw:
        return str(_DEFAULT_SQLITE)

    # Windows 盘符写法：/D:/path → D:/path
    if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]

    # Unix 绝对路径：/tmp/x.db
    # 在 Windows 上没有 "/" 根目录的概念，硬把它当相对路径会在**仓库里**建出
    # tmp/ 目录（污染工作树）。所以映射到系统临时目录 —— 语义更接近，
    # 也不会弄脏仓库。
    if raw.startswith("/"):
        stripped = raw.lstrip("/")
        if not stripped:
            return str(_DEFAULT_SQLITE)
        import tempfile

        path = Path(tempfile.gettempdir()) / stripped
    else:
        path = Path(raw)
        if not path.is_absolute():
            # 相对路径 → 相对仓库根，保证从任何 cwd 运行都指向同一个库
            path = Path(__file__).resolve().parent.parent / path

    # SQLite 不会自动创建父目录 —— 缺了就报
    # "unable to open database file"（看不出是路径问题，容易误判）
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


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
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    payload TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paypal_product_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscription_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paypal_plan_id TEXT UNIQUE NOT NULL,
    paypal_product_id TEXT NOT NULL,
    name TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    interval_unit TEXT NOT NULL DEFAULT 'MONTH',
    interval_count INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT UNIQUE,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    payload TEXT NOT NULL,
    verified SMALLINT NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vendors (
    id BIGSERIAL PRIMARY KEY,
    paypal_product_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscription_plans (
    id BIGSERIAL PRIMARY KEY,
    paypal_plan_id TEXT UNIQUE NOT NULL,
    paypal_product_id TEXT NOT NULL,
    name TEXT NOT NULL,
    amount_usd DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    interval_unit TEXT NOT NULL DEFAULT 'MONTH',
    interval_count INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
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
            self._conn = sqlite3.connect(_sqlite_path())
            self._conn.row_factory = sqlite3.Row

    def __enter__(self):
        if self.is_pg:
            self._conn.execute(_PG_SCHEMA)
        else:
            self._conn.executescript(_SQLITE_SCHEMA)
        self._upgrade_mandates()
        self._upgrade_schema()
        return self

    def _upgrade_mandates(self) -> None:
        """幂等补齐 mandates 表的分层列（trace_id / layer / content）。

        建表语句是 CREATE TABLE IF NOT EXISTS，对**已存在**的旧表不会加列，
        所以三层 Mandate 改造必须靠这里做在线迁移，否则旧库直接崩。
        """
        adds = (
            ("trace_id", "TEXT NOT NULL DEFAULT ''"),
            ("layer", "TEXT NOT NULL DEFAULT 'intent'"),
            ("content", "TEXT NOT NULL DEFAULT ''"),
        )
        cur = self.cur()
        if self.is_pg:
            for col, ddl in adds:
                cur.execute(f"ALTER TABLE mandates ADD COLUMN IF NOT EXISTS {col} {ddl}")
        else:
            existing = {r["name"] for r in cur.execute("PRAGMA table_info(mandates)").fetchall()}
            for col, ddl in adds:
                if col not in existing:
                    cur.execute(f"ALTER TABLE mandates ADD COLUMN {col} {ddl}")
        # 索引必须在列存在之后再建，否则旧库上报 UndefinedColumn
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mandates_trace ON mandates(trace_id)")

    def _upgrade_schema(self) -> None:
        """幂等补齐 D8 新增列：constraints.daily_threshold_usd / mandates.amount_usd。"""
        cur = self.cur()
        # constraints: 日累计阈值
        if self.is_pg:
            cur.execute(
                "ALTER TABLE constraints ADD COLUMN IF NOT EXISTS daily_threshold_usd DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
        else:
            existing = {r["name"] for r in cur.execute("PRAGMA table_info(constraints)").fetchall()}
            if "daily_threshold_usd" not in existing:
                cur.execute("ALTER TABLE constraints ADD COLUMN daily_threshold_usd REAL NOT NULL DEFAULT 0")
        # mandates: 交易金额（供滚动预算/日累计统计）
        if self.is_pg:
            cur.execute(
                "ALTER TABLE mandates ADD COLUMN IF NOT EXISTS amount_usd DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
        else:
            existing = {r["name"] for r in cur.execute("PRAGMA table_info(mandates)").fetchall()}
            if "amount_usd" not in existing:
                cur.execute("ALTER TABLE mandates ADD COLUMN amount_usd REAL NOT NULL DEFAULT 0")
        # mandates: 供应商（供预算仪表盘按供应商聚合）
        if self.is_pg:
            cur.execute(
                "ALTER TABLE mandates ADD COLUMN IF NOT EXISTS vendor TEXT NOT NULL DEFAULT ''"
            )
        else:
            existing = {r["name"] for r in cur.execute("PRAGMA table_info(mandates)").fetchall()}
            if "vendor" not in existing:
                cur.execute("ALTER TABLE mandates ADD COLUMN vendor TEXT NOT NULL DEFAULT ''")
        # mandates: 付款人身份（W-09）。旧记录自动落空串，审计页据此隐藏付款人行。
        if self.is_pg:
            cur.execute(
                "ALTER TABLE mandates ADD COLUMN IF NOT EXISTS payer_id TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "ALTER TABLE mandates ADD COLUMN IF NOT EXISTS payer_email TEXT NOT NULL DEFAULT ''"
            )
        else:
            existing = {r["name"] for r in cur.execute("PRAGMA table_info(mandates)").fetchall()}
            if "payer_id" not in existing:
                cur.execute("ALTER TABLE mandates ADD COLUMN payer_id TEXT NOT NULL DEFAULT ''")
            if "payer_email" not in existing:
                cur.execute("ALTER TABLE mandates ADD COLUMN payer_email TEXT NOT NULL DEFAULT ''")

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
                "INSERT INTO constraints (monthly_budget_usd, single_tx_threshold_usd, daily_threshold_usd, vendor_whitelist, category_whitelist, created_at) VALUES (?,?,?,?,?,?) RETURNING id",
                (
                    c.monthly_budget_usd,
                    c.single_tx_threshold_usd,
                    c.daily_threshold_usd,
                    json.dumps(c.vendor_whitelist, ensure_ascii=False),
                    json.dumps(c.category_whitelist, ensure_ascii=False),
                    ts,
                ),
            ).fetchone()
            return row["id"]
        cur.execute(
            "INSERT INTO constraints (monthly_budget_usd, single_tx_threshold_usd, daily_threshold_usd, vendor_whitelist, category_whitelist, created_at) VALUES (?,?,?,?,?,?)",
            (
                c.monthly_budget_usd,
                c.single_tx_threshold_usd,
                c.daily_threshold_usd,
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
        daily_threshold_usd=row.get("daily_threshold_usd", 0.0) or 0.0,
        vendor_whitelist=json.loads(row["vendor_whitelist"]),
        category_whitelist=json.loads(row["category_whitelist"]),
    )


def sum_completed_amount(since_iso: str) -> float:
    """统计 [since_iso, now) 区间内**已完成支付**（status=COMPLETED）的累计金额。

    只取 `payment` 层，避免一笔交易的三层记录重复计数。
    用于仪表盘展示"已到账"金额。
    """
    with _Conn() as db:
        row = db.cur().execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM mandates "
            "WHERE layer='payment' AND status='COMPLETED' AND created_at >= ?",
            (since_iso,),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def sum_spent_amount(since_iso: str) -> float:
    """统计 [since_iso, now) 区间内**已承诺支出**的累计金额 —— 预算口径用这个。

    口径：**下单即计入**（order_id 存在即算），不只算已到账的。
    原因：Agent 下单后订单处于 PAYER_ACTION_REQUIRED，钱虽然还没扣，但**已经承诺要花**。
    若只统计 COMPLETED，Agent 可以连下多笔大额待支付订单把预算"绕过"——
    这是约束引擎必须防的口径漏洞。

    被拒绝的请求没有 order_id，天然不计入。
    """
    with _Conn() as db:
        row = db.cur().execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM mandates "
            "WHERE layer='payment' AND COALESCE(order_id,'') <> '' AND created_at >= ?",
            (since_iso,),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def sum_completed_by_vendor(since_iso: str) -> list[dict]:
    """按供应商聚合区间内已完成支付的金额 —— 预算仪表盘的分布图数据源。"""
    with _Conn() as db:
        rows = db.cur().execute(
            "SELECT COALESCE(NULLIF(vendor,''),'未标注') AS vendor, "
            "COALESCE(SUM(amount_usd),0) AS amount, COUNT(*) AS cnt "
            "FROM mandates WHERE layer='payment' AND status='COMPLETED' AND created_at >= ? "
            "GROUP BY 1 ORDER BY amount DESC",
            (since_iso,),
        ).fetchall()
    return [{"vendor": r["vendor"], "amount_usd": float(r["amount"]), "count": int(r["cnt"])} for r in rows]


def count_mandates() -> int:
    """交易总数（按 trace_id 去重）—— 审计页分页用。"""
    with _Conn() as db:
        row = db.cur().execute(
            "SELECT COUNT(DISTINCT COALESCE(NULLIF(trace_id,''), 'legacy:' || id)) AS n FROM mandates"
        ).fetchone()
    return int(row["n"]) if row else 0


# ── 供应商目录 / SaaS 订阅计划（P1 功能）──────────────────────────────

def save_vendor(paypal_product_id: str, name: str, description: str = "", category: str = "") -> None:
    """写入/更新一个供应商（PayPal 产品）。paypal_product_id 唯一。"""
    ts = _now()
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            cur.execute(
                """INSERT INTO vendors (paypal_product_id, name, description, category, created_at)
                   VALUES (?,?,?,?,?) ON CONFLICT (paypal_product_id) DO UPDATE SET
                     name=EXCLUDED.name, description=EXCLUDED.description, category=EXCLUDED.category""",
                (paypal_product_id, name, description, category, ts),
            )
        else:
            cur.execute(
                "INSERT OR REPLACE INTO vendors (paypal_product_id, name, description, category, created_at) VALUES (?,?,?,?,?)",
                (paypal_product_id, name, description, category, ts),
            )


def list_vendors() -> list[dict]:
    with _Conn() as db:
        return db.cur().execute("SELECT * FROM vendors ORDER BY id").fetchall()


def save_subscription_plan(
    paypal_plan_id: str,
    paypal_product_id: str,
    name: str,
    amount_usd: float,
    currency: str = "USD",
    interval_unit: str = "MONTH",
    interval_count: int = 1,
    description: str = "",
) -> None:
    """写入/更新一个订阅计划。paypal_plan_id 唯一。"""
    ts = _now()
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            cur.execute(
                """INSERT INTO subscription_plans
                   (paypal_plan_id, paypal_product_id, name, amount_usd, currency, interval_unit, interval_count, description, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (paypal_plan_id) DO UPDATE SET
                     name=EXCLUDED.name, amount_usd=EXCLUDED.amount_usd, currency=EXCLUDED.currency,
                     interval_unit=EXCLUDED.interval_unit, interval_count=EXCLUDED.interval_count,
                     description=EXCLUDED.description""",
                (paypal_plan_id, paypal_product_id, name, amount_usd, currency, interval_unit, interval_count, description, ts),
            )
        else:
            cur.execute(
                "INSERT OR REPLACE INTO subscription_plans (paypal_plan_id, paypal_product_id, name, amount_usd, currency, interval_unit, interval_count, description, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (paypal_plan_id, paypal_product_id, name, amount_usd, currency, interval_unit, interval_count, description, ts),
            )


def list_subscription_plans(product_id: str | None = None) -> list[dict]:
    with _Conn() as db:
        if product_id:
            return db.cur().execute(
                "SELECT * FROM subscription_plans WHERE paypal_product_id=? ORDER BY id", (product_id,)
            ).fetchall()
        return db.cur().execute("SELECT * FROM subscription_plans ORDER BY id").fetchall()


def sync_catalog_from_paypal() -> dict:
    """从 PayPal 拉取供应商与订阅计划，upsert 进本地目录（Agent 侧查询的数据源）。

    返回 {vendors: int, plans: int} 同步条数。

    注意：PayPal 的 list 接口返回**精简视图**，没有 billing_cycles/定价，
    必须对每个计划再调 show_subscription_plan(plan_id) 取完整详情才能拿到价格，
    否则本地 amount_usd 会同步成 0。
    """
    from agent import paypal_tools

    n_vendors = 0
    n_plans = 0
    for p in paypal_tools.list_products():
        pid = p.get("id")
        if not pid:
            continue
        save_vendor(pid, p.get("name", ""), p.get("description") or "", p.get("category") or "")
        n_vendors += 1
        for pl in paypal_tools.list_subscription_plans(product_id=pid):
            plid = pl.get("id")
            if not plid:
                continue
            # list 是精简视图，需取完整详情拿定价
            detail = paypal_tools.show_subscription_plan(plid)
            amount, currency, unit, count = 0.0, "USD", "MONTH", 1
            cycles = detail.get("billing_cycles") or []
            if cycles:
                c0 = cycles[0]
                price = (c0.get("pricing_scheme") or {}).get("fixed_price") or {}
                try:
                    amount = float(price.get("value", 0) or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                currency = price.get("currency_code") or "USD"
                freq = c0.get("frequency") or {}
                unit = freq.get("interval_unit") or "MONTH"
                count = int(freq.get("interval_count", 1) or 1)
            save_subscription_plan(
                plid, pid, detail.get("name") or pl.get("name", ""), amount, currency,
                unit, count, description=detail.get("description") or "",
            )
            n_plans += 1
    return {"vendors": n_vendors, "plans": n_plans}


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


# ── MandateRecord（三层：intent / cart / payment 各一条）──────────────
_COLS = "mandate_uuid, trace_id, layer, content, intent, cart, payment, status, decision, order_id, capture_id, amount_usd, vendor, payer_id, payer_email, created_at"


def _params(m: MandateRecord) -> tuple:
    return (
        m.id,
        m.trace_id,
        m.layer.value,
        m.content,
        m.intent,
        m.cart,
        m.payment,
        m.status.value,
        m.decision.value if m.decision else None,
        m.order_id,
        m.capture_id,
        m.amount_usd,
        m.vendor,
        m.payer_id,
        m.payer_email,
        m.created_at,
    )


def save_mandate(m: MandateRecord) -> int:
    """写入单条 Mandate 记录。"""
    placeholders = ",".join("?" * len(_COLS.split(",")))
    with _Conn() as db:
        cur = db.cur()
        if db.is_pg:
            row = cur.execute(
                f"""INSERT INTO mandates ({_COLS}) VALUES ({placeholders})
                   ON CONFLICT (mandate_uuid) DO UPDATE SET
                     trace_id=EXCLUDED.trace_id, layer=EXCLUDED.layer, content=EXCLUDED.content,
                     intent=EXCLUDED.intent, cart=EXCLUDED.cart, payment=EXCLUDED.payment,
                     status=EXCLUDED.status, decision=EXCLUDED.decision,
                     order_id=EXCLUDED.order_id, capture_id=EXCLUDED.capture_id,
                     amount_usd=EXCLUDED.amount_usd, vendor=EXCLUDED.vendor,
                     payer_id=EXCLUDED.payer_id, payer_email=EXCLUDED.payer_email
                   RETURNING id""",
                _params(m),
            ).fetchone()
            return row["id"]
        cur.execute(
            f"INSERT OR REPLACE INTO mandates ({_COLS}) VALUES ({placeholders})",
            _params(m),
        )
        return cur.lastrowid


def save_mandates(trio: list[MandateRecord]) -> int:
    """一次交易写入三条 Mandate 记录（Intent / Cart / Payment 各一条）。返回首条 id。"""
    if not trio:
        raise ValueError("trio 不能为空")
    first = save_mandate(trio[0])
    for m in trio[1:]:
        save_mandate(m)
    return first


def _trace_of_mandate(mandate_uuid: str) -> str | None:
    with _Conn() as db:
        row = db.cur().execute(
            "SELECT trace_id FROM mandates WHERE mandate_uuid=?", (mandate_uuid,)
        ).fetchone()
    return row["trace_id"] if row else None


def update_mandate_status(
    mandate_uuid: str,
    status: str,
    order_id: str | None = None,
    capture_id: str | None = None,
    payer_id: str | None = None,
    payer_email: str | None = None,
) -> None:
    """按 mandate_uuid 定位，但**整笔交易三层同步更新状态**。

    三层 Mandate 属于同一笔交易，状态必须一致 —— 只更新一条会导致审计页出现
    「Payment 层 COMPLETED 但 Intent 层还 APPROVED」的假象。

    W-09：payer_id / payer_email 走 COALESCE —— 捕获响应里没有付款人信息时
    传 None，不覆盖已有的值（空串也按 None 处理，避免把已写入的身份清掉）。
    """
    payer_id = payer_id or None
    payer_email = payer_email or None
    sets = (
        "status=?, order_id=COALESCE(?, order_id), capture_id=COALESCE(?, capture_id), "
        "payer_id=COALESCE(?, payer_id), payer_email=COALESCE(?, payer_email)"
    )
    vals = (status, order_id, capture_id, payer_id, payer_email)
    trace_id = _trace_of_mandate(mandate_uuid)
    with _Conn() as db:
        if trace_id:
            db.cur().execute(f"UPDATE mandates SET {sets} WHERE trace_id=?", (*vals, trace_id))
        else:
            db.cur().execute(f"UPDATE mandates SET {sets} WHERE mandate_uuid=?", (*vals, mandate_uuid))


def update_mandate_by_order(
    order_id: str,
    status: str,
    capture_id: str | None = None,
    payer_id: str | None = None,
    payer_email: str | None = None,
) -> int:
    """按 PayPal order_id 定位 mandate 并更新三层 —— Webhook 回调用。返回受影响行数。

    W-09：新增可选 payer 参数，供 webhook 事件里带付款人信息时顺手写入；
    现有调用点不传，行为与改造前一致（COALESCE 不覆盖）。
    """
    sets = (
        "status=?, capture_id=COALESCE(?, capture_id), "
        "payer_id=COALESCE(?, payer_id), payer_email=COALESCE(?, payer_email)"
    )
    vals = (status, capture_id, payer_id or None, payer_email or None)
    with _Conn() as db:
        cur = db.cur()
        row = cur.execute("SELECT trace_id FROM mandates WHERE order_id=?", (order_id,)).fetchone()
        if row and row["trace_id"]:
            cur.execute(f"UPDATE mandates SET {sets} WHERE trace_id=?", (*vals, row["trace_id"]))
        else:
            cur.execute(f"UPDATE mandates SET {sets} WHERE order_id=?", (*vals, order_id))
        return cur._cur.rowcount


def find_mandate_by_order(order_id: str) -> dict | None:
    with _Conn() as db:
        rows = db.cur().execute("SELECT * FROM mandates WHERE order_id=?", (order_id,)).fetchall()
    return _aggregate(rows)[0] if rows else None


def _aggregate(rows: list[dict]) -> list[dict]:
    """把三层记录聚合回「一笔交易一行」的视图。

    DB 里一笔交易是三条记录（intent/cart/payment），但前端 /audit 与旧查询
    期望一行含三层内容 —— 这里做归并，使**前端零改动**。
    每条记录本身冗余存了三层快照，所以取 payment 层作代表行即可还原完整形状。
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        tid = r.get("trace_id") or ""
        key = tid if tid else f"legacy:{r['id']}"  # 旧数据无 trace_id，各自独立成组
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    out: list[dict] = []
    for key in order:
        grp = groups[key]
        rep = next((r for r in grp if r.get("layer") == "payment"), grp[0])
        out.append(
            {
                "mandate_uuid": rep["mandate_uuid"],
                "trace_id": rep.get("trace_id") or "",
                "intent": rep["intent"],
                "cart": rep["cart"],
                "payment": rep["payment"],
                "status": rep["status"],
                "decision": rep["decision"],
                "order_id": rep["order_id"],
                "capture_id": rep["capture_id"],
                "amount_usd": float(rep.get("amount_usd") or 0),
                "vendor": rep.get("vendor") or "",
                # W-09：付款人身份。旧记录是空串，前端据此隐藏付款人行。
                "payer_id": rep.get("payer_id") or "",
                "payer_email": rep.get("payer_email") or "",
                "created_at": rep["created_at"],
                # 分层明细，供审计详情展示（前端可按需消费）
                "layers": {
                    r.get("layer", "intent"): {
                        "mandate_uuid": r["mandate_uuid"],
                        "content": r.get("content", ""),
                        "status": r["status"],
                        "created_at": r["created_at"],
                        "payer_id": r.get("payer_id") or "",
                        "payer_email": r.get("payer_email") or "",
                    }
                    for r in grp
                },
            }
        )
    return out


def list_mandates(limit: int = 100, offset: int = 0) -> list[dict]:
    """审计列表：一笔交易一行（三层聚合后），支持分页。

    分页必须在**交易粒度**上做：先按 trace_id 聚合成交易，再切片。
    若直接对原始三层的行做 LIMIT/OFFSET，会出现「一页 20 条其实只有 7 笔交易」
    或「同一笔交易被切到两页」的错乱 —— 这是审计页最容易踩的坑。
    """
    with _Conn() as db:
        rows = db.cur().execute("SELECT * FROM mandates ORDER BY id DESC").fetchall()
    agg = _aggregate(rows)
    return agg[offset: offset + limit]


def list_mandate_layers(trace_id: str) -> list[dict]:
    """按 trace_id 取出一笔交易的三层原始记录（审计详情用）。"""
    with _Conn() as db:
        return db.cur().execute(
            "SELECT * FROM mandates WHERE trace_id=? ORDER BY id", (trace_id,)
        ).fetchall()


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
