# 约束引擎压测报告（D12）

- 随机种子：`42`
- 约束条数：**50**（随机生成：滚动预算 / 单笔阈值 / 日累计阈值 / 供应商白名单 / 品类白名单）
- 请求条数：**650**（其中 **550** 条为对抗样本）
- 判定总次数：**650**

## 核心指标（两层口径）

| 口径 | 含义 | 一致率 | 误拒 | 漏放 | 严重度错判 |
|---|---|---|---|---|---|
| 引擎语义 | 与当前实现口径逐条比对，检验**实现正确性** | 100.00% | 0 | 0 | 0 |
| 用户直觉 | 按用户真实预期比对，暴露**疑似误拒/漏放** | 56.31% | 18 | 263 | 3 |

## 判定分布

| 判定 | 次数 |
|---|---|
| AUTO_APPROVE | 244 |
| REQUIRE_HUMAN | 127 |
| REJECT | 279 |

## 疑似误拒 / 漏放分类（用户直觉口径）

| 类别 | 例数 | 说明 |
|---|---|---|
| F. 漏放：品类未知却跳过了品类白名单校验 | 263 | 约束限定了品类，但请求没带品类 → 校验被跳过放行，安全风险。 |
| A. 供应商大小写/空格导致的误拒 | 18 | 用户写 `notion`，白名单是 `Notion` → 被拒。真实场景极常见。 |
| H. 严重度错判（AUTO_APPROVE ↔ REQUIRE_HUMAN，常见于浮点尾数） | 3 |  |

### 样例

```
[at_threshold] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[over_threshold] vendor='Figma' category='' $10.01 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=REQUIRE_HUMAN → F. 漏放：品类未知却跳过了品类白名单校验
[at_budget] vendor='Figma' category='' $50.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=REQUIRE_HUMAN → F. 漏放：品类未知却跳过了品类白名单校验
[daily_at] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[daily_over] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=REQUIRE_HUMAN → F. 漏放：品类未知却跳过了品类白名单校验
[no_category] vendor='Figma' category='' $10.0 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[float_boundary] vendor='Figma' category='' $9.999999999999998 | 白名单=['Figma'] 品类白名单=['SaaS'] | 直觉=REJECT 引擎=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[at_threshold] vendor='Notion' category='' $10.0 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 引擎=AUTO_APPROVE → F. 漏放：品类未知却跳过了品类白名单校验
[over_threshold] vendor='Notion' category='' $10.01 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 引擎=REQUIRE_HUMAN → F. 漏放：品类未知却跳过了品类白名单校验
[at_budget] vendor='Notion' category='' $1000.0 | 白名单=['Notion', 'Dropbox', 'Linear'] 品类白名单=['Design'] | 直觉=REJECT 引擎=REQUIRE_HUMAN → F. 漏放：品类未知却跳过了品类白名单校验
```

## 方法论说明

压测**不以引擎自身为标准答案**（那等于没测）。本报告写了两份独立参照实现：

1. **引擎语义 oracle** —— 与当前实现口径一致，用来检验**代码有没有写错**；
2. **用户直觉 oracle** —— 按真实用户的预期推导（供应商大小写不敏感、金额按分取整、品类未知时保守处理），用来暴露**用户会撞上的误拒/漏放**。

两层结果的差距，正是「测试通过但用户仍觉得被冤枉」的地方。
对抗样本覆盖金额恰等于阈值/预算、阈值+0.01、大小写供应商、请求缺品类、浮点尾数等真实事故高发区。

复现：`python scripts/stress_constraints.py --seed 42`
