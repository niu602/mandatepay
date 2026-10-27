# AG Studio 可行性验证记录

> 结论：**可行，已落地**。本页 `/budget` 现由 AG Studio 渲染，数据源是已有的
> `/api/budget/summary`，**后端一行未改**。
> 验证方式：真实 npm 包 + 本机 Chrome（Playwright）+ 真实项目 `/api/budget/summary` 实测。

---

## 1. AG Studio 能否免费创建并 embed？—— ⚠️ 有条件可行

| 问题 | 结论 | 依据 |
|---|---|---|
| npm 包是否存在 | ✅ `ag-studio@3.0.0` | `npm view ag-studio` → `license = Commercial` |
| 是否有免费档 | ⚠️ **45 天免费试用**（无限制、无水印）；此后需商业授权 | 官方 FAQ 原文：*"AG Studio is an Enterprise product that requires a commercial licence. You can evaluate the full feature set with a free 45-day trial, no restrictions or watermarks."* |
| **无 license key 能否跑** | ✅ **能，功能全开** | 实测控制台输出：*"All AG Studio Core features are unlocked for trial."* |
| 无 key 会不会有水印 | ⚠️ **控制台有警告横幅，UI 上没有可见水印** | 实测：console 输出 `License Key Not Found` 横幅；**截图确认 UI 无水印** |
| 官方对 key 的态度 | ✅ key 是**可选**的 | PayPal 官方 boilerplate README：*"### AG Studio licence (optional) — Set `AG_STUDIO` in `.env.local`"* |

**结论**：对本项目（提交截止 11/11，在 45 天试用期内）**完全可用**。
若要去掉控制台警告，申请 trial key 即可（`info@ag-grid.com`），代码侧传 `AG_STUDIO` 环境变量。

---

## 2. 能否连接本地 REST API？—— ✅ 通过（模式与直觉不同）

**关键认知**：AG Studio **不自己连数据源**。它吃的是「数组 of plain objects」：

```js
createStudio(el, {
  data: { sources: [{ id: 'kpi', data: [...], fields: [...] }] },
  mode: 'view',
});
```

所以正确姿势是：**我们的页面 fetch `/api/budget/summary` → 拆成两个数据源 → 喂给 Studio**。
这正是 PayPal 官方 boilerplate 的模式（`lib/dashboard-data.ts` 把 REST 响应转成相关表再喂进去）。

数据源映射（不改后端，只在前端整形）：

| AG Studio source | 来源 | 字段 |
|---|---|---|
| `kpi` | `/api/budget/summary` 的标量字段 | 月度预算 / 本月已用 / 剩余 / 本月已到账 / 已用占比 / 预测占比 … |
| `vendors` | `by_vendor` 数组 | vendor / amountUsd / count |

---

## 3. embed 后跨域 / iframe 是否正常？—— ✅ 通过（该问题不适用）

AG Studio **不是托管 dashboard 的 iframe**，而是**跑在我们页面里的 JS 组件**（`createStudio(el, …)`）。

- 无 iframe → **无跨域问题**
- 无独立 origin → **Render 部署无碍**
- 同源静态资源 → 无额外 CORS 配置

---

## 4. 但有一个你没列、却决定成败的门槛：无构建步骤的纯 HTML 怎么加载它

我们的项目是 **FastAPI + 纯静态 HTML（无 npm 构建、无 package.json）**，
而 AG Studio 只发 npm 包，产物 `main.esm.mjs` **带裸依赖**（`from "ag-stack"`），
**没有 UMD 浏览器直引版**。

解法：`<script type="importmap">` 逐级映射裸 specifier。
传递依赖共 **7 个包**（BFS 解析得出）：

```
ag-studio → ag-stack, ag-grid-enterprise, ag-charts-enterprise
ag-grid-enterprise → ag-grid-community, ag-stack
ag-charts-enterprise → ag-charts-community, ag-charts-core
```

min 版合计 **7.0 MB**。

### 最终采用：CDN 主源 + 本地 vendor 自动探测

```js
// 先 HEAD 探测本地 /static/vendor（若服务器已挂载）
//   成功 → 用本地文件；失败 → 用 jsdelivr CDN
```

- **零 Python 改动**（符合「不改后端」约束）
- 零仓库体积（vendor 目录已 gitignore）
- CDN 全部版本已 pin 且实测可达（jsdelivr 200，3.8MB/3.7s）

---

## 5. 实测踩到的坑（全部已修，代码里有注释）

| # | 坑 | 现象 | 解法 |
|---|---|---|---|
| 1 | **注入时机错误** | `setState` 报 `Field with id 'kpi.monthlyBudget' does not exist in the schema` | 必须在 **`onStudioReady`** 回调里 `setState`——此时数据引擎才初始化完。`onApiReady` 还太早（文档明说 "data engine has not been initialised yet"） |
| 2 | **字段 id 形式** | 同上报错 | 必须是 `sourceId.fieldId`，且要在 source 的 `fields[]` 里**显式声明** |
| 3 | **format 名非法** | `references unknown format 'numberFormat'` | 合法值只有 `textFormat` / `currencyFormat` / `percentageFormat` / `integerFormat` / `dateFormat` / `booleanFormat` / `decimalFormat` |
| 4 | **图表 type 名** | `Widget has invalid type 'bar-chart'` | **没有裸 `bar-chart`**；竖向柱状图是 `column-chart-grouped`（横条才是 `bar-chart-grouped`） |
| 5 | **import map 插入时机** | import map 晚于模块加载 | import map 必须在任何模块加载前插入 → 页面用**经典脚本 + 动态 `import()`**，不用 `<script type="module">` |
| 6 | **KPI 标题截断** | 「月度预算 Monthly...」 | 标题 typography 要挂在 **`format.title.typography`**，不是 `format.style.title.typography` |
| 7 | **KPI caption 显示 "KPI"** | 卡片上冒出数据源名 | `caption: { enabled: false }`（`text:''` 不会清空，会回落到数据源名） |
| 8 | **KPI 数值被裁掉** | DOM 里有 200.00 但看不见 | 显式设 `layout: { rowHeight: 88, columns: 24 }` + 放大 `ySpan` |
| 9 | **Filters 侧栏占栏** | 右侧显示 "No filters available" | `panels: { view: { left: [], right: [] } }` |

---

## 6. 与任务要求的偏差（如实记录）

| 要求 | 实现 | 偏差 |
|---|---|---|
| KPI：月度预算/本月已用/剩余/本月已到账 | ✅ 四个 KPI 卡（双语） | 无 |
| 进度条：已用占比 vs 预测占比（两条，预测用虚线） | ✅ 两条 `linear-gauge`（19% / 21%，带 0–100% 刻度） | ⚠️ AG Studio 的 gauge widget **没有 target 虚线配置**（`AgGaugeDataMapping` 只有 `value`），所以「预测用虚线」改用**独立第二条 gauge** 表达，而非虚线叠加。原版视图里那条虚线投影条**仍在**（可切换查看） |
| 图表：按供应商支出分布 | ✅ `column-chart-grouped` | 无 |
| 风险提示：over_budget 时红色警告 | ✅ 画布上方 HTML 警示条（双语）+ KPI 卡数值超支时转红 | AG Studio 核心 widget 集**没有文本横幅类型**（只有 `image`），故警示条用相邻 HTML 实现 |
| 双语标签 | ✅ 所有 KPI/图表/警示条标题均中英双语 | 无 |
| 保留顶部导航栏 | ✅ 指向 / /constraints /approvals /audit /budget | 无 |

---

## 7. 复现与验证

```bash
# 安装前端运行时到 static/vendor（可选，页面默认走 CDN）
python scripts/setup_ag_studio.py
python scripts/setup_ag_studio.py --check    # 校验 import map 完整性

# 真实浏览器验证（需要本机 Chrome）
node <验证脚本> http://127.0.0.1:8888/budget
```

实测结果（真实项目 /budget）：

| 检查项 | 结果 |
|---|---|
| AG Studio 加载并渲染 | ✅ 状态栏「✅ AG Studio 已渲染预算总览（运行时：jsdelivr CDN）」 |
| 4 个 KPI 数值 | ✅ 1,000.00 / 190.00 / 810.00 / 10.00 |
| 双语标题完整性 | ✅ 无截断 |
| 两条进度条 | ✅ 19% / 21%，带刻度 |
| 供应商柱状图 | ✅ Notion 柱子渲染（Y 轴刻度正常） |
| Filters 侧栏 | ✅ 已隐藏 |
| UI 水印 | ✅ 无（仅控制台警告） |
| 视图切换（AG ↔ 原版） | ✅ 双向可用，原版数据同样正确 |
| 后端改动 | ✅ 0 行 |

---

## 8. 回退方案（3 步，1 分钟内）

AG Studio 版是**新增能力**，原版完整保留，且**已在同一文件内**（一键切换）。

**方式一（最快）**：页面顶部「切换到原版视图 / Classic view」按钮。
**方式二（完全回退到改造前）**：

```bash
cd D:/paypal-ai-hackathon
git checkout -- static/budget.html          # 恢复改造前的原版
# 或
cp static/budget.orig.html static/budget.html
```

**方式三（回退到 AG Grid Community 方案）**：若评审明确要求「必须用 AG Grid Community 开源免费版」，
按以下步骤替换（本次未采用，因 AG Studio 已验证可用）：

1. `npm install ag-grid-community ag-charts-community`（均为 MIT）
2. 把 `static/budget.html` 的 AG Studio 段替换为 AG Grid Community 的 `AgGridVue`/`ag-charts-community` 图表
3. 保留 `renderClassic()` 作为降级视图

> 注：AG Grid **Community** 是免费开源（MIT），**Enterprise** 才是 $999/开发者。
> AG Studio 建立在 Enterprise 之上，所以才有商业授权问题；Community 版无此顾虑。

---

## 9. 许可与合规提示（供你决策）

- `ag-studio` / `ag-grid-enterprise` / `ag-charts-enterprise` 的 npm `license` 均为 **Commercial**。
- 页面**从 CDN 运行时加载官方包**（与官方 boilerplate 的 `npm install` 等价），**未把商业 bundle 提交进仓库**
  （`static/vendor/` 已 gitignore）。
- 无 license key 时为官方明示的 **trial 模式**（功能全开、UI 无水印），控制台有警告横幅。
- 若要完全无警告：申请 trial key → 传环境变量 → 官方 boilerplate 的做法是 `AG_STUDIO` 环境变量。
  AG Studio 的 key 注入入口为 `AgStudioLicenseManager.setLicenseKey(licenseKey)`（静态方法）。

---

## 10. 一句话给评委

> 预算仪表盘用 **AG Studio** 构建：数据源就是我们自己的 `/api/budget/summary`，
> dashboard 由代码预定义（`setState`），无需人工拖拽；AG Studio 负责 KPI / 图表 / 交叉过滤的渲染，
> 而**预算口径与约束判定仍然完全在我们自己的可测试代码里**——
> 650 次判定的压测报告就是它敢这么用的底气。
