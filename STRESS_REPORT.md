# 约束引擎压测报告（D12）

- 随机种子：`42`
- 约束条数：**50**（随机生成：月度滚动预算 / 单笔阈值 / 日累计阈值 / 供应商白名单 / 品类白名单）
- 请求条数：**650**（其中 **550** 条为对抗样本）
- 判定总次数：**650**

## 三层口径与核心指标

| 口径 | 含义 | 一致率 | 误拒 | 漏放 | 严重度错判 |
|---|---|---|---|---|---|
| 当前引擎 vs 引擎规格 | 检验**实现有没有写错** | 100.00% | 0 | 0 | 0 |
| 修复前语义 vs 用户直觉 | 量化**用户此前实际遭遇什么** | 63.54% | 58 | 170 | 9 |
| 当前引擎 vs 用户直觉 | **修复后**还剩什么 | 62.31% | 0 | 0 | 245 |

## 发现的问题分类（修复前口径）

| 类别 | 例数 | 占比 | 说明 |
|---|---|---|---|
| F. 漏放：品类未知却跳过了品类白名单校验 | 170 | 26.15% | 约束限定了品类，但请求没带品类字段 → 品类校验被**静默跳过**，约束形同虚设（安全风险）。 |
| C. 品类大小写导致的误拒 | 38 | 5.85% | 品类 `saas` 与白名单 `SaaS` 大小写不同 → 被拒。 |
| A. 供应商大小写/空格导致的误拒 | 18 | 2.77% | 用户写 `notion`、或复制粘贴带空格 ` Notion `，与白名单里的 `Notion` 不匹配 → **被冤枉地拒绝**。 |
| J. 严重度差异：品类未知 → 引擎判「需人工确认」，直觉判「直接拒」 | 6 | 0.92% |  |
| I. 其他严重度错判 | 3 | 0.46% | 其他严重度错判。 |
| E. 其他误拒（需人工排查） | 2 | 0.31% | 以上都不是的误拒，需人工排查。 |

## 已应用的修复

| 问题 | 修复 |
|---|---|
| 供应商大小写/空格误拒 | 白名单匹配改为**归一化**（去空格 + 忽略大小写） |
| 品类大小写误拒 | 同上，品类白名单比较也归一化 |
| 浮点尾数边界错判 | 金额**按分取整**后再与阈值/预算比较 |
| 品类未知时静默放行 | 改为 **REQUIRE_HUMAN**：既不静默放行，也不硬阻断 |

## 判定分布（当前引擎）

| 判定 | 次数 |
|---|---|
| AUTO_APPROVE | 135 |
| REQUIRE_HUMAN | 363 |
| REJECT | 152 |

### 问题样例（修复前）

```
[at_threshold] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[daily_at] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[no_category] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[category_case] vendor='Figma' category='saas' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=AUTO_APPROVE 修复前=REJECT → C. 品类大小写导致的误拒
[float_boundary] vendor='Figma' category='' $9.999999999999998 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[at_threshold] vendor='Notion' category='' $10.0 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[daily_at] vendor='Notion' category='' $10.0 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[no_category] vendor='Notion' category='' $10.0 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[category_case] vendor='Notion' category='design' $10.0 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=AUTO_APPROVE 修复前=REJECT → C. 品类大小写导致的误拒
[float_boundary] vendor='Notion' category='' $9.999999999999998 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 修复前=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
```

## 方法论说明

压测**不以引擎自身为标准答案**（那等于没测）。本报告维护三份独立参照实现：

1. **引擎规格 oracle** —— 与当前实现逐条对齐，用来检验**代码有没有写错**；
2. **修复前语义 oracle** —— 还原修复前的判定口径，量化**用户此前实际遭遇什么**；
3. **用户直觉 oracle** —— 按真实用户预期推导（名称归一化、金额按分取整、品类未知即不放心），用来暴露**用户会撞上的误拒/漏放**。

三层结果的差距，正是「测试全绿但用户仍觉得被冤枉」的地方。本次修复即由第 2/3 层的差异驱动。

对抗样本覆盖：金额恰等于阈值/预算、阈值+0.01、供应商大小写、供应商带空格、品类大小写、请求缺品类、浮点尾数 —— 均为真实事故高发区。

复现：`python scripts/stress_constraints.py --seed 42`
