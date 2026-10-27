# README 更新 patch（待你确认，尚未应用）

> 目标文件：`README.md`
> 应用方式：确认后我直接写入；或你手动粘贴下面三段。
> 验证依据：`AG_STUDIO_FEASIBILITY.md`（真实浏览器实测，非推测）

---

## Patch 1 — 目标奖项段落（第 8–10 行）

**原文**

```markdown
## 目标奖项

**Best Use of Agentic Commerce**（$5,000）
```

**改为**

```markdown
## 目标奖项

**Best Use of Agentic Commerce**（$5,000）　主攻
**Best Use of AG Studio**（$5,000）　冲奖
```

---

## Patch 2 — 技术栈表格的「前端」行与新增两行（第 12–22 行表格内）

**原文**

```markdown
| 前端 | `static/` 原生 HTML（支付页 + 审计页） | ✅ 已可用 |
| MCP | @paypal/mcp | ⏳ P1 可选，冲奖用 |
```

**改为**

```markdown
| 前端 | **AG Studio dashboard（`/budget`）+ 原生 HTML（其余页）** | ✅ 已跑通 |
| 可视化 | AG Studio 3.0（`ag-studio`，KPI / 图表 / 仪表盘，dashboard 由代码预定义） | ✅ **已实测**：真实浏览器渲染通过 |
| MCP | @paypal/mcp | ⏳ P1 可选，冲奖用 |
```

---

## Patch 3 — 新增「AG Studio 预算仪表盘」小节（建议放在「AI 决策参与点」之后）

**新增**

```markdown
## AG Studio 预算仪表盘（`/budget`）

预算仪表盘用 **AG Studio** 构建，数据源是我们**已有的** `/api/budget/summary`——后端一行未改，
只在前端把 JSON 整形后喂给 Studio：

- **4 个 KPI 卡**（双语）：月度预算 / 本月已用 / 剩余额度 / 本月已到账
- **2 条进度条**：已用占比 vs 预测占比（线性仪表，0–100% 刻度）
- **供应商支出柱状图**：按 vendor 聚合本月已到账支出
- **风险提示**：`over_budget` 时画布上方出现红色警示条，超支的 KPI 数值同时转红

技术上值得说明的两点：

1. **dashboard 由代码预定义，不是人工拖出来的**。AG Studio 的
   `createStudio(el, {data, mode:'view'})` + `api.setState(report)` 让我们把整个
   dashboard（KPI/图表/布局坐标）写进源码，评审 clone 即见成品，无需交互式搭建。
2. **渲染与判定分离**。AG Studio 只负责「画」，预算口径、约束判定全在我们自己的
   可单测代码里（650 次判定的压测报告见 `STRESS_REPORT.md`）——
   这才是敢把预算交给可视化层的前提。

> AG Studio 是 AG Grid 的商业产品（npm `license: Commercial`）。无 license key 时为官方
> 明示的 **trial 模式：功能全开、UI 无水印**（仅控制台有提示横幅）。
> 运行时从 jsdelivr CDN 加载官方包（版本已 pin），商业 bundle **未提交进仓库**。
> 本地离线可用：`python scripts/setup_ag_studio.py`。
> 详见 `AG_STUDIO_FEASIBILITY.md`（含完整可行性验证与踩坑记录）。
```

---

## Patch 4 — 待办清单（第 324 行附近）

**原文**

```markdown
- [ ] MCP Server 集成（P1 冲奖）
```

**改为**

```markdown
- [x] **AG Studio 预算仪表盘**（`/budget`，dashboard 代码预定义，后端零改动，已浏览器实测）
- [ ] MCP Server 集成（P1 冲奖）
```

---

## Patch 5（可选）—「规则」段落补一条

**原文**

```markdown
## 规则

- 全程沙箱，不涉及真实资金
```

**改为**

```markdown
## 规则

- 全程沙箱，不涉及真实资金
- AG Studio 前端运行时从官方 CDN 加载，不入库商业 bundle；试用期内使用
```

---

## 附：为什么不把 `static/vendor/` 提交进仓库

- `ag-studio` / `ag-grid-enterprise` / `ag-charts-enterprise` 的 npm `license` 均为 **Commercial**，
  把 7MB 商业 bundle 提交进**公开仓库**属于再分发，评审在公开仓库里 clone 反而是风险点。
- 官方 PayPal boilerplate 也是 `npm install` 而非 vendor 进仓库。
- 页面默认走 CDN（版本 pin、实测可达），本地 vendor 仅在需要离线时用
  `python scripts/setup_ag_studio.py` 安装。

若你希望**完全离线**（不依赖 CDN），需要额外一行 Python（把 `/static` 挂出来），
这一行我**没有擅自加**——它虽然不碰任何 API，但属于后端改动，留给你决定：

```python
# app/main.py
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
```

加上后页面会自动探测并改用本地 `static/vendor/*.mjs`（`budget.html` 里已有这段探测逻辑）。

---

## 附：本次改动涉及的文件

| 文件 | 状态 | 说明 |
|---|---|---|
| `static/budget.html` | **改写** | AG Studio 版本（含原版视图内嵌 + 一键切换） |
| `static/budget.orig.html` | **新增** | 改造前的原版备份（原封不动） |
| `static/vendor/` | **新增（已 gitignore）** | AG Studio 前端运行时 7.0 MB |
| `scripts/setup_ag_studio.py` | **新增** | 安装 / 校验前端运行时 |
| `AG_STUDIO_FEASIBILITY.md` | **新增** | 可行性验证记录 |
| `README_PATCH.md` | **新增** | 本文件 |
| `.gitignore` | **改** | 增加 `static/vendor/` |
| `app/main.py` | **未改** | ✅ 后端零改动 |
| 其余页面 | **未改** | ✅ 只动 `/budget` |
