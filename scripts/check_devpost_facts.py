#!/usr/bin/env python
"""核验 DEVPOST_SUBMISSION.md 里的每个事实断言。

DoD 要求：*每一段的事实都能在仓库里找到出处*。

这个脚本做两件事：
1. **结构检查** —— 五个评分段落齐全、占位符只有 VIDEO_URL、无中文残留
2. **事实检查** —— 把稿子里的关键数字与仓库源文件里的对应值比对

事实检查是**双向**的：稿子里出现一个数字，就去源文件里找它；
源文件里的关键数字，也要确认稿子提到了。
这样能抓到"漏写"和"吹牛"两类问题。

用法：
```bash
python scripts/check_devpost_facts.py
```

退出码：0 = 全部通过；1 = 有问题（会逐条列出）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "DEVPOST_SUBMISSION.md"

# ── 五个评分段落（工单的 5 项）────────────────────────────
REQUIRED_SECTIONS = [
    "Technological Implementation",
    "Design",
    "Potential Impact",
    "Innovation",
    "Presentation",
]

# ── 事实断言：(稿子里必须出现的字符串, 必须在哪个源文件里出现, 说明)──
# 源文件用相对路径；字符串在两个文件里都要能找到。
FACT_CLAIMS: list[tuple[str, str, str, bool]] = [
    # —— 线上与仓库 ——
    ("https://mandatepay-zisen-dph6eucmf4gjckct.eastasia-01.azurewebsites.net",
     "README.md", "live URL", False),
    ("https://github.com/niu602/mandatepay",
     "README.md", "GitHub 仓库", False),
    ("sb-mtet153479750@personal.example.com",
     "WEEK1_REVIEW.md", "沙箱买家账号", False),
    ("Best Use of Agentic Commerce",
     "README.md", "目标奖项", False),

    # —— 意图验证 ——
    ("60/60",
     "LLM_INTENT_VERIFICATION.md", "意图验证 60/60", False),
    ("verify_llm_zai.py",
     "LLM_INTENT_VERIFICATION.md", "验证脚本出处", False),
    ("hand-enumerated",
     "README.md", "期望结果人工枚举（README 英文口径）", False),
    ("人工枚举",
     "LLM_INTENT_VERIFICATION.md", "期望结果人工枚举（报告中文口径，稿子用英文）", True),

    # —— 压测 ——
    ("650",
     "STRESS_REPORT.md", "650 次判定", False),
    ("550",
     "STRESS_REPORT.md", "550 条对抗样本", False),
    ("26.15%",
     "STRESS_REPORT.md", "F 类漏洞占比（修复前口径）", False),
    ("170",
     "STRESS_REPORT.md", "F 类漏洞例数", False),
    ("found and fixed",
     "README.md", "found and fixed 措辞", False),
    ("0 false rejects",
     "README.md", "修复后 0 误拒", False),

    # —— MCP 论证 ——
    ("28",
     "agent/mcp_guarded.py", "MCP 工具总数", False),
    ("mcp_guarded.py",
     "README.md", "MCP 闸门出处", False),
    ("invoices.list",
     "agent/mcp_guarded.py", "唯一开放的只读工具", False),
    ("orders.capture",
     "agent/mcp_guarded.py", "动钱工具举例", False),

    # —— AG Studio ——
    ("AG Studio",
     "README.md", "AG Studio dashboard-as-code", False),
    ("createStudio",
     "static/budget.html", "dashboard-as-code API", False),
    ("falls back",
     "static/budget.html", "自动回退", False),
    ("classic",
     "static/budget.html", "classic 视图存在", False),

    # —— 架构 ——
    ("PAYER_ACTION_REQUIRED",
     "DEMO_SCRIPT_EN.md", "待人工授权状态", False),
    ("three-layer",
     "README.md", "三层 Mandate", False),
    ("trace_id",
     "README.md", "trace_id", False),
]

# 稿子里允许出现的占位符（其它 <!-- --> 都算未替换）
ALLOWED_PLACEHOLDERS = {"VIDEO_URL"}


def main() -> int:
    if not DOC.exists():
        print(f"❌ 找不到 {DOC}")
        return 1

    text = DOC.read_text(encoding="utf-8")
    problems: list[str] = []

    # ── ① 结构检查 ──
    print("=== ① 结构 ===")
    for sec in REQUIRED_SECTIONS:
        if re.search(rf"^##\s+{re.escape(sec)}\s*$", text, re.M):
            print(f"  ✅ 段落齐全：{sec}")
        else:
            problems.append(f"缺少评分段落：{sec}")
            print(f"  ❌ 缺少段落：{sec}")

    # 占位符：只允许 VIDEO_URL
    placeholders = set(re.findall(r"<!--\s*(.*?)\s*-->", text))
    unexpected = placeholders - ALLOWED_PLACEHOLDERS
    if unexpected:
        problems.append(f"有未预期的占位符：{unexpected}")
        print(f"  ❌ 意外占位符：{unexpected}")
    else:
        print(f"  ✅ 占位符只有 {sorted(placeholders)}（VIDEO_URL 待用户回填）")

    # 视频链接应出现两次（头部 + 附录）
    n_video = text.count("<!-- VIDEO_URL -->")
    if n_video < 2:
        problems.append(f"VIDEO_URL 占位符只出现 {n_video} 次（头部 + 附录应各一处）")
    print(f"  {'✅' if n_video >= 2 else '❌'} VIDEO_URL 占位符 {n_video} 处")

    # 中文残留（代码块外）
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    cn = re.findall(r"[\u4e00-\u9fff]+", body)
    if cn:
        problems.append(f"有中文残留 {len(cn)} 处：{cn[:5]}")
        print(f"  ❌ 中文残留 {len(cn)} 处")
    else:
        print("  ✅ 全英文（代码块外零中文）")

    # ── ② 事实检查 ──
    print("\n=== ② 事实（稿子 ↔ 仓库源文件 双向核对）===")

    def norm(s: str) -> str:
        """归一化空白 —— 稿子里的换行不该让精确串匹配失败。"""
        return re.sub(r"\s+", " ", s)

    text_n = norm(text)
    for claim, src_rel, desc, one_way in FACT_CLAIMS:
        src_path = ROOT / src_rel
        if not src_path.exists():
            problems.append(f"源文件不存在：{src_rel}")
            print(f"  ❌ 源文件缺失：{src_rel}")
            continue
        src_n = norm(src_path.read_text(encoding="utf-8"))
        claim_n = norm(claim)
        in_doc = claim_n in text_n
        in_src = claim_n in src_n
        if one_way:
            # 单向：只要求源文件里有（源文件是中文口径，英文稿不该出现）
            if in_src:
                print(f"  ✅ {desc:24s} （源文件口径存在）")
            else:
                problems.append(f"源文件里找不到：{desc} 「{claim}」（{src_rel}）")
                print(f"  ❌ {desc:24s} {src_rel} 无")
            continue
        if in_doc and in_src:
            print(f"  ✅ {desc:24s} 「{claim[:38]}」")
        elif in_doc and not in_src:
            problems.append(f"稿子写了但源文件里找不到：{desc} 「{claim}」（{src_rel}）")
            print(f"  ❌ {desc:24s} 稿子有、{src_rel} 无")
        elif not in_doc and in_src:
            problems.append(f"源文件里有但稿子漏了：{desc} 「{claim}」（{src_rel}）")
            print(f"  ⚠️ {desc:24s} {src_rel} 有、稿子无")

    # ── ③ 措辞守卫 ──
    print("\n=== ③ 措辞 ===")
    # 26.15% 必须紧邻 "found and fixed" 或 "pre-fix"，不能让评委误读成现存漏洞
    for m in re.finditer(r"26\.15%", text):
        window = text[max(0, m.start() - 600): m.start() + 600].lower()
        ok_ctx = (
            "pre-fix" in window          # 修复前口径
            or "found and fixed" in window
            or "found" in window and "fixed" in window
            or "found it and closed it" in window
        )
        if ok_ctx:
            print("  ✅ 26.15% 出现在「修复前 / found and fixed」语境里")
        else:
            problems.append("26.15% 附近没有「pre-fix / found and fixed」语境 —— 会被误读为现存漏洞")
            print("  ❌ 26.15% 缺少口径说明")

    # 不能出现"密码"类断言（沙箱买家仓库里没有密码）
    if re.search(r"sb-mtet153479750@\S+\s*/\s*\S+", text):
        problems.append("稿子写了沙箱买家密码 —— 仓库里没有密码，别编")
        print("  ❌ 出现沙箱买家密码")
    else:
        print("  ✅ 沙箱买家只给邮箱，没编密码")

    print("\n" + "=" * 66)
    if problems:
        print(f"❌ {len(problems)} 个问题：")
        for p in problems:
            print(f"   · {p}")
        print("=" * 66)
        return 1
    print("✅ 全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
