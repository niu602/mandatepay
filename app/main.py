"""FastAPI 入口：支付页面 + PayPal REST 支付 API + Agent 约束/审计 API。

三条面：
1. Web 支付流（已验证跑通）：/ 、/api/create-order、/api/capture-order/{id}
2. Agent 流（LangGraph 状态机）：/api/agent/constraints、/api/agent/requests
3. 审计日志：/api/mandates
"""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, paypal_client, session

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时确保数据库表就绪。

    部署到 Render/Neon 后，数据库是**空库**（Neon 免费项目只有库没有表），
    而建表逻辑在 `store._Conn.__enter__` 里（连接时执行幂等 schema + 在线迁移）。
    这里显式触发一次，避免第一个请求才建表（冷启动首请求超时）。

    失败不阻断启动：表由后续请求的连接逻辑兜底建，
    只是在日志里明确暴露出来（而不是让首个用户请求报错）。
    """
    try:
        # store 在项目根的 agent/ 包里（不在 app/ 包内），要用绝对导入
        from agent import store

        with store._Conn():
            pass
        print("[startup] 数据库 schema 就绪:", store.backend_name())
    except Exception as exc:
        print(f"[startup] ⚠️ 建表失败（不阻断启动）: {exc}")
    yield


app = FastAPI(title="MandatePay — A Company Card for AI Agents", lifespan=lifespan)

# ──────────────────────────────────────────────────────
# W-08 · 会话鉴权
# ──────────────────────────────────────────────────────
# 为什么用 cookie session 而不是 header 凭据：五个前端页面的 fetch 全部无凭据
# （实测 11 处），页面路由本身不提供数据。header 方案一上就是"页面空 + 按钮 401"，
# 直接砸掉演示。cookie 方案下页面路由下发签名 cookie，页面内 fetch 同源自动带上，
# 前端代码一行不用改。
#
# 豁免清单是**显式**的，不是"凡 /api/* 全拦"：
#   - 页面路由：不鉴权（否则评委打不开）
#   - /healthz：不鉴权（外部保温探针不能带凭据）
#   - /api/webhook：**必须豁免** —— PayPal 是服务器对服务器调用，
#     不可能带浏览器 cookie。它自己有 PayPal 官方签名校验（失败返 401），
#     那才是这条路径正确的防护。
#   - /static/*：静态资源（页面要能加载）
EXEMPT_PATHS = frozenset({
    "/",
    "/constraints",
    "/budget",
    "/approvals",
    "/audit",
    "/healthz",
    # /api/health 也豁免 —— 它是给人排查配置用的诊断端点，
    # 且工单 DoD 明确要求「curl -i /healthz → 200」时可能连带用 /api/health 对照。
    # 它不返回任何交易数据，暴露 db_backend / paypal_mode 无安全风险。
    "/api/health",
    # ⚠️ /api/webhook 必须豁免 —— PayPal 是服务器对服务器调用，
    # 不可能带浏览器 cookie。它自己有 PayPal 官方签名校验（失败返 401），
    # 那才是这条路径正确的防护。**若误拦，PayPal 事件永远收不到，
    # mandate 状态不会更新** —— 这是 W-08 最容易踩的坑。
    "/api/webhook",
    "/favicon.ico",
})

# 页面路由 —— 访问时下发会话 cookie
SESSION_ISSUING_PATHS = frozenset({"/", "/constraints", "/budget", "/approvals", "/audit"})


@app.middleware("http")
async def session_auth(request: Request, call_next):
    """给 `/api/*` 加会话校验（cookie session）。

    只拦 `/api/*`（除豁免清单），页面路由与静态资源不动 ——
    这样前端一行不用改，而裸 curl 打 API 会被挡成 401。
    """
    path = request.url.path

    # 静态资源直接放行（页面要能加载 CSS/JS）
    if path.startswith("/static/"):
        return await call_next(request)

    if path.startswith("/api/") and path not in EXEMPT_PATHS:
        cookie_header = request.headers.get("cookie")
        if not session.verify_cookie(cookie_header, user_agent=request.headers.get("user-agent")):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "需要有效的会话 cookie。请先访问首页取得会话。",
                    "hint": "open the page root first, then retry",
                },
            )

    response = await call_next(request)

    # 页面路由：下发会话 cookie（签发）
    if path in SESSION_ISSUING_PATHS:
        # 有反向代理时，request.url.scheme 可能仍是 http，
        # 所以 Secure 标记看 X-Forwarded-Proto（Azure App Service 会带）。
        forwarded = request.headers.get("x-forwarded-proto", "")
        is_https = request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"
        cookie_name, cookie_value = session.issue_cookie_parts(
            user_agent=request.headers.get("user-agent"),
            secure=is_https,
        )
        response.set_cookie(
            key=cookie_name,
            value=cookie_value,
            max_age=session.SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=is_https,
            path="/",
        )

    return response

# 挂载 /static —— 两个用途：
# 1. /budget 页面的 AG Studio 运行时探测 `/static/vendor/ag-studio.mjs`，
#    命中就用本地 ESM（离线可用、免 CDN 往返）；未命中才回退 jsdelivr。
# 2. 静态资源直出（部署到 Render 后前端不再依赖本地文件系统路径以外的东西）。
# 注意：static/vendor/ 已 gitignore（npm 商业包不入库），部署时由
# scripts/setup_ag_studio.py 在构建阶段安装；未安装则自动回退 CDN，功能不受影响。
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


@app.get("/constraints", response_class=HTMLResponse)
def constraints_page():
    """约束设定页：自然语言设约束 + 结构化规则展示 + 消费洞察/决策解释演示。"""
    html = (STATIC_DIR / "constraints.html").read_text(encoding="utf-8")
    return html


@app.get("/budget", response_class=HTMLResponse)
def budget_page():
    """预算仪表盘：本月已用 / 剩余 / 预测 / 按供应商分布。"""
    html = (STATIC_DIR / "budget.html").read_text(encoding="utf-8")
    return html


@app.get("/approvals", response_class=HTMLResponse)
def approvals_page():
    """采购审批台：采购请求列表 + 审批弹窗。"""
    html = (STATIC_DIR / "approvals.html").read_text(encoding="utf-8")
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


@app.get("/healthz")
def healthz():
    """轻量存活探针 —— 供外部 pinger 与演示前预热使用。

    **刻意做到最轻**：不查库、不调 PayPal、甚至不import `agent.store`
    （`/api/health` 为了报告 db_backend 导入了 store，这个导入链会拉起
    psycopg / dotenv 等模块）。这里只用 FastAPI 自身 + 标准库。

    为什么需要它而不是直接用 `/api/health`：
    1. **Azure App Service F1 不支持 Always On** —— 闲置后实例可能被卸载，
       首访要走冷启动（2-5 CPU 秒）。F1 有 **60 CPU 分钟/天硬上限**，
       所以保温必须是"低频"，而低频探针本身必须便宜到可以忽略。
    2. 冷启动预热时需要确认"服务真的起来了"，`/api/health` 返回体里
       混了PayPal 配置与订阅事件，对存活判断是噪音。
    3. 未来若给 `/api/*` 加鉴权（W-08），本端点会与页面路由一起被排除在
       鉴权之外 —— 探针不能带凭据。

    返回体刻意保持极小：pinger 每 30 分钟打一次，返回体越小越好。
    """
    return {"status": "ok"}


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
    monthly_budget_usd: float = Field(gt=0, description="月度滚动预算上限")
    single_tx_threshold_usd: float = Field(gt=0, description="单笔超此金额需人工确认")
    daily_threshold_usd: float = Field(default=0.0, ge=0, description="日累计阈值，0 表示不限制")
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
        daily_threshold_usd=body.daily_threshold_usd,
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
    from agent.models import (
        MandateRecord,
        MandateStatus,
        PurchaseRequest,
        build_mandate_trio,
        _now_iso,
    )
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
    # 一笔交易落三条：Intent / Cart / Payment 各一条，共享 trace_id
    trio = build_mandate_trio(
        intent=f"采购 {req.item}（供应商 {req.vendor}）",
        cart=f"{req.item} × ${req.amount_usd:.2f} ({req.currency})",
        amount_usd=req.amount_usd,
        vendor=req.vendor,
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
    store.save_mandates(trio)
    mandate = trio[-1]  # 以 Payment 层作代表行

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


class NLIn(BaseModel):
    """自然语言入口：一句话驱动整条 Agent 链路。"""

    text: str
    session_id: str = "default"


@app.post("/api/agent/parse")
def agent_parse(body: NLIn):
    """**自然语言 → 完整 LangGraph 七节点** 的端到端入口。

    与 `/api/agent/requests` 的区别：后者收结构化 JSON、绕过状态机；
    这里收自然语言，真实走 parse → validate → insight → explain → approve → pay → verify，
    因此 LLM function calling 解析、AI 消费洞察/决策解释、三层 Mandate 审计都会真正生效。

    返回里的 `insight`（消费洞察）与 `explanation`（决策解释+建议）是本周新增的
    两个 AI 输出点 —— 让 AI 不只"听一句话"，而是参与每一次决策。

    注意：命中采购意图且通过校验时，pay 节点会创建**真实沙箱订单**。
    """
    from langchain_core.messages import HumanMessage

    from agent import store
    from agent.graph import graph

    try:
        final = graph.invoke(
            {"messages": [HumanMessage(content=body.text)]},
            {"configurable": {"thread_id": body.session_id}},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent 执行失败: {exc}")

    mandate = final.get("mandate")
    out_mandate = None
    if mandate is not None:
        out_mandate = mandate.model_dump(mode="json")
        # 附带同 trace 的三层原始记录，便于前端逐层展示审计链
        out_mandate["layers"] = [
            {
                "layer": r["layer"],
                "content": r.get("content", ""),
                "status": r["status"],
                "mandate_uuid": r["mandate_uuid"],
                "created_at": r["created_at"],
            }
            for r in store.list_mandate_layers(mandate.trace_id)
        ]

    # 纯约束设定不涉及支付，清掉 checkpoint 可能残留的上一笔 payment_result
    decision = final.get("decision")
    payment = final.get("payment_result")
    if decision == "PARSED_CONSTRAINT":
        payment = None

    return {
        "text": body.text,
        "decision": decision,
        "error": final.get("error"),
        "constraint": final.get("constraint").model_dump() if final.get("constraint") else None,
        "request": final.get("request").model_dump() if final.get("request") else None,
        "mandate": out_mandate,
        "payment_result": payment,
        # 本周新增：AI 决策参与点
        "insight": final.get("insight"),          # 消费洞察一句话
        "explanation": final.get("explanation"),  # 决策解释 + 可操作建议
        "reason": final.get("reason"),            # 引擎原始原因
        "spend": final.get("spend"),              # 花费快照 {monthly_usd, daily_usd}
        # AI 推理轨迹：让"AI 是推理器而非分类器"在返回体里可见
        "reasoning": final.get("reasoning"),      # 模型的自然语言推理
        "tool_trace": final.get("tool_trace") or [],  # 工具调用轨迹
        "parse_note": final.get("parse_note"),    # 解析方式（LLM / 规则降级）
    }


# ──────────────────────────────────────────────────────
# 3. 审计日志与当前约束
# ──────────────────────────────────────────────────────
@app.get("/api/mandates")
def list_mandates(limit: int = 20, offset: int = 0):
    """三层 Mandate 审计日志（评委最爱看的页面数据源），支持分页。

    分页在**交易粒度**上做（先按 trace_id 聚合再切片），一笔交易永远完整落在同一页。
    """
    from agent import store

    total = store.count_mandates()
    items = store.list_mandates(limit=limit, offset=offset)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }


@app.get("/api/budget/summary")
def budget_summary():
    """预算仪表盘数据：本月已用 / 剩余 / 预测 / 按供应商分布。

    「预测」= 按本月已过天数与当前花费速率外推到月末（线性外推），
    给 Potentual Impact 提供可量化的风险提示。
    """
    from agent import store
    from agent.models import _now_iso

    now = _now_iso()
    month_start = now[:7] + "-01T00:00:00+00:00"
    day_start = now[:10] + "T00:00:00+00:00"

    constraint = store.load_latest_constraint()
    budget = constraint.monthly_budget_usd if constraint else 0.0
    # 预算口径 = 已承诺支出（下单即计入）；已到账单独给出，便于看真实扣款
    spent = store.sum_spent_amount(month_start)
    completed = store.sum_completed_amount(month_start)
    daily = store.sum_spent_amount(day_start)

    day_of_month = int(now[8:10])
    days_in_month = 30  # 近似值：跨月天数对预测影响很小
    projected = (spent / day_of_month * days_in_month) if day_of_month else 0.0

    return {
        "monthly_budget_usd": budget,
        "monthly_spent_usd": round(spent, 2),
        "monthly_completed_usd": round(completed, 2),
        "monthly_committed_usd": round(spent - completed, 2),
        "remaining_usd": round(budget - spent, 2),
        "daily_spent_usd": round(daily, 2),
        "used_ratio": (spent / budget) if budget else 0.0,
        "projected_month_usd": round(projected, 2),
        "projected_ratio": (projected / budget) if budget else 0.0,
        "day_of_month": day_of_month,
        "days_in_month": days_in_month,
        "over_budget": bool(budget and projected > budget),
        "by_vendor": store.sum_completed_by_vendor(month_start),
        "thresholds": {
            "single_tx_threshold_usd": constraint.single_tx_threshold_usd if constraint else 0,
            "daily_threshold_usd": constraint.daily_threshold_usd if constraint else 0,
            "vendor_whitelist": constraint.vendor_whitelist if constraint else [],
            "category_whitelist": constraint.category_whitelist if constraint else [],
        },
    }


@app.get("/api/approvals")
def list_approvals(status: str = "pending"):
    """采购请求列表（审批台）。

    status=pending → 待人工确认（PENDING_HUMAN）
    status=all     → 全部交易
    """
    from agent import store

    items = store.list_mandates(limit=200)
    if status == "pending":
        items = [i for i in items if i.get("status") == "PENDING_HUMAN"]
    elif status and status != "all":
        items = [i for i in items if i.get("status") == status]
    return {"items": items, "total": len(items)}


@app.post("/api/approvals/{trace_id}/reject")
def reject_approval(trace_id: str):
    """拒绝一笔待审批的采购请求 —— 人拿着最终否决权。"""
    from agent import store

    layers = store.list_mandate_layers(trace_id)
    if not layers:
        raise HTTPException(status_code=404, detail=f"找不到 trace_id={trace_id} 的 Mandate")
    for row in layers:
        store.update_mandate_status(row["mandate_uuid"], "REJECTED")
    return {"trace_id": trace_id, "status": "REJECTED", "updated": len(layers)}


@app.get("/api/constraints/current")
def current_constraint():
    """当前生效的约束。"""
    from agent import store

    c = store.load_latest_constraint()
    return c.model_dump() if c else {"detail": "尚未设定约束"}


# ──────────────────────────────────────────────────────
# 4. 供应商目录 / SaaS 订阅计划（P1：Agent 侧可查询）
# ──────────────────────────────────────────────────────
class ProductIn(BaseModel):
    name: str
    description: str = ""
    product_type: str = "SERVICE"  # PHYSICAL / DIGITAL / SERVICE


class PlanIn(BaseModel):
    product_id: str = Field(description="PayPal 供应商(product) id")
    name: str
    amount_usd: float = Field(gt=0)
    currency: str = "USD"
    interval_unit: str = "MONTH"   # MONTH / WEEK / YEAR / DAY
    interval_count: int = 1
    description: str = ""


@app.get("/api/catalog")
def get_catalog():
    """返回本地供应商目录与订阅计划 —— Agent 侧查询入口。

    本地目录由 PayPal 同步（/api/catalog/sync）或显式创建而来，
    Agent 无需每次采购都打 PayPal API，直接查本地即可。
    """
    from agent import store

    return {
        "vendors": store.list_vendors(),
        "plans": store.list_subscription_plans(),
    }


@app.post("/api/catalog/sync")
def sync_catalog():
    """从 PayPal 拉取供应商与订阅计划，upsert 进本地目录。"""
    from agent import store

    result = store.sync_catalog_from_paypal()
    return {"synced": result}


@app.post("/api/catalog/products")
def create_product(body: ProductIn):
    """用 Toolkit 创建供应商（PayPal 产品）并写入本地目录。"""
    from agent import store
    from agent import paypal_tools

    created = paypal_tools.create_product(body.name, body.description, body.product_type)
    pid = created.get("product_id")
    if not pid:
        raise HTTPException(status_code=502, detail=f"PayPal 创建产品失败: {created}")
    store.save_vendor(pid, body.name, body.description)
    return {"product_id": pid, "name": body.name}


@app.post("/api/catalog/plans")
def create_plan(body: PlanIn):
    """用 Toolkit 创建 SaaS 订阅计划并写入本地目录。"""
    from agent import store
    from agent import paypal_tools

    created = paypal_tools.create_subscription_plan(
        product_id=body.product_id,
        name=body.name,
        amount_usd=body.amount_usd,
        currency=body.currency,
        interval_unit=body.interval_unit,
        interval_count=body.interval_count,
        description=body.description,
    )
    plan_id = created.get("plan_id")
    if not plan_id:
        raise HTTPException(status_code=502, detail=f"PayPal 创建计划失败: {created}")
    store.save_subscription_plan(
        plan_id, body.product_id, body.name, body.amount_usd,
        body.currency, body.interval_unit, body.interval_count, body.description,
    )
    return {"plan_id": plan_id, "name": body.name}


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
