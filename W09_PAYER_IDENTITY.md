# W-09 · Mandate 审计记录付款人身份（PayerID / Payer Email）

> 结论先行：**PayerID 已经能拿到，只是没落库**。PayPal 捕获响应里自带
> `purchase_units[0].payment_source.paypal.payer`（含 `account_id` 即 PayerID、
> `email_address`）。在捕获链路里顺手提取、存进 `MandateRecord`、审计页展示即可。
> 不改判定链、不改支付链路、不加登录。

---

## 一、问题背景

三层 Mandate 的叙事是 **"human authorises, machine executes"（人授权，机器执行）**。
当前审计记录能展示"这笔交易经过了三层授权"，但**答不出"谁授权的"**：

- `/paypal/return` 回跳页能显示 PayerID（`main.py:220` 的 `#pid` 单元格），
  但只用于展示，`confirm` 请求体里**只传 order_id**（`main.py:234-237`），没传 PayerID
- `capture_order()` 的响应里**本来就有付款人信息**，但只提取了 `capture_id`
  （`paypal_tools.py:162-177`），其余丢弃
- `MandateRecord` **没有 user/payer 字段**（`agent/models.py:74-97`），
  数据层根本没有地方存"谁"

## 二、方案（改动范围：5 个文件 + 测试）

### 改动 1 · `agent/models.py` —— 模型加两个字段

`MandateRecord` 追加（**都有默认值，`build_mandate_trio` 等所有构造点零改动**）：

```python
payer_id: str = ""        # PayPal PayerID（= capture 响应里的 payer.account_id）
payer_email: str = ""     # 付款人邮箱（payer.email_address，沙箱测试买家也有）
```

### 改动 2 · `agent/paypal_tools.py::capture_order` —— 提取付款人

在现有提取 `capture_id` 的同一段里追加（`paypal_tools.py:162-177`，已从 raw 里取
`purchase_units[0]`）：

```python
pu = (raw.get("purchase_units") or [{}])[0]
payer = ((pu.get("payment_source") or {}).get("paypal") or {}).get("payer") or {}
payer_id = payer.get("account_id") or ""
payer_email = payer.get("email_address") or ""
```

返回 dict 追加两个键：`"payer_id": payer_id, "payer_email": payer_email`。

**全部用嵌套 `.get()` 防御式取值** —— 沙箱响应结构若与文档有出入，字段缺失
返回空串而不是抛异常（捕获链路不允许因为新字段崩掉）。

### 改动 3 · `agent/store.py` —— 落库

1. `_COLS`（`store.py:600`）追加 `payer_id, payer_email`；
   `_params()`（`store.py:603-619`）按相同顺序追加两个值（**顺序必须一致**）
2. `save_mandate` 的 ON CONFLICT / INSERT 列清单同步加两列（PG 的 UPDATE SET 列表也要加）
3. `_upgrade_schema()`（`store.py:311-340`，`amount_usd`/`vendor` 同款位置）幂等加列：

   ```python
   # PG:
   cur.execute("ALTER TABLE mandates ADD COLUMN IF NOT EXISTS payer_id TEXT NOT NULL DEFAULT ''")
   cur.execute("ALTER TABLE mandates ADD COLUMN IF NOT EXISTS payer_email TEXT NOT NULL DEFAULT ''")
   # SQLite: 先 PRAGMA table_info 判存在再 ALTER TABLE mandates ADD COLUMN ...
   ```

4. `update_mandate_status()`（`store.py:665`）追加可选参数 `payer_id=None, payer_email=None`，
   UPDATE 语句用 `COALESCE`（与 order_id/capture_id 同款，空值不覆盖已有值）
5. `update_mandate_by_order()`（`store.py:690`，webhook 路径用）同样追加两参 + COALESCE

### 改动 4 · `app/main.py` —— 确认链路传入

1. `ConfirmIn`（`main.py:1024`）加 `payer_id: str | None = None`
2. `confirm_payment()`（`main.py:1028`）：捕获成功后把响应里的 payer 信息传给
   `update_mandate_status(..., payer_id=res.get("payer_id"), payer_email=res.get("payer_email"))`。
   **body.payer_id 作为兜底**：仅当 `res` 里 payer_id 为空且 body 里有值时用 body 的
   （回跳 URL 里的 PayerID 与 account_id 是同一个东西，只是来源不同，互为冗余）
3. `/paypal/return` 内联页（`_PAYPAL_RETURN_HTML`，`main.py:226-237`）：
   confirm fetch 的 body 加 `payer_id: q.get("PayerID") || undefined`

### 改动 5 · `static/audit.html` —— 审计页展示

`layerBox()`（`audit.html:124-131`）：payment 层的 `lsub` 里追加付款人行 ——
**仅当 `obj.payer_id` 非空时显示**（旧记录为空则不显示，保持页面干净）：

```html
<div class="lsub">
  uuid ...
  <br>${payerHtml}   <!-- 仅 cls==='payment' 且 obj.payer_id 非空时拼这段 -->
</div>
```

payerHtml 内容：`Payer 付款人: <email> · PayerID: <payer_id>`（email 为空只显示 PayerID）。
**新文案必须带 `data-en` / `data-zh` 属性**（沿用本页既有 i18n 模式，静态检查器 W-10 会查）。

### 不改的部分（红线）

- **不碰** `create_order` / 下单链路 —— 下单时付款人还没出现
- **不碰** 判定链（`evaluate()`）、不碰 LangGraph 七节点
- **不碰** approvals.html / budget.html / constraints.html / index.html
- **不做** PayPal OAuth 登录、不做账号体系（W-08 已明确定位）
- `/api/webhook` 的 payer 提取**可选**：webhook 事件 body 里
  `resource.purchase_units[0].payment_source.paypal.payer` 同样有付款人信息，
  若 `update_mandate_by_order` 已加参，顺手在 webhook handler 里提取传入即可；
  不提取也不影响主流程（confirm 点击时已写入）

## 三、验收（DoD）

| # | 项 | 标准 |
|---|---|---|
| 1 | 真实沙箱闭环 | 创建订单 → 沙箱付款 → `/paypal/return` 自动捕获 → **审计页 Payment 层显示 PayerID + email** |
| 2 | 审批台链路 | approvals 页点"我已付款·捕获"（无 PayerID 入参）→ 捕获响应提取 → 审计页同样显示 |
| 3 | 旧数据兼容 | 改造前已有的 mandate 记录（payer_id=''）**不崩、不显示付款人行**，其余字段不变 |
| 4 | 本地 + 线上 schema | SQLite 开发库与 Neon（线上）启动后自动加列（`_upgrade_schema` 幂等），无手工迁移 |
| 5 | 测试全绿 | `pytest` 全通过（既有测试零破坏 + 新增用例） |
| 6 | 静态检查 | `python scripts/check_ui_english.py` 通过（audit.html 新文案有 data-en/data-zh） |

## 四、测试要求（新增用例）

1. **payer 提取（mock）**：构造含 `payment_source.paypal.payer.{account_id,email_address}`
   的 capture 响应，断言 `capture_order()` 返回 dict 里 payer_id/payer_email 正确；
   再构造**不含** payer 字段的响应，断言返回空串且不抛异常
2. **store 落库**：save_mandates 写入后 `list_mandates` / `_aggregate` 能读出两字段；
   `update_mandate_status` 传 payer 参数后生效、不传时 COALESCE 不覆盖
3. **旧库迁移**：用不含新列的旧 schema 建 SQLite，启动 `_upgrade_schema` 后新列存在且旧行可查
4. **confirm 端点（TestClient）**：mock `capture_order` 返回 payer 信息 →
   断言 mandate 记录两字段写入；返回空 payer → 不崩、字段为空

## 五、已知限制（如实记录）

1. **旧记录无法补** —— 付款人身份只在捕获那一刻出现，改造前的交易永久为空
2. **沙箱买家邮箱是测试账号** —— 评审现场看到的 email 形如
   `sb-<random>@personal.example.com`，属正常现象；真买家场景是真实邮箱
3. **不做登录/多用户** —— 本项目单租户全局共享数据，付款人身份只是审计叙事的一部分，
   不构成账号体系
4. 若捕获响应实际结构与文档不符（沙箱差异），步骤二的防御式取值保证不崩，
   但需要按 §一 的实测结果调整字段路径 —— **见下方执行顺序第 0 步**

## 六、执行顺序（给执行 agent）

1. **先实测确认结构**（5 分钟）：用沙箱跑一次真实捕获
   （`create_order_with_test_card` 或既有回跳链路，订单 6S3278047H2709334 同款），
   把响应里 `purchase_units[0].payment_source` 的 JSON 打出来，确认 payer 字段路径
2. 改 models → store → paypal_tools → main.py → audit.html（按改动 1-5 顺序）
3. 跑测试：`pytest` 全绿 + `check_ui_english.py` 通过
4. 本地起服务，跑一次真实闭环验证 DoD 1、2
5. 提交 + push（GitHub Actions 自动部署到 Azure），
   线上 `GET /api/mandates` 抽查一条新交易确认 payer 字段存在
6. 更新 `HANDOVER_2026-11-08.md` 或新建 `HANDOVER_2026-11-12.md` 记录本次改动

> 时间约束：硬冻结线 **11-12 22:00**，本工单须在此之前完成并部署验证。
