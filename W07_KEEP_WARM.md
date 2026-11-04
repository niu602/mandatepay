# W-07 · Azure F1 保温与预热操作手册

> 目标：让评审期（12-01 ~ 12-15）评委打开页面是秒开，且**不把F1 的CPU 配额 ping 挂**。
> 配套代码：`app/main.py` 的 `/healthz` + `scripts/keep_warm.py`。

---

## 一、为什么需要这件事（三个前提叠在一起）

| 前提 | 事实 | 后果 |
|---|---|---|
| Azure App Service **F1 不支持 Always On** | 闲置后实例可能被平台卸载 | 下次访问走冷启动，实测 2-5 CPU 秒 |
| F1 有 **60 CPU 分钟/天硬上限** | 超了当天请求被拒 | 高频 ping 可能把服务 ping 挂 |
| 评审期评委**集中访问** | 短时间流量突增 | 首访慢 =第一印象差 |

→ 所以策略是**低频保温 + 演示前预热**，两件事各管一头：
保温解决"评委什么时候来都热"，预热解决"评委第一次点开就秒开"。

---

## 二、CPU 预算：间隔定30 分钟的依据

`scripts/keep_warm.py --budget` 可以自己算这张表：

| 间隔 | 次/天 | 冷启动 CPU 秒 | 热实例 CPU 秒 | 合计/天 | 占 60 分钟预算 | 判定 |
|---|---|---|---|---|---|---|
| 10 分钟 | 144 | 245 | 28 | 4.56 min | 7.6% | ⚠️ 频率过高（v1/v2 作废的方案） |
| 20 分钟 | 72 | 122 | 14 | 2.28 min | 3.8% | ⚠️ 频率过高 |
| **30 分钟** | **48** | **82** | **10** | **1.52 min** | **2.5%** | ✅ **采用** |
| 45 分钟 | 32 | 54 | 6 | 1.01 min | 1.7% | ✅ 达标 |
| 60 分钟 | 24 | 41 | 5 | 0.76 min | 1.3% | ✅ 达标 |
| 120 分钟 | 12 | 20 | 2 | 0.38 min | 0.6% | ✅ 达标 |

**假设（保守）**：冷启动 5 CPU 秒（取实测 2-5 的上界）、热实例 0.3 秒、
1/3 的探针落在实例被卸载后的冷启动上。

**结论**：30 分钟约 **1.5 CPU 分钟/天**，占预算 2.5%，
远低于工单 DoD 的"当天 < 10 CPU 分钟"，余量充足。

> ⚠️ **一个容易被忽略的点**：10 分钟间隔其实也只占 7.6%，
> 单看数字并不会超预算。工单仍把它作废，理由不是算不过来，而是：
> **F1 不支持 Always On，实例卸载的时机不可控**。
> 万一某天平台调整卸载策略（更激进地回收实例），10 分钟间隔的探针
> 会更频繁地落在冷启动上，成本是线性放大 —— 而我们对这个变量没有控制力。
> 30 分钟是在"够用"和"抗变化"之间的稳妥点。

---

## 三、怎么用

### 3.1 保温（常驻，跑在你自己的机器上）

> ⚠️ **探针必须跑在 F1 实例外面**。
> 如果探针跑在同一个实例里，实例被卸载时探针一起没了 —— 根本起不到保温作用。
> 所以要在**本地机器**或任何常开设备上跑。

```bash
# 前台跑（Ctrl-C 退出）
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net

# 后台跑（Windows Git Bash）
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net > /tmp/keepwarm.log 2>&1 &

# 只跑有限次（测试用）
python scripts/keep_warm.py --url https://... --interval 30 --max-pings 5
```

脚本会在启动时打印 CPU 预算估算，间隔低于 30 分钟会给出警告。

### 3.2 演示前预热（录制 / 答辩前 5 分钟跑一次）

```bash
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net --warm
```

它按 `/healthz` → `/` → `/constraints` → `/approvals` → `/audit` → `/budget`
顺序打一遍，输出每条的耗时。**目的不只是唤醒实例** ——
还要让浏览器侧缓存与 JIT 都热起来，评委第一次点开时才是秒开。

`/budget` 如果失败会明确提示：评委看到的会是 classic 回退视图
（功能正常，但不是 AG Studio 渲染）—— 录制前要知道这件事。

### 3.3 冷启动耗时测量（DoD 验收）

```bash
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net --measure
```

连打 3 次 `/healthz`，输出「首访耗时」与「热实例耗时」的对比，
并写入 `cold_start_measure.json` 供填进验收记录。

DoD 要两个数据点：**首访**（可能命中冷启动）与**唤醒后**（热实例）。

### 3.4 部署后自检：确认 AG Studio 走 CDN

```bash
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net --check-cdn
```

检查两件事：
1. `/static/vendor/ag-studio.mjs` 是否可达 —— 期望 **404**
2. `/budget` 页面是否含 CDN 回退标记 —— 期望 **有**

**为什么必须查**：`static/vendor/` 是 7MB 的 npm 商业包，已 gitignore，
平台构建**不会**装它 → 页面会自动回退 jsdelivr CDN。
但如果 vendor 真被挂上去，**7MB × 访问次数**会很快吃掉 **165MB 带宽/天**上限
→ 评委看到的是"加载半天"或直接白屏。

> 本地跑这个检查会报"未通过"（本地确实挂载了 vendor）—— **这是正确行为**，
> 说明检查逻辑有效。线上才会通过。

### 3.5 代理陷阱（实测踩到）

`urllib` 默认读环境变量 `http_proxy` / `https_proxy`。
开发机设了代理时，本地探针会被代理拦成 502，**误判成"服务已死"**。

脚本默认**绕过代理**（`ProxyHandler({})`）。真需要走代理时加 `--use-proxy`。

---

## 四、`/healthz` vs `/api/health`

| | `/healthz` | `/api/health` |
|---|---|---|
| 查库 | ❌ 不查 | ❌ 不查（只读 DSN 字符串判断后端类型） |
| 调 PayPal | ❌ 不调 | ❌ 不调 |
| import `agent.store` | ❌ **不 import** | ✅ import（拉起 psycopg / dotenv） |
| 返回体 | `{"status":"ok"}` | 6 个字段（含 paypal_mode / db_backend / 订阅事件） |
| 用途 | 存活探针、平台健康检查、保温 | 人工排查配置 |
| 本地实测 | 36ms | 略慢（多一次模块链） |

**为什么不用 `/api/health` 做健康检查**：它会 import `agent.store`，
那一串导入链要拉起 psycopg 等模块 —— 对"服务是否活着"是纯开销。
F1 只有 0.1 CPU，低频探针也要尽量轻。

**未来加鉴权时**：`/healthz` 会与页面路由一起被排除在鉴权之外 ——
探针不能带凭据。

---

## 五、验收记录（DoD）

| 项 | 数据 | 何时填 |
|---|---|---|
| 首次构建耗时 | 待填 | 部署完成后 |
| **首访耗时**（可能命中冷启动） | 待填 | `--measure` 输出 |
| **唤醒后耗时**（热实例） | 待填 | `--measure` 输出 |
| 演示前预热可用 | 待填 | `--warm` 全绿 |
| 配额页面当天用量 < 10 CPU 分钟 | 待填 | Azure 门户 → 配额 |
| AG Studio 走 CDN 回退 | 待填 | `--check-cdn` 全绿 |

> 部署完成后把 `--measure` 与 `--check-cdn` 的实际输出贴进这里，
> 并同步更新 `W06_DEPLOY.md` §六 的冷启动耗时表。

---

## 六、常见问题

**Q：本地跑 `--check-cdn` 报"未通过"，是不是脚本坏了？**
不是。本地 `static/vendor/` 确实存在（AG Studio 的 npm 包已装），
所以 200 是正确的。线上因为 gitignore 不会装 → 404 → 通过。
这个检查的语义是"线上不该有 vendor"，本地有 vendor 恰好证明检查有效。

**Q：Windows 上后台跑保温脚本怎么不弹黑窗？**
```bash
pythonw scripts/keep_warm.py --url https://... > /tmp/keepwarm.log 2>&1 &
```
或用 `nohup`（Git Bash）。

**Q：保温脚本要不要也监控 /budget？**
不要。`/healthz` 足够判断"实例活着"，而打 `/budget` 会触发
AG Studio 的 CDN 请求（首屏 3.8MB）—— 那是**访客的**带宽，
不是我们的，但我们没必要替评委预热 CDN。

**Q：F1 配额超了会怎样？**
当天剩余请求被拒（服务返回错误），次日 0 点重置。
所以 DoD 的"当天 < 10 CPU 分钟"是硬要求 —— 留 6 倍余量。
