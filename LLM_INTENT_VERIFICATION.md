# 意图解析验证报告 · 20 句 × 3 次（Z.ai `glm-4.7-flash`）

> 执行时间：2026-11-01
> 端点：`https://api.z.ai/api/paas/v4`　模型：`glm-4.7-flash`　思维链：**disabled**
> 断言集：`scripts/verify_llm_zai.py` 的 `CASES` 表（**期望结果人工枚举并固化**，非模型自评）
> 背景见 [W05_ZAI_MIGRATION.md](W05_ZAI_MIGRATION.md)

---

## 一、结论

| 指标 | 结果 |
|---|---|
| 用例数 | 20 句 × 3 次 = **60 次运行** |
| **判定正确率** | **60/60（100%）** —— 逐句全对 **20/20** |
| **判定稳定性** | **20/20 句三次完全一致**（无偶发跳变） |
| 字段匹配（供应商 / 金额） | **20/20 句** |
| **降级次数（LLM 未生效）** | **0 / 60 次** ✅ |
| 因端点 429 过载重跑 | 1 次（端点侧容量问题，非模型缺陷，已如实上报） |

**脚本退出码 0（全部通过）。**

三场景覆盖：AUTO_APPROVE 10 句、REQUIRE_HUMAN 5 句、REJECT 5 句。

### 对照组：thinking 开启（`--thinking enabled --repeat 1`）

同一套 20 句断言集，thinking 开启时 **20/20 判定正确、稳定性一致、降级 0/20**
—— **thinking 开关不影响 function calling 的判定质量**。

token 实测（同一句，`max_tokens=200`）：

| 模式 | reasoning_tokens | completion | total |
|---|---|---|---|
| disabled | **0** | 142 | 329 |
| enabled | **83** | 124 | 311 |

> 反直觉发现：关闭思维链后 `reasoning_tokens` 归零，但**总 completion 反而略高**
> （142 vs 124）—— 模型把省下的预算补到了回答内容里。
> 工单情报里 "13→2" 是纯对话场景的数据，**function calling 场景下这个收益不存在**。
> 详见 [W05_ZAI_MIGRATION.md](W05_ZAI_MIGRATION.md) §5.2。

---

## 二、验证表格

固定约束：月度预算 $200 / 单笔阈值 $50 / 日累计阈值不限 / 供应商白名单 [Notion, Figma] / 品类白名单空
固定花费快照：monthly=0, daily=0（不读 DB，保证可复现）
判定由生产引擎 `agent/constraints.py` 的 `evaluate()` 直接给出（不做二次实现）

| # | 测试句 | 期望判定 | 实际判定（×3） | 供应商 | 金额 | 稳定性 | 降级 | 说明 |
|---|---|---|---|---|---|---|---|---|
| 1 | 订阅 Notion Pro，10 美元每月 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Notion | $10.00 | ✅ 一致 | 0 | 标准订阅句 |
| 2 | 买 Figma Professional，15 美元一个月 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Figma | $15.00 | ✅ 一致 | 0 | 标准订阅句 |
| 3 | 帮我订阅 Notion Plus，10 美元/月 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Notion | $10.00 | ✅ 一致 | 0 | 斜杠写法 |
| 4 | 订 Figma，15 美元每月 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Figma | $15.00 | ✅ 一致 | 0 | 省略商品名 |
| 5 | 买 Notion，50 美元 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Notion | $50.00 | ✅ 一致 | 0 | 边界：等于阈值不触发人工确认 |
| 6 | Notion 和 Figma 挑便宜的买 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Notion | $10.00 | ✅ 一致 | 0 | 推理：须查目录比价，选 $10 的 Notion |
| 7 | 先订那个设计工具 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Figma | $15.00 | ✅ 一致 | 0 | 推理：须查目录消歧，Figma=design tool |
| 8 | 买 Notion Pro，10 美元，月付 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Notion | $10.00 | ✅ 一致 | 0 | 末尾补充计费周期 |
| 9 | 购买 Figma，15 美元每月 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Figma | $15.00 | ✅ 一致 | 0 | 同义动词 |
| 10 | 订阅 Notion Plus，10美元 | AUTO_APPROVE | AUTO_APPROVE / AUTO_APPROVE / AUTO_APPROVE | Notion | $10.00 | ✅ 一致 | 0 | 金额无空格 |
| 11 | 订阅 Figma 企业版，80 美元每月 | REQUIRE_HUMAN | REQUIRE_HUMAN / REQUIRE_HUMAN / REQUIRE_HUMAN | Figma | $80.00 | ✅ 一致 | 0 | 超阈值但未超预算 |
| 12 | 买 Notion 企业版，120 美元 | REQUIRE_HUMAN | REQUIRE_HUMAN / REQUIRE_HUMAN / REQUIRE_HUMAN | Notion | $120.00 | ✅ 一致 | 0 | 超阈值但未超预算 |
| 13 | 订 Figma，60 美元一个月 | REQUIRE_HUMAN | REQUIRE_HUMAN / REQUIRE_HUMAN / REQUIRE_HUMAN | Figma | $60.00 | ✅ 一致 | 0 | 略超阈值 |
| 14 | 买 Notion，50.01 美元 | REQUIRE_HUMAN | REQUIRE_HUMAN / REQUIRE_HUMAN / REQUIRE_HUMAN | Notion | $50.01 | ✅ 一致 | 0 | 边界：刚好越过阈值 1 分 |
| 15 | 采购 Notion 团队版，99 美元每月 | REQUIRE_HUMAN | REQUIRE_HUMAN / REQUIRE_HUMAN / REQUIRE_HUMAN | Notion | $99.00 | ✅ 一致 | 0 | 超阈值但未超预算 |
| 16 | 订阅 Slack Pro，15 美元每月 | REJECT | REJECT / REJECT / REJECT | Slack | $15.00 | ✅ 一致 | 0 | 供应商不在白名单 |
| 17 | 买 Zoom Pro，20 美元每月 | REJECT | REJECT / REJECT / REJECT | Zoom | $20.00 | ✅ 一致 | 0 | 供应商不在白名单 |
| 18 | 买 Adobe Photoshop，60 美元每月 | REJECT | REJECT / REJECT / REJECT | Adobe | $60.00 | ✅ 一致 | 0 | 供应商不在白名单（校验顺序早于阈值） |
| 19 | 订阅 Jira Cloud，30 美元 | REJECT | REJECT / REJECT / REJECT | Atlassian | $30.00 | ✅ 一致 | 0 | 供应商不在白名单（识别为 Atlassian/Jira 均可） |
| 20 | 买 Notion 企业版，250 美元 | REJECT | REJECT / REJECT / REJECT | Notion | $250.00 | ✅ 一致 | 0 | 金额超月度预算 $200 |

---

## 三、覆盖的边界与推理场景

| 类型 | 用例 | 验证点 |
|---|---|---|
| 阈值边界 | #5 `买 Notion，50 美元` | 恰好等于阈值 → AUTO_APPROVE（不误升级为人工确认） |
| 阈值边界 | #14 `买 Notion，50.01 美元` | 越过阈值 1 分 → REQUIRE_HUMAN（不漏判） |
| 预算边界 | #20 `买 Notion 企业版，250 美元` | 超月度预算 $200 → REJECT（供应商在白名单但预算优先拦截） |
| 校验顺序 | #18 `买 Adobe Photoshop，60 美元` | 供应商不在白名单 → REJECT（先于阈值判定命中） |
| 推理·比价 | #6 `Notion 和 Figma 挑便宜的买` | 须调 `query_catalog` 拿真实价格 → 选 $10 的 Notion（非 $15 的 Figma） |
| 推理·消歧 | #7 `先订那个设计工具` | 须在目录描述里匹配品类词 → 选 Figma（"Collaborative design tool"），非列表第一个 Notion |
| 金额写法 | #3 / #10 / #12 | `10美元/月`、`10美元`（无空格）、`80 美元每月` 都能正确提取 |
| 供应商识别 | #19 `订阅 Jira Cloud，30 美元` | 模型识别为 Atlassian（Jira 母公司，正确）→ 仍 REJECT |

---

## 四、降级统计口径（为什么这个数字可信）

**降级次数 0** 不是"没统计"，而是三重保障：

1. **计数器在客户端**：`agent/llm_intent.py` 的 `_STATS` 记录
   `calls` / `success` / `rate_limited` / `retries` / `degraded` / `no_tool_call`，
   每次降级 +1 并打 warning 日志。
2. **脚本按增量归因**：用调用前后的计数**差值**判断本次是否降级
   （累计值会把历史过载算到本次头上）。
3. **过载 ≠ 模型错误，严格区分**：
   - 429 重试耗尽 → 归因端点，等 60s 后**重跑**并单独计数上报
   - 端点返回 200 但响应残缺（无 tool_calls，且本次撞过 429）→ 归因端点
   - 端点正常响应但模型确实没调工具 → **归因模型，直接算失败，不给重跑机会**

> 工单要求"验证时统计降级次数，确认不是静默降级" ——
> 上面第三条是关键：**模型真出错时不会被重跑掩盖**。
> 若把过载直接算作模型失败，测的就不是模型；若把过载直接忽略，
> 就等于掩盖了"AI 路径当时确实没生效"。

---

## 五、复现方式

```bash
python scripts/verify_llm_zai.py --repeat 3 --thinking disabled --md LLM_INTENT_VERIFICATION.md
```

- 需要 `.env` 配好 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`
- 免费档过载时单轮可能要 40+ 分钟，脚本**逐句落盘**（`.verify_state.json`），
  中断后重跑自动复用已完成用例，结果累积
- 退出码 0 = 全部通过；非 0 = 存在未通过项
