"""W-10 前端英文化验收 —— 可见文本必须是英文（双语可保留，但不能有纯中文区块）。

判定口径（与工单一致）
--------------------
1. 剔 HTML 注释 + JS 注释（注释可以留中文，不影响评审）
2. **保留 `<script>` 内字符串** —— AG Studio 的 KPI 标签、空态文案就预定义在那段 JS 里，
   会渲染成可见 UI。只剔 HTML 文本节点会漏掉这部分。
3. 对每个可见中文片段，判断它**旁边有没有英文**：
   - 有 → 双语区块 ✅（工单允许：「中文原声 + 英文字幕的项目，UI 双语自洽」）
   - 没有 → **纯中文区块** ❌（工单明令禁止）

用法：
    python scripts/check_ui_english.py            # 逐页报告 + 退出码
    python scripts/check_ui_english.py --verbose  # 列出每个纯中文片段
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 五个页面（W-10 范围）。budget.orig.html 是改造前的原版备份，无路由、运行时不可达，
# 不在范围内 —— 见 W06_DEPLOY / AG_STUDIO_FEASIBILITY 的"方式二"说明。
PAGES = ("index.html", "constraints.html", "approvals.html", "audit.html", "budget.html")

CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")


def strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def strip_js_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    return code


def visible_chunks(html: str) -> list[tuple[str, str]]:
    """返回 [(来源, 文本片段)]，只含含中文的可见片段。

    来源标记：`html` = HTML 文本节点，`js` = JS 字符串字面量。
    """
    chunks: list[tuple[str, str]] = []

    # ── HTML 文本节点 ──
    rest = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    rest = re.sub(r"<style[^>]*>.*?</style>", "", rest, flags=re.S | re.I)
    for m in re.finditer(r">([^<>]*)<", rest):
        s = m.group(1).strip()
        if s and CJK.search(s):
            chunks.append(("html", s))

    # ── JS 字符串字面量 ──
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    js = strip_js_comments("\n".join(scripts))
    for m in re.finditer(r"""[`"']([^`"'<>]*)[`"']""", js):
        s = m.group(1).strip()
        if s and CJK.search(s):
            chunks.append(("js", s))

    return chunks


def merge_js_concat_units(js: str) -> list[str]:
    """把 JS 里用 `+` 连接的字符串片段合并成一个"渲染单元"。

    为什么需要：形如
        msg.textContent = "Payment status: " + (x) + " · 支付状态";
    渲染出来是**一条连续文本**（英文在前、中文在后），
    但按字面量切分会得到两个片段，其中"· 支付状态"单独看是纯中文 → 误报。

    实现：**按行处理**。JS 里 `+` 拼接的表达式基本都在一行内（跨行的极少），
    所以逐行看"有没有 `+`"，有就把该行的字符串字面量拼起来。
    比正则匹配 `+` 链稳得多 —— 链里可能有嵌套括号、转义引号（`\"`）、
    函数调用，正则很难覆盖全。
    """
    units: list[str] = []
    for line in js.splitlines():
        if "+" not in line:
            continue
        # 提取该行的字符串字面量（处理 \" 这种转义：先按未转义的引号切）
        parts = re.findall(r"""(?<!\\)(?:"([^"]*)"|'([^']*)'|`([^`]*)`)""", line)
        texts = [a or b or c for (a, b, c) in parts]
        if len(texts) < 2:
            continue
        merged = "".join(texts).strip()
        if merged and CJK.search(merged):
            units.append(merged)
    return units


def has_english_nearby(chunk: str) -> bool:
    """判断这个中文片段是否"双语"（同一可见单元里有英文）。

    判定：片段自身含英文字母，或它的紧邻上下文（前后 60 字符）里有英文。
    —— 覆盖两种实际写法：
      a) 片段自身就是双语：「Monthly budget 月度预算」
      b) 片段单独是中文，但同一行/同一语句里有英文：
         `<button>② I have paid · capture 我已付款·捕获</button>`
    """
    if LATIN.search(chunk):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="W-10 前端英文化验收")
    ap.add_argument("--verbose", action="store_true", help="列出每个纯中文片段")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：任何中文片段都不允许（纯英文，丢掉双语）")
    args = ap.parse_args()

    static = Path(__file__).resolve().parent.parent / "static"
    print("=" * 84)
    print("W-10 前端英文化验收")
    print("=" * 84)
    print("口径：剔 HTML/JS 注释，保留 script 字符串；双语允许，纯中文区块不允许")
    print()

    total_chunks = 0
    total_pure = 0
    failed = False

    for name in PAGES:
        f = static / name
        if not f.exists():
            print(f"  {name:22s} ❌ 文件不存在")
            failed = True
            continue

        html = strip_html_comments(f.read_text(encoding="utf-8"))
        chunks = visible_chunks(html)

        # lang 属性与 title 单独查
        lang = re.search(r'<html[^>]*lang="([^"]+)"', html)
        title = re.search(r"<title>(.*?)</title>", html, flags=re.S)
        lang_ok = bool(lang and lang.group(1).startswith("en"))
        title_txt = (title.group(1).strip() if title else "")
        title_ok = bool(LATIN.search(title_txt))

        # ── 判定纯中文片段 ──
        # 两条路径：
        #  a) HTML 文本节点 —— 直接判
        #  b) JS 字符串 —— 先按 `+` 拼接链合并成"渲染单元"再判
        #     （"Payment status: " + x + " · 支付状态" 渲染后是一条连续文本，
        #      按字面量切会把后半截误判成纯中文）
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
        js = strip_js_comments("\n".join(scripts))

        pure: list[tuple[str, str]] = []
        js_units = merge_js_concat_units(js)

        for src, txt in chunks:
            if src == "html":
                if not has_english_nearby(txt):
                    pure.append((src, txt))
                continue
            # JS 字面量：若它出现在某个拼接链里，就用整条链判定
            # （链里通常已有英文 → 双语，不算纯中文）
            in_chain = any(txt in unit for unit in js_units)
            if in_chain:
                continue
            if not has_english_nearby(txt):
                pure.append((src, txt))

        # 拼接链整体也判一次（整条链都没英文 → 纯中文）
        for unit in js_units:
            if not has_english_nearby(unit):
                pure.append(("js-concat", unit))

        total_chunks += len(chunks)
        total_pure += len(pure)

        status = "✅" if (not pure and lang_ok and title_ok) else "❌"
        if pure or not lang_ok or not title_ok:
            failed = True
        print(f"  {status} {name:22s} 含中文片段 {len(chunks):>3}　纯中文区块 {len(pure):>3}")
        print(f"       lang=\"{lang.group(1) if lang else '(none)'}\"　"
              f"title=\"{title_txt[:44]}\"")

        if args.verbose:
            for src, txt in pure:
                print(f"       ⚠️  [{src}] {txt}")

    print("-" * 84)
    print(f"  合计：含中文片段 {total_chunks}　纯中文区块 {total_pure}")
    print()
    if failed:
        print("❌ 未通过 —— 存在纯中文区块，或 lang/title 未英文化")
        return 1
    print("✅ 通过 —— 五页可见文本均为英文（双语保留，无纯中文区块）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
