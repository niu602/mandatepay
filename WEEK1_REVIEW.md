# MandatePay · 第 1 周复盘（D1–D7）

> 复盘时间：2026-10-22 · 距 Devpost 截止（11/13 04:00 北京时间）22 天
> 数据库后端：**PostgreSQL 16.15**（本周已从 SQLite 切换，见 §4）

---

## 一、D1–D5 完成情况清单

| # | 任务 | 状态 | 验证方式（可复现命令） |
|---|---|---|---|
| **D1** | 骨架能启动 + 沙箱完整支付闭环 | ✅ 完成 | `uvicorn app.main:app --port 8888`；沙箱订单 `6S3278047H2709334` + 捕获 `58M77740LJ042791Y` 均 `COMPLETED`，$10.00 |
| **D1** | `paypal-agent-toolkit` 安装并跑通官方示例 | ✅ 完成 | `pip show paypal-agent-toolkit` → 1.1.0；`agent/paypal_tools.py` 已封装 `create_order` / `capture_order` / `get_order_details` |
| **D2** | LangGraph 五节点状态机 parse→validate→approve→pay→verify | ✅ 完成 | `python -m agent.demo` → 4 个场景全部流转，输出 `PARSED_CONSTRAINT / AUTO_APPROVE / REQUIRE_HUMAN / REJECT` |
| **D2** | Constraint API + 校验逻辑（预算/白名单/阈值） | ✅ 完成 | `POST /api/agent/constraints` + `GET /api/constraints/current`；`agent/constraints.py` 纯函数 `evaluate()` |
| **D3** | DeepSeek function calling 意图解析 | ⚠️ 部分完成 | 代码完成（`agent/llm_intent.py`，两个 tool：`set_constraint` / `create_purchase_request`），**但 `.env` 无 `DEEPSEEK_API_KEY`，实际走规则降级**；降级路径已验证：`python -m agent.demo` 全绿 |
| **D3** | Validate 节点接入约束校验 | ✅ 完成 | `POST /api/agent/requests` 三分支：Slack $15 → `REJECT`（供应商不在白名单） |
| **D4** | Pay 节点接入 Toolkit 真实下单（替换 mock） | ✅ 完成 | 实测真实沙箱订单：`6DR397836S494300J`($10)、`0PJ1096431513723S`($80)、`7B479478V9647700F`($8)，均返回 `PAYER_ACTION_REQUIRED` + approval_url |
| **D4** | Approve 节点双分支（自动通过 / 需审批） | ✅ 完成 | 小额 → `APPROVED` 直接进 pay；大额 → `PENDING_HUMAN` 生成审批记录，不扣款 |
| **D5** | 三层 Mandate 审计日志（Intent / Cart / Payment） | ✅ 完成 | `GET /api/mandates`；前端 `/audit` 页分层展示；当前 PG 中 23 条 |
| **D5** | 3 个硬编码场景端到端跑通 | ✅ 完成 | 见下方 §2 三场景实测表 |

### D6（本周额外完成）

| 任务 | 状态 | 验证方式 |
|---|---|---|
| 公网隧道 | ✅ 完成 | `tmole 8888` → `https://uankjk-ip-27-187-171-194.tunnelmole.net`；公网 `GET /api/health` 返回 200 |
| Webhook 注册 + 订阅 4 事件 | ✅ 完成 | `python scripts/setup_webhook.py --list` → id `4PM36159TK561873M`，订阅 `PAYMENT.CAPTURE.COMPLETED` / `CHECKOUT.ORDER.APPROVED` / `PAYMENT.CAPTURE.DENIED` / `PAYMENT.CAPTURE.PENDING` |
| 签名校验 | ✅ 完成 | 伪造签名 → `401`；缺 transmission 头 → `400`；未配 webhook_id → `500` |
| 事件处理 + 幂等 | ✅ 完成 | 模拟 `PAYMENT.CAPTURE.COMPLETED` 经公网投递 → mandate 置 `COMPLETED`；重复 event_id → `duplicate:true` 跳过 |

---

## 二、三场景实测（PostgreSQL 后端，2026-10-22）

| 场景 | 输入 | 决策 | Mandate 状态 | 真实 PayPal 订单 |
|---|---|---|---|---|
| ① 小额自动通过 | Notion / $10 | `AUTO_APPROVE` | `APPROVED` | `6DR397836S494300J` |
| ② 大额需审批 | Figma / $80（超 $50 阈值） | `REQUIRE_HUMAN` | `PENDING_HUMAN` | `0PJ1096431513723S` |
| ③ 越界拒绝 | Slack / $15（不在白名单） | `REJECT` | `REJECTED` | 未创建（未扣款） |

当前 PG 数据：constraints 6 · purchase_requests 23 · mandates 23（COMPLETED 1 / APPROVED 8 / PENDING_HUMAN 7 / REJECTED 7）· webhook_events 1

---

## 三、阻塞项

### 🔴 阻塞 1：capture 闭环仍需人工点确认（**产品安全设计，非缺陷**）

- **现象**：真实订单创建后状态是 `PAYER_ACTION_REQUIRED`，必须买家去 approval_url 登录沙箱付款后才能 capture。
- **原因**：PayPal agentic commerce 的范式就是「人授权范围 + Agent 在范围内行动」。MandatePay 的命题恰恰是「AI 不能偷偷花钱」，全自动扣款与核心叙事直接冲突。
- **决策**：**不改**，不做浏览器自动化（此前已踩过沙箱短信验证码死路）。
- **下一步**：Demo 视频里把这步讲成产品卖点——「Agent 只负责在约束内下单，最终扣款必须由人点确认」。前端 `/audit` 已提供「去确认付款」+「我已付款·捕获」按钮。

### 🟡 阻塞 2：DeepSeek API Key 未配置

- **现象**：`.env` 无 `DEEPSEEK_API_KEY`，意图解析走规则降级。
- **影响**：复杂句式（如「每月给 Notion 500 但超过 100 要问我」）可能抽错金额顺序；但不影响三场景演示。
- **下一步**：配一个 Key（几十块钱够用），或明确接受规则解析并在 README 里说明「已实现 LLM 解析，因未配置 Key 默认走规则降级」。

### 🟡 阻塞 3：隧道工具非 ngrok，且 URL 每次重启会变

- **现象**：`ngrok.com` 在本网络不可达（下载不到二进制），改用 `tunnelmole`（npm）。免费版 URL 每次启动随机变化。
- **影响**：隧道重启后必须重新注册 Webhook（URL 变了），否则 PayPal 打不进来。
- **下一步**：`python scripts/setup_webhook.py --create --url <新URL> --force` 一条命令重注册；或付费/自托管固定子域名。录 Demo 前务必确认隧道与 webhook 一致。

### 🟢 阻塞 4（技术栈偏差，已解决）：数据库

- v1 计划锁定 PostgreSQL，上一轮实际用了 SQLite。**本周已切换完成**，见 §4。

---

## 四、数据库切换说明（SQLite → PostgreSQL）

### 切换方式

通过 `DATABASE_URL` 环境变量切换，**业务代码零改动**：

```bash
# PostgreSQL（当前生效）
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/mandatepay

# SQLite（留空即可回退，零配置）
DATABASE_URL=
```

新增文件：
- `scripts/init_db.py` —— 幂等建表（两张 schema：`_SQLITE_SCHEMA` / `_PG_SCHEMA` 自动按后端选择）
- `scripts/migrate_sqlite_to_pg.py` —— 数据迁移

### 数据迁移处理方式

1. **只追加、不覆盖**：目标表有数据时默认跳过，`--force` 才清空重灌。
2. **唯一键去重**：`mandates` 按 `mandate_uuid`、`webhook_events` 按 `event_id` 做 `ON CONFLICT DO NOTHING`，**重复执行安全**。
3. **源库不删**：`mandatepay.db` 保留为备份。
4. **行数对账**：迁移脚本结束后打印源/目标行数。

本次迁移结果：constraints 4 行 · purchase_requests 16 行 · mandates 16 行，全部迁入，源 SQLite 文件保留。

### 本地启动方式

```bash
# 1) 起 PostgreSQL（已装 16.15，Windows 服务名 postgresql-x64-16）
#    用 Docker 亦可：docker run --name mandatepay-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16
# 2) 建库
createdb -U postgres -h 127.0.0.1 mandatepay
# 3) 建表
python scripts/init_db.py
# 4) 起服务
uvicorn app.main:app --port 8888
# 5) 确认后端
curl http://127.0.0.1:8888/api/health   # db_backend 应为 postgresql
```

### 回归结论

D1–D5 关键路径在 PG 上**全部跑通，无回归**：设定约束 → 当前约束 → 三分支决策（均生成真实订单）→ 审计日志落盘 → 状态机 4 场景 → `/` 与 `/audit` 页面 200。

---

## 五、下一周建议优先级

1. **Demo 录屏脚本**（D18-D20 前移）——现在功能已够录，别等完美。3 分钟以内，讲清「约束设定 → 小额自动 → 大额拦住 → 审计日志」。
2. **补 DeepSeek Key** 或明确降级说明。
3. **MCP Server 集成**（P1 冲奖）——README 里写「已调研」，有余力再实现。
4. **Render 部署**前把 PG 连接串换成托管实例（只改 `DATABASE_URL`，代码不用动）。
5. **11/11 提交**（截止前 2 天），Devpost 拥堵是历史常态。
