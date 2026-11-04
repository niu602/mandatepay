# W-06 · Neon + Azure 部署（全免费，面向学生）

> 执行时间：2026-11-01（方案于 11-02 修订四次）　目标：把 MandatePay 部署成公开可访问的 URL，供海外评委直接体验。
>
> **最终方案**：**Neon（数据库）+ Azure App Service F1（应用）**，
> 用 **Azure for Students** 免卡开通 —— 官方明确"学生版无需信用卡"。

---

## 一、平台选型（最终结论 · 第四次修订）

| 方案 | 结论 | 原因 |
|---|---|---|
| **Neon** 免费 PostgreSQL | ✅ **采用** | 免费项目**不会自动删除**（只休眠，唤醒秒级）；提供标准 `postgresql://` 连接串 |
| **Azure App Service F1** + **Azure for Students** | ✅ **采用** | **官方明确学生版"无需信用卡"**；F1 是 always-free（10 应用 / 1GB 内存 / 60 CPU 分钟每天）；额度**按天重置，评审期任何一天都不会失效**；另有 $100/年额度兜底 |
| Render 免费 Web Service | ⚠️ 账号被拦 | 2026 年对**新注册账号**普遍强制 Stripe 验证（$1 预授权），实测最后一步仍被拦 |
| Railway | ⚠️ 注册关闭 | Free 档本身确实免卡（$1/月额度），但**新用户注册入口已关闭** |
| Northflank Sandbox | ⚠️ 也要卡 | 定价页写 "No card needed"，但账单文档明确："**all users must add a payment method** to start creating resources" |
| Render 免费 PostgreSQL | ❌ 不用 | **创建 30 天后删除** —— 11 月建库正好在评审期内失效（R-09） |
| Supabase 免费层 | ❌ 不用 | **7 天无活动即暂停**，需手动恢复 |
| Zeabur 免费档 | ❌ 不用 | **2026-10-24 已宣布停止运营**：12-01 起共享集群暂停所有服务 —— 正好撞上评审期 |
| Hugging Face Spaces | ❌ 不用 | 官方文档：Docker Space **需要付费计划**（免费只保留 Static 与 ZeroGPU Gradio） |
| PythonAnywhere 免费版 | ❌ 不用 | 出站网络**只能访问白名单域名** —— 白名单里没有 `api.z.ai` / `neon.tech`，LLM 调用与数据库连接会双双失败 |
| Cloudflare Containers | ❌ 不用 | 免费额度（25 GiB-hrs 等）是 **Workers Paid $5/月** 附带的，Free 档为 N/A |
| Google Cloud Run | ❌ 不用 | 标准部署要绑卡；AI Studio Starter Tier 免卡但**只能部署 AI Studio 内生成的应用** |
| Oracle Cloud / Fly.io / Koyeb | ❌ 不用 | 均要求绑卡做身份验证 |

### 为什么最终是 Azure（而不是继续找免卡 PaaS）

前三个方案（Render / Railway / Zeabur）都卡在同一个点：**要么要卡，要么注册关闭**。
而 **Azure for Students 是唯一"官方明文免卡 + 面向学生 + 额度充足"的路径**：

> 官方页面（azure.microsoft.com/free/students）：
> **"$100 USD in credits to use within 12 months plus free services, with no credit card required.
> Sign up with your school email."**
>
> 对比普通免费账号的注册要求："A phone number, a credit or debit card" ——
> **学生版明确豁免信用卡**。

你是在校本科生，这个方案天生匹配。三个关键优势：

1. **额度按天重置，永不失效** —— App Service F1 是 always-free
   （不像 Render 免费 PG 30 天删除、Zeabur 12-01 停服）
2. **不会在评审期突然停服** —— 学生版不绑卡，额度用完只会停服务，不会产生账单
3. **$100/年额度** —— 就算 F1 的 60 CPU 分钟/天不够，还有真金白银的额度兜底

### Neon 连接串（已验证可用）

```
postgresql://neondb_owner:***@ep-soft-sky-arct6yay-pooler.c-4.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

实测：连接成功（PostgreSQL 18.6）、六张表已建、约束已写入、目录已同步、已清场。
注意连接串用的是 **pooler 端点**（`-pooler.`），Neon 的连接池对 serverless 部署更友好。

---

## 二、方案修订过程（三次走错的记录）

工单原本指定 Render。执行中连续遇到"必须绑卡""注册关闭"的硬门槛，逐一查证后结论如下。

### 2.1 坎一：Render 的 Stripe "Add Card" 弹窗

**现象**：点 Blueprint → 弹出「Add Card — Add credit card to verify your identity」，
Stripe 托管，`$1 USD 临时授权不扣费`，右上角只有 ×，**关闭后无法继续部署**。

**第一次判断（错）**：以为必须绑卡 → 改用 Zeabur。

**第二次判断（也错）**：查到 Render 官方 FAQ 说免卡（"不加卡，免费实例照样能用"）→
以为改走"手动创建 Web Service"（非 Blueprint）即可绕过 → 让用户去试。

**实测结论（对）**：用户按手动路径走到**最后一步 "Deploy Web Service" 时仍被要求填卡**。
参考 FreeTier.co 2026-08 的核实记录：Render 免费档"**注册时需要信用卡**"。
官方文章与实际体验的差异 → Render 对**新注册账号**普遍启用了 Stripe 身份验证。
这是账号级风控，不是某条部署路径的问题。

> **教训**：官方文档说"免卡"不等于新账号一定能免。
> 选型时必须查**用户实测记录**（FreeTier.co / Reddit / 社区），而不只看平台自己的宣传。

### 2.2 坎二：Railway 注册关闭

查证后确认 Railway Free 档本身确实免卡（$0/月含 $1 使用额度、always-on、无休眠），
但**新用户注册入口已关闭** —— 免卡平台在 2026 年普遍收紧。

同时查证了 Northflank Sandbox（$20/月免费额度、always-on、2 个服务 —— 额度上最优越），
但其账单文档明确写道：

> "all users must add a payment method to start creating resources on Northflank,
> **regardless of plan selection**. This is to verify user identity, and prevent malicious usage."

即定价页的 "No card needed" 与账单文档矛盾，**实际仍需绑卡**。

### 2.3 坎三：银联卡能不能绑？（用户提问，已查证）

**能，但有条件。**（据 Stripe 官方文档与多方实测）

| 卡种 | 可行性 | 说明 |
|---|---|---|
| **境内银行双标卡**（带 Visa/Mastercard 标识） | ✅ 可尝试 | Stripe 优先走 Visa/Master 通道。实测初始通过率约 45%，大额扣款降到 15%；**$1 预授权金额小，通过率明显高于大额扣款** |
| **纯银联单标卡**（BIN 以 62 开头） | ❌ 基本不行 | Stripe 官方文档：中国银联（CUP）在线支付**仅支持特定账户地区**（美/英/加/澳/新/港/新马/瑞士/EEA），中国大陆不在列；实测单标卡绑定成功率不足 12% |
| 香港中资银行实体卡 | ✅ 通过率更高（70%） | — |

**如果仍想绑卡**，四个必须先做好的准备（否则必被拒）：

1. 银行 App 里开通**国际线上支付 + 无卡交易 + 自动扣款**三项开关（国内卡默认关闭）
2. 账单地址**填卡的实际发卡地**，邮编要与银行登记一致（填海外地址直接触发风控）
3. 用**双标卡**（有 Visa/Master 标识），不是纯银联单标
4. **IP 与账单地址地区保持一致**（人在国内就用国内地址，别配海外地址）

> 但既然学生版 Azure 免卡，**最省事的做法是根本不绑卡**。

### 2.4 排除的其他平台（避免后来者重走）

**Zeabur —— 已停运**（官方公告 zeabur.com/blogs/zeabur-is-shutting-down）：

> "12 月 1 日起，共享集群：**暂停所有正在运行的服务**。你仍然可以手动恢复服务运行，以便进行数据备份。
> 12 月 31 日：所有独立服务器到期关闭，所有共享集群停止服务。"

评审期是 12-01 ~ 12-15 —— **服务在评审第一天就被暂停**，比 Render 免费 PG 的 30 天删除更致命。

**Hugging Face Spaces —— Docker Space 现已需要付费计划**（官方文档）：

> "Static Spaces are free for everyone.
> **Gradio and Docker Spaces run on compute and require a paid plan to create.**"

**PythonAnywhere —— 出站白名单挡住关键依赖**（官方 whitelist 页面）：
免费账号出站只能访问白名单域名，白名单里**没有 `api.z.ai`（Z.ai LLM）也没有 `neon.tech`（Neon 数据库）**
→ LLM 调用与数据库连接双双失败。另有 100 秒/天的 CPU 限制、免费应用每 3 个月需手动续期。

**Cloudflare Containers —— 免费额度挂在付费档上**（官方定价页）：
> "Memory | **Free** N/A | Workers Paid 25 GiB-hours/month included"

**Google Cloud Run —— Starter Tier 不支持部署我们的仓库**：
AI Studio Starter Tier 确实免卡免账号（Google I/O 2026 公告："no credit card, no Google Cloud account"），
但它**只能用 prompt 在 AI Studio 里生成应用**，无法部署我们已有的 GitHub 仓库。

---

## 三、Azure 部署步骤（免卡，面向学生）

### 步骤 1：开通 Azure for Students（免卡）

1. 打开 https://azure.microsoft.com/free/students
2. **用学校邮箱**（`.edu.cn`）注册/登录 Microsoft 账号
   - ⚠️ 实测提示：用**学校邮箱**注册并用它做验证；用 GitHub 登录但邮箱不一致会导致
     "You're not eligible"（Microsoft 官方 Q&A 记录的常见坑）
3. 完成**手机验证** + **学籍验证**（点学校邮箱收到的验证链接）
4. 选学校、专业、毕业年份 → 激活，即得 **$100 额度（12 个月）**

> ⚠️ 若提示 "You're not eligible"：需要学校 IT 管理员确认该校的 Microsoft tenant
> 已注册为学术机构，再由管理员提交 support ticket 做后端资格审核。
> 河北金融学院作为正规本科院校通常可过。

### 步骤 2：创建 App Service（F1 免费档）

Azure Portal → **Create a resource** → 搜 **App Service** → Create：

| 表单项 | 填什么 | 说明 |
|---|---|---|
| **Name** | `mandatepay-你的名字` | 全局唯一，决定域名 `mandatepay-xxx.azurewebsites.net` |
| **Runtime** | **Python 3.11**（或更新但 ≤3.13） | ⚠️ 别选 3.14（太新，C 扩展可能没 wheel） |
| **Operating System** | **Linux** | |
| **Region** | 东亚/东南亚（如 East Asia）或与 Neon 同区 | |
| **App Service Plan** | 新建，SKU **Free (F1)** | ⚠️ 必选 F1，B1 是付费的 |

> F1 规格（官方）：10 个应用 / 1GB 内存 / **60 CPU 分钟每天** / **165MB 带宽每天** / 1GB 存储。

### 步骤 3：配置部署（GitHub 持续部署）

左侧 **Deployment Center** → **GitHub** → 授权 → 选：

| 项 | 值 |
|---|---|
| GitHub 账号 | 你的账号 |
| 仓库 | `niu602/mandatepay` |
| 分支 | `master` |
| **Root Directory** | 留空（仓库根） |

> Git 部署时 `SCM_DO_BUILD_DURING_DEPLOYMENT` **自动为 true**（官方文档），
> Oryx 会自动 `pip install -r requirements.txt` —— 我们的 requirements.txt 在仓库根，符合要求。
>
> ⚠️ 若用 `az webapp deploy` 上传 ZIP，必须手动设 `SCM_DO_BUILD_DURING_DEPLOYMENT=1`，
> 否则依赖不会安装。

### 步骤 4：设启动命令（关键）

**Configuration** → **General settings** → **Startup Command**：

```
bash startup.sh
```

`startup.sh` 内容（已本地实测）：

```bash
exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${WEBSITES_PORT:-8000}" \
    --timeout-keep-alive 120
```

> ⚠️ **为什么用 `app.main:app` 而不是 `--app-dir app`**：
> 我们的 FastAPI 实例在 `app/main.py`，且该模块用了**相对导入**
> （`from . import config, paypal_client`）。用 `--app-dir app` 会把 `app/main.py`
> 提升成顶层模块 `main`，相对导入立刻失效
> （`ImportError: attempted relative import with no known parent package`）—— **本地实测过**。
> 所以从仓库根启动，用完整包路径 `app.main:app`。
> 本地等价命令已验证：六端点全 200、`/static/budget.html` 200、启动建表成功。

### 步骤 5：填环境变量

**Configuration** → **Environment variables**：

| Key | Value |
|---|---|
| `DATABASE_URL` | Neon 连接串 |
| `PAYPAL_CLIENT_ID` | PayPal 沙箱 Client ID |
| `PAYPAL_CLIENT_SECRET` | PayPal 沙箱 Secret |
| `PAYPAL_WEBHOOK_ID` | Webhook ID |
| `LLM_API_KEY` | Z.ai key |
| `PAYPAL_MODE` | `sandbox` |
| `LLM_BASE_URL` | `https://api.z.ai/api/paas/v4` |
| `LLM_MODEL` | `glm-4.7-flash` |
| `LLM_THINKING` | `disabled` |

保存后点 **Restart**。

### 步骤 6：部署后验证

打开 `https://mandatepay-xxx.azurewebsites.net` → 验收清单见 §五。

> 💡 **Neon 侧的建表/种子/清场已在本地连库执行完**，部署完直接访问即可。
> （Azure App Service 有 SSH/Shell，但 F1 免卡档不必用 —— 我们的数据在 Neon。）

### 备选：Render / Railway（若日后绑到卡或注册开放）

- `render.yaml` 保留在仓库里，配置完整（10 个环境变量 + 免费档 + 健康检查）
- Railway 的 `railway.toml` **官方已废弃、2026-12-01 停止读取**（正撞评审期），
  故未放该文件；新格式是 `.railway/railway.ts`（需 `npm install railway`）

---

## 四、本地已完成的准备工作

### 4.1 代码修复（部署前必须做，否则线上有问题）

| 修复 | 原因 | 文件 |
|---|---|---|
| **挂载 `/static`** | `budget.html` 的 AG Studio 运行时靠 `fetch('/static/vendor/ag-studio.mjs')` 探测本地 ESM。此前**从未挂载 `/static`**，探测永远 404 → 只能回退 jsdelivr CDN（外网依赖 + 首屏慢） | `app/main.py` |
| **启动时自动建表** | Neon 免费项目**只有空库没有表**，而建表逻辑在 `store._Conn.__enter__`（连接时执行）。若不显式触发，**第一个请求才建表** → 冷启动首请求超时 | `app/main.py`（lifespan） |
| 静态资源直出 | 部署后前端资源统一走 `/static` | `app/main.py` |

### 4.2 部署配置

| 文件 | 用途 |
|---|---|
| `startup.sh` | **Azure App Service 启动命令**（`app.main:app`，绑 0.0.0.0，端口 8000） |
| `Dockerfile` | 容器镜像（若改走自定义容器模式；`$PORT` 兼容各平台） |
| `.dockerignore` | 排除 `.env` / `node_modules` / 本地数据库 / `static/vendor`，防密钥进镜像 |
| `Procfile` | 启动命令（Railway / Render 原生模式自动识别） |
| `render.yaml` | Render Blueprint 配置（**备用** —— 若日后绑卡可直接用） |
| `.python-version` | `3.11.9` —— 锁定 Python 版本（Render 默认会是 3.14.8） |
| `deploy.env` | 部署环境变量清单（本地生成，含明文密钥，已 gitignore） |
| `scripts/deploy_init.sh` | 一键：建表 → 种子约束 → 同步目录 → 清场 |

> ⚠️ **不用 `railway.toml`**：官方文档明确 Config as Code 已废弃、
> **2026-12-01 停止读取旧文件** —— 正好撞上评审期。

### 4.3 本地验证结果

```
✅ 五个页面全部 200：/ /constraints /budget /approvals /audit
✅ /static/budget.html                200
✅ /static/vendor/ag-studio.mjs       200（本地 ESM 命中，不再走 CDN）
✅ 启动建表：postgresql schema 就绪
✅ 模拟平台启动命令（0.0.0.0 + $PORT + env）：六个端点全部 200
✅ Azure 启动命令等价形式（app.main:app + 0.0.0.0:8000）：六端点全 200
✅ Procfile 命令 + Neon 生产库 + 完整环境变量 → 六端点全 200
✅ deploy_init.sh 全流程跑通（建表→约束→目录→清场）
✅ 清场后应用正常：约束 $200 / 预算已花 $0
✅ Neon 实连：PG 18.6，六表就绪，约束已写，目录已同步，已清场
✅ 真实 LLM 链路：连 Neon + Z.ai 走 function calling，解析正确
✅ DSN 兼容：postgres:// 与 postgresql:// 都能用（store._effective_dsn 归一化）
✅ 应用常驻内存 60MB（App Service F1 有 1GB，余量充足）
✅ Python 3.11 语法兼容性检查：ast.parse(feature_version=(3,11)) 全部通过
```

### 4.4 冷启动预判（实测前）

| 项 | 实测值 | 判断 |
|---|---|---|
| 应用 import 耗时 | **0.68s** | 运行时不构成瓶颈 |
| 应用常驻内存 | **60MB** | F1 有 1GB，余量充足 |
| `paypal-agent-toolkit` 传递依赖 | **仅 httpx / pydantic / requests** | 很轻，工单担心的"依赖过重"**不成立** |
| `uvicorn[standard]` | 含 httptools / uvloop / watchfiles | 需编译或取预编译 wheel → **构建期**可能慢（不是运行期） |
| langchain 链 | langgraph + langchain + langchain-openai | 中等，pip 安装约 1-2 分钟 |

> 结论：冷启动风险主要在**构建阶段**（pip install），而非运行时。
> 工单给的精简顺序（① 裸 uvicorn ② 查 toolkit 依赖）**已预先验证第 ② 条不适用**。

### 4.5 Azure App Service F1 额度核算（评审期 12-01 ~ 12-15）

F1 限制：**60 CPU 分钟/天**、1GB 内存、165MB 带宽/天。三项逐一评估：

| 限制 | 我们的用量 | 结论 |
|---|---|---|
| **内存 1024MB** | 实测常驻 60MB | ✅ 余量 17 倍 |
| **带宽 165MB/天** | 单页 ≈45KB，一次完整浏览 5 页 + 几个 API ≈ 249KB → 约 **679 次完整浏览/天** | ✅ 够（几十个评委各逛几轮） |
| **CPU 60 分钟/天** | 静态页 <0.05 秒；约束引擎 <0.01 秒；`/budget` 聚合 <0.1 秒；LLM 调用是 **I/O 等待**（等网络时几乎不占 CPU），单次 <0.5 秒；应用启动 2-5 CPU 秒（一次性） | ✅ 够 —— 典型场景一天累计 <5 CPU 分钟 |

**结论**：F1 的三项限制全部够用，且**额度按天重置**，评审期 15 天中任何一天都不会因
额度耗尽而中断（不像 Render 免费 PG 30 天删库、Zeabur 12-01 停服）。

## 五、验收清单（DoD）

部署完成后逐项核对：

- [ ] 公开 URL 五个页面全部 200：/ /constraints /budget /approvals /audit
- [ ] `/budget` 双视图均正常（AG Studio 视图 + 原版视图，切换按钮可用）
- [ ] 冷启动耗时已记录（首访 + 唤醒后各测一次）
- [ ] 截图存档

---

## 六、冷启动与保温（W-07）

### 6.1 冷启动耗时记录

| 场景 | 耗时 | 备注 |
|---|---|---|
| 首次构建 | 待填 | Oryx 装依赖 + pip install langchain 链 |
| 首次访问（冷） | 待填 | App Service 实例启动 + 应用初始化 |
| 二次访问（热） | 待填 | F1 实例复用，预期 <1 秒 |

**测量方法**（部署后跑）：

```bash
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net --measure
```

连打 3 次 `/healthz`，输出「首访耗时 vs 热实例耗时」的对比，
并写入 `cold_start_measure.json` —— 填进上表。

### 6.2 保温策略（F1 不支持 Always On）

F1 **不支持 Always On**：闲置后实例可能被平台卸载，首访走冷启动（实测 2-5 CPU 秒）。
但 F1 有 **60 CPU 分钟/天硬上限**，所以保温必须低频。

**采用：单个外部探针 / 30 分钟**（约 1.5 CPU 分钟/天，占预算 2.5%）。
探针**必须跑在 F1 实例外面**（跑在实例里的话，实例卸载时探针一起没了）。

```bash
# 保温（常驻，跑在自己机器上）
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net

# 演示前预热（录制/答辩前 5 分钟）
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net --warm

# 部署后自检：确认 AG Studio 走 CDN 而非本地 7MB vendor
python scripts/keep_warm.py --url https://<你的域名>.azurewebsites.net --check-cdn

# CPU 预算核算（不需要 URL）
python scripts/keep_warm.py --budget
```

完整说明见 **[W07_KEEP_WARM.md](W07_KEEP_WARM.md)**。

### 6.3 部署后自检清单

- [ ] `/healthz` 返回 200（`curl -i <域名>/healthz`）
- [ ] 冷启动耗时已记录（首访 + 唤醒后各测一次）→ 填进 §6.1 表
- [ ] `--check-cdn` 通过（`/static/vendor/ag-studio.mjs` 期望 404）
- [ ] 演示前预热可用（`--warm` 全绿）
- [ ] Azure Portal 配额页面当天用量 < 10 CPU 分钟

**若冷启动 > 90 秒**，按工单顺序精简：
1. `uvicorn[standard]` → 裸 `uvicorn`（去掉 httptools/uvloop/watchfiles 编译依赖）
2. 检查 `paypal-agent-toolkit` 传递依赖 —— **已验证：仅 httpx/pydantic/requests，不重**

---

## 七、已知限制（如实记录）

1. **API 无鉴权**（技术债 #12）：公网部署后 `/api/*` 全部暴露。
   本项目是 Demo 性质，评审期可接受；但**不要放任何真实数据**。
2. **F1 的 60 CPU 分钟/天 是硬上限**：超出后当天请求会被拒绝（App Service 停服）。
   按 §4.5 核算典型用量一天 <5 分钟，够用；但**如果评委集中访问导致异常**，
   需要关注 Azure Portal 的配额页面。缓解手段：$100 学生额度可临时升级到 B1（付费）。
3. **F1 无自定义域名**：只能用 `*.azurewebsites.net` 子域（官方限制）。
   想要自己的域名必须升级付费档 —— 评审期用默认域名完全够。
4. **`/budget` 的 AG Studio 走 CDN**（若未在构建期装 `static/vendor/`）：
   `static/vendor/` 已 gitignore（npm 商业包），平台构建时不会自动装。
   页面会自动回退 jsdelivr CDN —— **功能正常**，但首屏依赖外网。
5. **PayPal Webhook 需要公网 URL**：部署后把 webhook 地址改成
   `https://<你的域名>/api/webhook`（比 ngrok 稳定，评审期一直有效）。
6. **Azure for Students 需要学籍验证**：必须用学校邮箱 + 手机号。
   若提示 "You're not eligible"，需学校 IT 管理员协助提交 support ticket。
   学生订阅**每年可续**（在校期间），这点比任何 30 天/3 个月的方案都稳。
7. **Neon 连接串（数据库密码）曾在聊天中明文出现** —— 按用户要求不做处理，
   但记录在案：若后续要轮换，需同步更新平台的 `DATABASE_URL`。
8. **Azure 学生额度不覆盖 Azure China**：如果后续想迁到世纪互联运营的国内区，
   权益不适用。当前用全球区（East Asia 等）+ Neon 美西，评委访问走公网没问题。


