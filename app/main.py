"""FastAPI 入口：支付页面 + PayPal REST 支付 API + Agent 约束/审计 API。

三条面：
1. Web 支付流（已验证跑通）：/ 、/api/create-order、/api/capture-order/{id}
2. Agent 流（LangGraph 状态机）：/api/agent/constraints、/api/agent/requests
3. 审计日志：/api/mandates
"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config, paypal_client

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MandatePay — 带约束的 AI 支付执行层")


# ──────────────────────────────────────────────────────
# 1. Web 支付流（REST，已实测跑通，不要改）
# ──────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    """返回支付测试页，并把 .env 中的 Sandbox Client ID 注入 JS SDK。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("{{CLIENT_ID}}", config.PAYPAL_CLIENT_ID)


@app.get("/audit", response_class=HTMLResponse)
def audit_page():
    """三层 Mandate 审计日志页 + 人工确认付款入口。"""
    html = (STATIC_DIR / "audit.html").read_text(encoding="utf-8")
    return html


@app.post("/api/create-order")
async def create_order(request: Request):
    """后端创建 PayPal 订单，返回订单 id 给前端 JS SDK。"""
    body = await request.json()
    amount = float(body.get("amount", 10.0))
    try:
        order = paypal_client.create_order(amount)
    except Exception as exc:  # 骨架阶段统一暴露错误便于调试
        raise HTTPException(status_code=500, detail=f"创建订单失败: {exc}")
    return {"id": order["id"], "status": order["status"]}


@app.post("/api/capture-order/{order_id}")
async def capture_order(order_id: str):
    """买家在沙箱完成付款后，服务端捕获订单。"""
    try:
        captured = paypal_client.capture_order(order_id)
        captures = captured["purchase_units"][0]["payments"]["captures"]
        capture_id = captures[0]["id"] if captures else None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"捕获订单失败: {exc}")
    return {"status": captured.get("status"), "capture_id": capture_id}


# 约定订阅的 4 个支付事件（PayPal Developer Dashboard → Webhooks → 订阅）
SUBSCRIBED_EVENTS = (
    "PAYMENT.CAPTURE.COMPLETED",  # 钱真正到账 —— MandatePay 的闭环终点
    "CHECKOUT.ORDER.APPROVED",    # 买家批准订单（尚未扣款）
    "PAYMENT.CAPTURE.DENIED",     # 扣款被拒
    "PAYMENT.CAPTURE.PENDING",    # 扣款挂起
)

# transmission-* 头 → 内部字段名
_HEADER_MAP = {
    "paypal-transmission-id": "transmission_id",
    "paypal-transmission-sig": "transmission_sig",
    "paypal-transmission-time": "transmission_time",
    "paypal-cert-url": "cert_url",
    "paypal-auth-algo": "auth_algo",
}


@app.post("/api/webhook")
async def webhook(request: Request):
    """PayPal Webhook 回调入口（含官方签名校验）。

    处理顺序：取原始 body → 提取 transmission-* 头 → 官方接口验签
    → 幂等落库 → 按事件类型更新 Mandate → 记录关键日志。

    状态码语义：
      200  已接收并处理（或重复事件已幂等跳过）
      400  缺少必需的 transmission-* 头 / body 非 JSON
      401  签名校验失败（疑似伪造请求）
      500  服务端未配置 PAYPAL_WEBHOOK_ID
      503  无法连通 PayPal 验签接口（上游故障）
    """
    import logging

    log = logging.getLogger("mandatepay.webhook")

    raw = await request.body()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("webhook 收到非 JSON body")
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

    event_id = event.get("id")
    event_type = event.get("event_type", "UNKNOWN")
    log.info("收到 webhook: type=%s id=%s", event_type, event_id)

    # ── 1. 提取 transmission-* 头 ───────────────────────
    headers = {k.lower(): v for k, v in request.headers.items()}
    sig_parts = {}
    for header_name, field in _HEADER_MAP.items():
        val = headers.get(header_name)
        if not val:
            log.warning("缺少签名头: %s", header_name)
            raise HTTPException(
                status_code=400,
                detail=f"缺少必需的签名头 {header_name}（PayPal 每个 webhook 请求都应携带）",
            )
        sig_parts[field] = val

    # ── 2. 签名校验 ─────────────────────────────────────
    if config.PAYPAL_WEBHOOK_SKIP_VERIFY:
        log.warning("⚠ 本地联调模式：已跳过签名校验（PAYPAL_WEBHOOK_SKIP_VERIFY=true）")
        verified = True
    else:
        webhook_id = config.PAYPAL_WEBHOOK_ID
        if not webhook_id:
            log.error("未配置 PAYPAL_WEBHOOK_ID，无法验签")
            raise HTTPException(
                status_code=500,
                detail="服务端未配置 PAYPAL_WEBHOOK_ID（开发者后台 → Webhooks → 创建后获取）",
            )
        try:
            result = paypal_client.verify_webhook_signature(
                webhook_id=webhook_id,
                webhook_event=event,
                **sig_parts,
            )
        except Exception as exc:
            log.error("调用 PayPal 验签接口失败: %s", exc)
            raise HTTPException(status_code=503, detail=f"无法连通 PayPal 验签接口: {exc}")

        verified = result.get("verification_status") == "SUCCESS"
        if not verified:
            log.warning(
                "签名校验失败: transmission_id=%s status=%s detail=%s",
                sig_parts["transmission_id"],
                result.get("verification_status"),
                result.get("detail"),
            )
            raise HTTPException(
                status_code=401,
                detail="Webhook 签名校验失败，请求可能非 PayPal 发出或 webhook_id 不匹配",
            )
        log.info("签名校验通过: transmission_id=%s", sig_parts["transmission_id"])

    # ── 3. 幂等落库（event_id 唯一键，重复事件直接跳过）──
    from agent import store

    resource_id = _extract_resource_id(event)
    row_id = store.save_webhook_event(
        event_id=event_id,
        event_type=event_type,
        resource_id=resource_id,
        payload=event,
        verified=verified,
    )
    if row_id == 0 and event_id:
        log.info("重复事件，已幂等跳过: id=%s", event_id)
        return {"received": True, "event_type": event_type, "duplicate": True}

    # ── 4. 按事件类型处理业务 ────────────────────────────
    handled = _handle_webhook_event(event_type, event, resource_id, log)

    return {
        "received": True,
        "event_type": event_type,
        "verified": verified,
        "handled": handled,
    }


def _extract_resource_id(event: dict) -> str | None:
    """从事件体里取资源 id：capture 取 resource.id，订单类取 supplementary_data。"""
    resource = event.get("resource") or {}
    rid = resource.get("id")
    if rid:
        return rid
    sup = resource.get("supplementary_data") or {}
    return (sup.get("related_ids") or {}).get("order_id")


def _related_order_id(event: dict) -> str | None:
    """取事件关联的 PayPal 订单 id（用于回查 mandate）。"""
    resource = event.get("resource") or {}
    sup = resource.get("supplementary_data") or {}
    return (sup.get("related_ids") or {}).get("order_id")


def _handle_webhook_event(event_type: str, event: dict, resource_id: str | None, log) -> str:
    """按事件类型更新 Mandate 状态。返回处理结果描述。"""
    from agent import store

    order_id = _related_order_id(event)

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        # 钱到账 —— 这是 MandatePay 的闭环终点
        capture_id = resource_id
        n = store.update_mandate_by_order(order_id, "COMPLETED", capture_id=capture_id) if order_id else 0
        log.info("✅ 扣款完成: order=%s capture=%s 更新 mandate %d 条", order_id, capture_id, n)
        return f"mandate 置为 COMPLETED（更新 {n} 条）"

    if event_type == "CHECKOUT.ORDER.APPROVED":
        log.info("买家已批准订单: order=%s（尚未扣款，等待 capture）", resource_id)
        return "订单已批准，等待 capture"

    if event_type == "PAYMENT.CAPTURE.DENIED":
        n = store.update_mandate_by_order(order_id, "REJECTED") if order_id else 0
        log.warning("❌ 扣款被拒: order=%s（更新 %d 条）", order_id, n)
        return f"mandate 置为 REJECTED（更新 {n} 条）"

    if event_type == "PAYMENT.CAPTURE.PENDING":
        log.info("⏳ 扣款挂起: order=%s", order_id)
        return "扣款挂起，保持原状态"

    log.info("未订阅的事件类型，仅记录: %s", event_type)
    return "仅记录，未处理"


@app.get("/api/webhook/events")
def list_webhook_events(limit: int = 50):
    """Webhook 事件审计日志（联调时用来确认 PayPal 是否真的打过来了）。"""
    from agent import store

    return {"items": store.list_webhook_events(limit=limit)}


@app.get("/api/health")
def health():
    """健康检查：确认服务在跑、当前数据库后端与订阅事件。"""
    from agent import store

    return {
        "status": "ok",
        "paypal_mode": config.PAYPAL_MODE,
        "db_backend": store.backend_name(),
        "webhook_id_configured": bool(config.PAYPAL_WEBHOOK_ID),
        "skip_verify": config.PAYPAL_WEBHOOK_SKIP_VERIFY,
        "subscribed_events": list(SUBSCRIBED_EVENTS),
    }


# ──────────────────────────────────────────────────────
# 2. Agent 流：约束设定 + 采购执行（LangGraph 状态机）
# ──────────────────────────────────────────────────────
class ConstraintIn(BaseModel):
    monthly_budget_usd: float = Field(gt=0, description="月度预算上限")
    single_tx_threshold_usd: float = Field(gt=0, description="单笔超此金额需人工确认")
    vendor_whitelist: list[str] = Field(default_factory=list)
    category_whitelist: list[str] = Field(default_factory=list)
    session_id: str = "default"


@app.post("/api/agent/constraints")
def set_constraint(body: ConstraintIn):
    """设定约束：结构化入参 → 落库 + 写入状态机会话。

    直接构造 Constraint 落库，不依赖自然语言解析，保证接口稳定。
    """
    from agent import store
    from agent.models import Constraint, _now_iso

    c = Constraint(
        monthly_budget_usd=body.monthly_budget_usd,
        single_tx_threshold_usd=body.single_tx_threshold_usd,
        vendor_whitelist=body.vendor_whitelist,
        category_whitelist=body.category_whitelist,
    )
    store.save_constraint(c, _now_iso())
    return {"constraint": c.model_dump(), "session_id": body.session_id}


class PurchaseIn(BaseModel):
    vendor: str
    item: str
    amount_usd: float = Field(gt=0)
    currency: str = "USD"
    category: str = ""
    recurring: bool = False
    session_id: str = "default"


@app.post("/api/agent/requests")
def submit_purchase(body: PurchaseIn):
    """提交采购请求：约束校验 → 生成三层 Mandate → 真实 PayPal 下单。

    直接调约束引擎与 PayPal Toolkit，不经自然语言解析，
    避免 LLM/正则抽取误差影响演示稳定性。
    """
    from agent import store
    from agent.constraints import evaluate
    from agent.models import MandateRecord, MandateStatus, PurchaseRequest, _now_iso
    from agent.paypal_tools import create_order as paypal_create_order

    req = PurchaseRequest(
        vendor=body.vendor,
        item=body.item,
        amount_usd=body.amount_usd,
        currency=body.currency,
        category=body.category,
        recurring=body.recurring,
    )
    store.save_request(req, _now_iso())

    constraint = store.load_latest_constraint()
    result = evaluate(req, constraint)

    status_map = {
        "AUTO_APPROVE": MandateStatus.APPROVED,
        "REQUIRE_HUMAN": MandateStatus.PENDING_HUMAN,
        "REJECT": MandateStatus.REJECTED,
    }
    mandate = MandateRecord(
        intent=f"采购 {req.item}（供应商 {req.vendor}）",
        cart=f"{req.item} × ${req.amount_usd:.2f} ({req.currency})",
        payment=(
            "PayPal Sandbox · "
            + (
                "自动扣款"
                if result.decision.value == "AUTO_APPROVE"
                else ("人工确认后扣款" if result.decision.value == "REQUIRE_HUMAN" else "未扣款")
            )
        ),
        status=status_map[result.decision.value],
        decision=result.decision,
    )
    store.save_mandate(mandate)

    payment_result = {"status": "SKIPPED", "reason": result.reason}
    if result.decision.value != "REJECT":
        try:
            created = paypal_create_order(req.item, req.amount_usd)
            payment_result = {
                "order_id": created["order_id"],
                "status": created["status"],
                "approval_url": created["approval_url"],
            }
            store.update_mandate_status(mandate.id, mandate.status.value, order_id=created["order_id"])
            mandate = mandate.model_copy(update={"order_id": created["order_id"]})
        except Exception as exc:
            payment_result = {"status": "FAILED", "reason": str(exc)}

    return {
        "decision": result.decision.value,
        "reason": result.reason,
        "request": req.model_dump(),
        "mandate": mandate.model_dump(mode="json"),
        "payment_result": payment_result,
    }


# ──────────────────────────────────────────────────────
# 3. 审计日志与当前约束
# ──────────────────────────────────────────────────────
@app.get("/api/mandates")
def list_mandates(limit: int = 100):
    """三层 Mandate 审计日志（评委最爱看的页面数据源）。"""
    from agent import store

    return {"items": store.list_mandates(limit=limit)}


@app.get("/api/constraints/current")
def current_constraint():
    """当前生效的约束。"""
    from agent import store

    c = store.load_latest_constraint()
    return c.model_dump() if c else {"detail": "尚未设定约束"}


class ConfirmIn(BaseModel):
    """人工确认付款：人在沙箱点完「完成购买」后回调此接口。"""

    order_id: str
    mandate_uuid: str | None = None


@app.post("/api/mandates/confirm")
def confirm_payment(body: ConfirmIn):
    """人在 approval_url 完成付款后，由前端回调：捕获订单 + 断言 COMPLETED。

    这是 MandatePay 的核心交互模式 ——「人授权 + 机器执行」：
    Agent 只负责在约束内下单，最终扣款必须由人点确认。
    """
    from agent import store
    from agent.paypal_tools import capture_order

    try:
        res = capture_order(body.order_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"捕获失败（订单可能尚未付款）: {exc}")

    completed = res.get("status") == "COMPLETED"
    if completed and body.mandate_uuid:
        store.update_mandate_status(
            body.mandate_uuid, "COMPLETED", order_id=body.order_id, capture_id=res.get("capture_id")
        )
    elif completed:
        # 没传 mandate_uuid 时，按 order_id 反查并更新
        for row in store.list_mandates(limit=200):
            if row.get("order_id") == body.order_id:
                store.update_mandate_status(
                    row["mandate_uuid"], "COMPLETED", order_id=body.order_id, capture_id=res.get("capture_id")
                )
                break

    return {
        "order_id": body.order_id,
        "status": res.get("status"),
        "capture_id": res.get("capture_id"),
        "completed": completed,
    }
