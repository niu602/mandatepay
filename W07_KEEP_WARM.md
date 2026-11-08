# W-07 · 线上预热与监控（B1 + Always On 口径）

> 目标：让评审期（12-01 ~ 12-15）评委打开页面是秒开，并且**宕机时有人知道**。
> 配套代码：`app/main.py` 的 `/healthz` + `scripts/keep_warm.py`。
>
> ⚠️ **2026-11-08 修订：线上是 B1，不是 F1；不需要"保温"。**
> 原文档按 F1 写的（F1 无 Always On、60 CPU 分钟/天硬上限），线上实际是
> **B1 + Always On**。下面的机制说明已按实际配置更正。

---

## 一、Always On 已经解决了"保温"这件事

| 前提 | 事实 | 后果 |
|---|---|---|
| 线上 SKU 是 **B1**（不是 F1） | B1 支持 Always On | 平台自己每 5 分钟 ping 一次 → 实例常驻不卸载 |
| **Always On = On** | Azure 官方：*"the front-end load balancer sends a GET request to the application root every five minutes. The continuous ping prevents the app from being unloaded."* | 评委什么时候来都是热实例 |
| B1 **没有** 60 CPU 分钟/天硬上限 | 那是 F1 的限制 | 高频 ping 不会"把配额 ping 挂" |
| 但 Azure **仍会重启实例** | 配置变更、平台维护、缩放 | 冷启动实测 **3.143s**（可接受） |

→ **原方案"在自己电脑上挂 15 天 daemon"的前提消失了。**
B1 已经保住了实例，不需要外部探针来保温。

### 那还剩什么要做？

两件事，都很轻：

| 事项 | 目的 | 怎么做 |
|---|---|---|
| **演示前预热** | 让浏览器侧缓存与 JIT 都热起来，评委第一次点开就是秒开 | `python scripts/keep_warm.py --url <URL> --warm`（录制/答辩前 5 分钟跑一次） |
| **外部宕机监控** | 服务挂了你能在评委访问前知道 | UptimeRobot 之类的 SaaS，30 分钟一次打 `/healthz` |

### 为什么监控用 SaaS 而不是本地 daemon

评审期那 15 天，你的电脑会关机、会休眠、会换网络。
**本地进程一定会中断**，而 SaaS 的探针是常驻的。
既然要做监控，就该用不会中断的方案 —— 而且它只是**报警**，不负责保温
（保温已经由 Always On 负责了）。

### UptimeRobot 探针配置（推荐）

| 项 | 值 |
|---|---|
| 类型 | HTTP(s) |
| URL | `https://mandatepay-zisen-dph6eucmf4gjckct.eastasia-01.azurewebsites.net/healthz` |
| 监控间隔 | 30 分钟 |
| 期望状态码 | 200 |
| 超时 | 30 秒 |

> `/healthz` 是**零 DB 零 PayPal** 的存活探针（刻意不 import `agent.store`），
> 一次请求约 36ms，30 分钟一次对 B1 的 CPU 影响可忽略。
>
> 监控项**只配这一个**。不要给 `/` 或 `/api/*` 配监控 ——
> 它们要鉴权（401 是正常的），而且会污染监控数据。

---

## 二、探针的 CPU 占用（参考值）

`scripts/keep_warm.py --budget` 可以自己算这张表：

| 间隔 | 次/天 | 冷启动 CPU 秒 | 热实例 CPU 秒 | 合计/天 | 判定 |
|---|---|---|---|---|---|
| 10 分钟 | 144 | 245 | 28 | 4.56 min | ⚠️ 频率过高（平台自己才 5 分钟一次） |
| 20 分钟 | 72 | 122 | 14 | 2.28 min | ⚠️ 频率过高 |
| **30 分钟** | **48** | **82** | **10** | **1.52 min** | ✅ **采用** |
| 45 分钟 | 32 | 54 | 6 | 1.01 min | ✅ 达标 |
| 60 分钟 | 24 | 41 | 5 | 0.76 min | ✅ 达标 |

**假设（保守）**：冷启动 5 CPU 秒（取实测上界）、热实例 0.3 秒、
1/3 的探针落在平台重启后的冷启动上。

**结论**：30 分钟约 **1.5 CPU 分钟/天**，低于工单 DoD 的"当天 < 10 CPU 分钟"。

> ⚠️ **口径提醒**：B1 没有硬上限，这张表是**参考值**而非红线。
> 但"低频"仍然是对的 —— 平台自己才 5 分钟 ping 一次，
> 你的探针比它还频繁就没有意义了。

---

## 三、怎么用

### 3.1 外部监控（可选，UptimeRobot 之类）

> **线上是 B1 + Always On，平台自己每 5 分钟 ping 一次 —— 不需要保温 daemon。**
> 这一节保留常驻模式的用法，供两种场景：
> ① 换成 F1 之类不支持 Always On 的套餐时；② 想在本机做长时间观察。

**推荐的正式做法是 UptimeRobot**（评审期你的电脑会关机/休眠，
本地进程一定会中断，SaaS 探针才不会）：

| 项 | 值 |
|---|---|
| 类型 | HTTP(s) |
| URL | `https://<你的域名>.azurewebsites.net/healthz` |
| 间隔 | 30 分钟 |
| 期望状态码 | 200 |

它只是**宕机报警**，不负责保温。

**需要本机常驻探针时**（比如换回 F1）：

```bash
# 前台跑（Ctrl-C 退出）
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net

# 后台跑（Windows Git Bash）
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net > /tmp/keepwarm.log 2>&1 &

# 只跑有限次（测试用）
python scripts/keep_warm.py --url https://... --interval 30 --max-pings 5
```

脚本会在启动时打印 CPU 占用估算，间隔低于 30 分钟会给出警告。

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

| 项 | 数据 | 来源 |
|---|---|---|
| 首次构建耗时 | ~2 min | 部署时观察（Oryx 装依赖） |
| **实例重启后首访耗时**（真冷启动） | **3.143s** | `--measure`（11-08 实测） |
| 进程内首访耗时 | 0.709s | `--measure`（11-08 实测） |
| 热实例耗时 | 0.553s ~ 0.717s | `--measure`（11-08 实测） |
| 演示前预热可用 | ✅ | `--warm` 全绿 |
| **外部宕机监控已配** | 待填 | UptimeRobot（30 分钟打 `/healthz`） |
| AG Studio 走 CDN 回退 | ✅ | `--check-cdn` 全绿（vendor 404 → jsDelivr） |

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

**Q：F1 配额超了会怎样？**（当前线上是 B1，无此限制）
F1 才有 60 CPU 分钟/天硬上限 —— 超了当天请求被拒，次日 0 点重置。
线上是 **B1**，没有这个限制；但工单 DoD 仍要求"当天 < 10 CPU 分钟"，
探针间隔保持 30 分钟即可（实测约 1.5 CPU 分钟/天）。

**Q：Always On 开着就完全不会冷启动了吗？**
不是。Always On 防止的是**空闲卸载**，但 Azure 仍会在这些时机重启实例：
配置变更、平台维护、缩放操作、换 plan。
实测 11-08 撞上过一次实例重启，冷启动从 0.709s 变成 3.143s。
所以演示前仍要 `--warm` 预热一次。
