# 第 2 周复盘（10/27–11/02）

> 本周主线从「多 Agent + MCP」调整为「**AI 加深（解释 + 洞察 + 推理）+ 前端**」。
> 理由：多 Agent/MCP 解决的是"架构完整性"，而当前短板是"**AI 程度浅**"——
> AI 只在入口解析了一句话，之后全是确定性规则。做厚 AI 参与度，对评审的
> Innovation 与 Technological Implementation 两项打分更直接，且不动现有架构。

---

## 一、完成情况

| 天 | 任务 | 完成标志 | 状态 |
|---|---|---|---|
| D8 | `explain_node`：约束违反的自然语言解释 | 说"为什么被拒"→ 返回"超出单笔阈值 $30，建议提高阈值或拆分订单" | ✅ 完成 |
| D8 | explain 接入 `/api/agent/parse` 返回 | 返回体多 `explanation` 字段 | ✅ 完成 |
| D9 | 消费洞察：每笔采购前查本月已花费，LLM 生成一句洞察 | "本月已花 $170/$200，这笔 $10 会通过，但只剩 $20 余额" | ✅ 完成 |
| D9 | 前端骨架：约束设定页（自然语言输入 + 结构化规则展示） | 可交互 | ✅ 完成（另加 /budget /approvals /audit 重写） |
| D10 | 异常推理场景：规则解析器做不到的句式 | 至少 2 个 | ✅ 完成（2 个 + 对照组） |
| D10 | 前端：采购请求列表 + 审批弹窗 | 可交互 | ✅ 完成 |
| D11 | 前端：审计日志页（三层 Mandate 完整展示） | 分页 + 按 trace_id 聚合 | ✅ 完成 |
| D11 | 前端：预算仪表盘（本月已用/剩余/预测） | 可视化 | ✅ 完成 |
| D12 | 约束压测 + 失败 case 分类 | 50 约束 × 100 请求，量化数据，≥3 类误拒 | ✅ 完成（650 次判定，4 类问题，已全部修复） |
| D13 | Demo 剧本重写（explain/洞察/推理 三点写进逐字稿） | 文稿定稿 | ✅ 完成（`DEMO_SCRIPT.md`） |
| D14 | 周复盘 + 清理技术债 | 列出未完成项 | ✅ 完成（本文档第三、四节） |

**验证手段一览**

```bash
python -m pytest tests/ -q                    # 30 例单元测试
python scripts/verify_ai_explain.py           # 洞察 + 解释（三场景）
python scripts/verify_reasoning.py            # 异常推理（含规则对照）
python scripts/verify_async_sync.py           # Webhook 异步同步
python scripts/verify_webhook.py --simulate PAYMENT.CAPTURE.COMPLETED
python scripts/stress_constraints.py --seed 42   # 约束压测 → STRESS_REPORT.md
python scripts/setup_catalog.py               # 供应商目录建库
```
页面：`/constraints` `/audit` `/budget` `/approvals`

---

## 二、本周的关键增量

### 1. AI 从"分类器"变成"推理器"（D10）

规则解析器只能做分类（找关键词、抠数字）。两句人类会说的话它直接返回 `None`：

| 句式 | 规则解析器 | MandatePay（多轮 tool loop） |
|---|---|---|
| 「Notion 和 Figma 挑便宜的买」 | ❌ 无法处理 | ✅ 查两家价格 → 选 $10 的 Notion |
| 「先订那个设计工具，预算看着办」 | ❌ 无法处理 | ✅ 目录消歧出 Figma + 沿用当前约束下单 |

实现要点：`agent/llm_intent.py` 新增 `query_catalog` 工具 + **多轮 tool loop**
（模型可先查目录拿数据，把结果作为 ToolMessage 喂回去再决策），
并把「当前生效约束」注入 system prompt，使"预算看着办"有据可依而非编造。
返回体带 `reasoning`（模型推理文本）与 `tool_trace`（工具轨迹）——**AI 的思考过程在 API 里可见**。

### 2. AI 参与每一次决策，而不只是入口（D8/D9）

状态机从五节点扩到**七节点**：

```
parse → validate → insight → explain → approve → pay → verify
                        ↑ LLM    ↑ LLM
```

`insight_node` 每笔采购前算预算处境；`explain_node` 把判定翻译成可行动建议。
两者均有**纯模板兜底**（无 Key/调用失败也返回可读文案），demo 永不断链。

### 3. 压测发现问题 → 修复问题（D12，最有说服力的一块）

压测**不以引擎自身为标准答案**（那等于没测），而是维护三份独立参照实现：

| 口径 | 一致率 | 误拒 | 漏放 | 严重度错判 |
|---|---|---|---|---|
| 当前引擎 vs 引擎规格（检验实现对不对） | **100.00%** | 0 | 0 | 0 |
| 修复前语义 vs 用户直觉（用户此前遭遇什么） | 63.54% | 58 (8.92%) | 170 (26.15%) | 9 (1.38%) |
| 当前引擎 vs 用户直觉（修复后还剩什么） | 62.31% | **0** | **0** | 245（有意设计折中） |

发现并修复 **4 类**问题：

| 类别 | 例数 | 修复 |
|---|---|---|
| A. 供应商大小写/空格误拒 | 18 | 白名单匹配**归一化**（去空格 + 忽略大小写） |
| C. 品类大小写误拒 | 38 | 同上 |
| F. 品类未知时静默放行 | 170 | 改 `REQUIRE_HUMAN`：既不静默放行，也不硬阻断 |
| H. 浮点尾数边界错判 | 3 | 金额**按分取整**后再比较 |

> F 类是**真·安全漏洞**：约束限定了品类，但请求没带品类字段 → 品类校验被静默跳过，
> 约束形同虚设。压测不自己测自己，就能发现这种"测试全绿但约束没生效"的问题。

### 4. 前端四页成体系（D10/D11）

`/constraints`（约束设定 + 洞察解释演示）· `/approvals`（审批台 + 审批弹窗）·
`/audit`（三层 Mandate 可视化 + 交易粒度分页）· `/budget`（已用/剩余/月末预测/供应商分布）

---

## 三、技术债清单（未完成 / 待补）

| # | 项 | 影响 | 优先级 | 预估 |
|---|---|---|---|---|
| 1 | **MCP Server 未集成** | 计划内后移；Devpost "技术深度"叙事少一块 | 中 | 1 天 |
| 2 | **多 Agent 拆分未做** | 目前是单图七节点状态机；多 Agent 协作叙事缺失 | 中 | 2 天 |
| 3 | ~~**LLM 判定稳定性未压测**~~ | ✅ **已补**（W-05）：20 句 × 3 次 = 60 次运行，判定正确率 60/60、稳定性 20/20（三次完全一致）、降级 0 次。断言集在 `scripts/verify_llm_zai.py`（期望结果人工枚举固化），报告见 `LLM_INTENT_VERIFICATION.md`。原 6 句样本量太小的问题一并解决 | ~~ | ~~ |
| 4 | **insight/explain 未做缓存** | 每笔采购 3 次 LLM 调用，Demo 录制有等待感 | 中 | 0.5 天 |
| 5 | **支付成功金额未回流到 mandate 审计** | `amount_usd` 来自请求值，若实际扣款金额不同（如手续费）审计链不反映 | 低 | 0.5 天 |
| 6 | **`/api/agent/requests`（结构化入口）不跑 insight/explain** | 绕过状态机，缺 AI 输出；前端若改用它就没了 AI 感 | 中 | 0.5 天 |
| 7 | **前端无错误边界/加载态统一** | 各页各写一套，风格易漂移 | 低 | 1 天 |
| 8 | **`vendor_whitelist` 仍是精确名匹配** | 已归一化，但"Notion"与"Notion Pro"仍是两个供应商 | 低 | — |
| 9 | ~~**月度预算只统计 COMPLETED**~~ | ✅ **已修**：改为「已承诺支出」口径（下单即计入），否则 Agent 能连下多笔待支付订单绕过预算；仪表盘同时给出已到账/待支付拆分 | ~~ | ~~ |
| 10 | **SQLite / PostgreSQL 双后端只测了 PG** | 迁移脚本写了但 SQLite 路径未跑过完整回归 | 低 | 0.5 天 |
| 11 | **Demo 依赖数据清场** | 预算按"已承诺"口径后，测试期订单会占满预算 → 录制前必须跑 `reset_demo_data.py --confirm` | **高** | 5 分钟 |
| 12 | **所有 `/api/*` 端点无鉴权** | `app/main.py` 无认证中间件，`/api/mandates` 返回全部交易数据。Demo 本地跑可接受，**上线前必须补** | **高** | 1 天 |
| 13 | **access_token 无缓存** | 两套封装都是每次 API 调用换一次 token（多一次 OAuth 往返）。Demo 无影响，生产需加 | 中 | 0.5 天 |
| 14 | **两套 PayPal 封装并存** | 部分冗余但**不可替代**：Toolkit 没有 webhook 验签 / webhook 管理 / 测试卡直付三项能力，裸 REST 是唯一实现。真实成本是两套 API 语义漂移的风险由人承担 | 中 | 收敛到统一 client 层 |
| 15 | **`approval_url` 未落库** | `mandates` 表无该列 → `/approvals`、`/audit` 用硬编码 `checkoutnow?token={order_id}` 重建。**实测 PayPal 自己返回的 payer-action 就是这个格式**，故风险低，但仍是技术债 | 低 | 加一列即可 |
| 16 | **quantity>1 时金额漂移** | Toolkit 只用 `item_cost` 重算总额，单价先四舍五入到分再乘数量会漂移（$10 ×3 → 实收 $9.99）。当前全部调用点 quantity=1 故精确 | 低 | 按分分配余数 |
| 17 | **`PAYPAL_MODE=live` 无部署路径** | 该变量目前只用于选 base URL 与 SKIP_VERIFY 护栏，代码里没有真正的 live 部署脚本 | 中 | 0.5 天 |

### 架构评审额外发现并已修复的（2026-10-26）

| 问题 | 影响 | 修复 |
|---|---|---|
| **Toolkit `capture_order` 返回包装结构** `{message,status,amount,raw}`，我们直接读顶层 `purchase_units` → **capture_id 恒为 None** | 三层 Mandate 审计链丢失捕获 id；**恰好在 Demo「我已付款·捕获」高光时刻** | 改为从 `raw` 取（兼容未包装结构）；新增 `tests/test_paypal_tools.py` 6 例锁住 |
| **`items[].item_total` 传了被忽略的值** | Toolkit 内部按 `item_cost×quantity` 重算总额，两边数字打架 | 传值对齐其重算结果；补注释说明 schema 必填 + 实际不被使用 |

**建议下一步优先级**：#3（LLM 稳定性数据）→ #11（录前清场）→ #1 MCP。
Demo 录制本身 + #11 就够了，其余可留到提交后。

---

## 四、给下一周（D10–D14 之外）的建议

1. **先录 Demo，再补功能**。功能已经够厚（七节点 + 三层审计 + 四页前端 + 压测数据），
   继续做 MCP / 多 Agent 是边际收益递减，而 Demo 视频是**硬性提交物**。
2. **录前必做两件事**：① `reset_demo_data.py --confirm` 清场（否则预算被测试订单占满）；
   ② 确认约束句不会把 `category_whitelist` 设成非空（"SaaS 预算"≠"只许买 SaaS"，
   已在 prompt 里区分）。
3. **LLM 稳定性数据要补**（#3）：现在"输出稳定 JSON"只测过 6 句。
   50 句 × 3 次能给出可引用的数字，比任何形容词都有说服力。
