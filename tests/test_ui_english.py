"""W-10 前端英文化的验收测试。

工单 DoD：「五页逐页截图自查，可见区域无纯中文段落；/budget 双视图都要过一遍」

这个文件做两件事：
1. **静态检查**（快，每次跑测试都跑）—— 读 HTML 源码，判定有无纯中文可见区块
2. **结构检查** —— lang="en"、title 含英文、关键 UI 元素存在

「真实浏览器渲染后」的检查在 `scripts/check_ui_rendered.py`（需要 Chrome，
不进单元测试 —— CI/评测机上不一定有浏览器）。两者互补：
静态检查抓源码里的纯中文，渲染检查抓 JS 运行时插入的纯中文。

运行：python -m pytest tests/ -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 复用检查工具的核心逻辑（**不要在测试里重写一套** —— 之前就是这么翻车的：
# 测试自己写的判定比工具简单，测出来的"通过"和工具的结论对不上）
from check_ui_english import (  # noqa: E402
    PAGES,
    has_english_nearby,
    merge_js_concat_units,
    strip_js_comments,
    visible_chunks,
)

LATIN = re.compile(r"[A-Za-z]")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _read(name: str) -> str:
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def _pure_chinese_units(name: str) -> list[tuple[str, str]]:
    """找出该页所有"纯中文可见单元"。

    判定与 scripts/check_ui_english.py 完全一致：
      · HTML 文本节点 → 直接判
      · JS 字符串 → 先按 `+` 拼接链合并成渲染单元再判
        （"Payment status: " + x + " · 支付状态" 渲染后是一条连续文本，
          按字面量切会把后半截误判成纯中文）
    """
    html = _read(name)
    chunks = visible_chunks(html)
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    js = strip_js_comments("\n".join(scripts))
    units = merge_js_concat_units(js)

    pure: list[tuple[str, str]] = []
    for src, txt in chunks:
        if src == "html":
            if not has_english_nearby(txt):
                pure.append((src, txt))
            continue
        if any(txt in unit for unit in units):
            continue  # 属于某个拼接链，用整条链判
        if not has_english_nearby(txt):
            pure.append((src, txt))
    for unit in units:
        if not has_english_nearby(unit):
            pure.append(("js-concat", unit))
    return pure


# ──────────────────────────────────────────────────────
# 1. 静态：无纯中文可见区块
# ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", PAGES)
def test_no_pure_chinese_blocks(name):
    """每个可见中文片段都必须双语（英文在旁边）。

    工单口径：双语可以保留（中文原声 + 英文字幕的项目，UI 双语自洽），
    但**不能出现纯中文的可见区块**。
    """
    pure = _pure_chinese_units(name)
    assert not pure, (
        f"{name} 有 {len(pure)} 处纯中文可见区块：\n  "
        + "\n  ".join(f"[{s}] {t[:60]}" for s, t in pure[:8])
    )


@pytest.mark.parametrize("name", PAGES)
def test_lang_is_english(name):
    """`<html lang="en">` —— 评委浏览器与读屏软件按语言处理。"""
    html = _read(name)
    m = re.search(r"<html[^>]*lang=\"([^\"]+)\"", html)
    assert m, f"{name} 没有 lang 属性"
    assert m.group(1).startswith("en"), f"{name} 的 lang 应为 en，实际 {m.group(1)!r}"


@pytest.mark.parametrize("name", PAGES)
def test_title_has_english(name):
    """`<title>` 必须含英文（原来四个是纯中文）。"""
    html = _read(name)
    m = re.search(r"<title>(.*?)</title>", html, flags=re.S)
    assert m, f"{name} 没有 title"
    assert LATIN.search(m.group(1)), f"{name} 的 title 应含英文：{m.group(1)!r}"


# ──────────────────────────────────────────────────────
# 2. 关键 UI 元素存在（英文化时最容易漏掉的）
# ──────────────────────────────────────────────────────

def test_ag_studio_kpi_titles_are_bilingual():
    """AG Studio 的 KPI 标题是**代码预定义**的（createStudio/setState），
    不是 HTML 文本节点 —— 改文案要改那段 JS。工单特别点名了这一处。"""
    html = _read("budget.html")
    for label in ("Monthly Budget", "Committed Spend", "Remaining", "Completed",
                  "Pending", "Today", "Used %", "Projected", "Vendors", "Vendor",
                  "Amount", "Count"):
        assert label in html, f"AG Studio KPI 标签缺英文：{label}"


def test_budget_has_both_views():
    """`/budget` 双视图在同一个文件里：AG Studio + classic。"""
    html = _read("budget.html")
    assert 'id="studio"' in html, "缺 AG Studio 容器"
    assert 'id="classicCard"' in html, "缺 classic 回退视图容器"
    assert "toggleView()" in html, "缺视图切换函数"


def test_view_toggle_buttons_bilingual():
    """两个切换按钮都要双语。"""
    html = _read("budget.html")
    assert "Switch to the classic view" in html
    assert "Back to AG Studio" in html


def test_capture_button_bilingual():
    """「我已付款·捕获」是 W-14 视频里的关键按钮，必须双语。

    （中文旁白 + 英文字幕的项目，按钮双语才自洽）
    """
    for name in ("approvals.html", "audit.html"):
        html = _read(name)
        assert "I have paid" in html, f"{name} 的捕获按钮缺英文"
        assert "我已付款" in html, f"{name} 的捕获按钮缺中文"


def test_constraint_examples_are_english():
    """示例输入改成英文了 —— 因为 LLM 对英文的解析能力必须验证过。

    锁定这条防止有人改回中文（那会让英文评委看不懂示例）。
    """
    html = _read("constraints.html")
    assert "SaaS budget 200 dollars a month" in html, "约束示例应为英文"
    assert "Buy Notion Pro for 10 dollars" in html, "采购示例应为英文"


def test_nav_is_bilingual_on_every_page():
    """每个**有导航的**页面，导航项都要双语。

    （`index.html` 是 PayPal 支付测试页，故意没有导航 —— 它是最短路径，
      评委点开就能付款。测试要适配这个事实，不能假设五页都有 nav。）
    """
    for name in ("constraints.html", "approvals.html", "audit.html", "budget.html"):
        html = _read(name)
        m = re.search(r"<nav[^>]*>(.*?)</nav>", html, flags=re.S)
        assert m, f"{name} 没有 nav"
        nav = m.group(1)
        for txt in re.findall(r">([^<>]*)<", nav):
            txt = txt.strip()
            if txt and CJK.search(txt):
                assert LATIN.search(txt), f"{name} 导航项纯中文：{txt!r}"


def test_index_page_has_no_nav_intentionally():
    """`index.html` 故意不加导航 —— 它是最短路径（打开就能付款）。

    锁定这个事实：将来有人"顺手补个导航"反而会破坏演示的最短路径设计。
    """
    html = _read("index.html")
    assert "<nav" not in html, "index.html 是最短路径页，不应加导航"
    # 但标题与金额说明必须双语
    assert "PayPal Sandbox" in html
    assert "Amount" in html


def test_budget_orig_is_marked_as_archived():
    """budget.orig.html 必须明确标注"归档快照，不是运行中的页面"。

    否则评委翻到 static/ 会以为有两个预算页。
    """
    html = _read("budget.orig.html")
    head = html[:2000]
    assert "ARCHIVED SNAPSHOT" in head, "budget.orig.html 缺少归档标注"
    assert "no route" in head, "budget.orig.html 应说明无路由"
    # 也不能有 href 指向它（它不可达）
    for name in ("index.html", "constraints.html", "approvals.html", "audit.html", "budget.html"):
        assert "budget.orig" not in _read(name), f"{name} 不应引用 budget.orig.html"


def test_no_frontend_uses_removed_readme_patch():
    """README_PATCH.md 已删（W-09），仓库里不应再有引用。"""
    for f in ROOT.rglob("*.md"):
        if ".venv" in str(f) or "node_modules" in str(f) or ".workbuddy" in str(f):
            continue
        assert f.name != "README_PATCH.md", "README_PATCH.md 应已删除"


# ──────────────────────────────────────────────────────
# 3. 回归保护：模板字符串里的 HTML 也必须被检查
#    （2026-11-08 线上验收时发现的漏检）
# ──────────────────────────────────────────────────────

def test_template_string_html_is_inspected():
    """`<script>` 块内模板字符串里的 HTML 文本节点必须进入检查范围。

    **为什么需要这条**：approvals.html / audit.html 把详情面板整块写在
    `<script>` 内的模板字符串里（`` el.innerHTML = `...` ``）。
    而检查器原来把 `<script>` 块整体剥掉再找 `>文本<` ——
    等于**漏检了这两页绝大部分可见文本**。

    实测漏掉（且两个检查器都报"通过"）：
      `<span class="k">供应商</span>`   审批详情面板
      `layerBox('Intent 层', ...)`      审计页三层标签
    """
    from check_ui_english import visible_chunks

    for name in ("approvals.html", "audit.html"):
        html = _read(name)
        sources = {src for src, _ in visible_chunks(html)}
        assert "js-html" in sources, (
            f"{name} 的模板字符串 HTML 未被检查 —— "
            f"可见文本范围有缺口（当前来源: {sorted(sources)}）"
        )


def test_detects_pure_chinese_inside_template_string():
    """反向验证：模板字符串里的纯中文标签必须被抓到。

    这条比上一条更值钱 —— 上一条只证明"检查范围包含模板字符串"，
    这条证明"范围内的纯中文确实会被标红"。
    """
    from check_ui_english import visible_chunks

    html = _read("approvals.html")
    # 模拟 W-10 漏改的状态：详情面板的标签只剩中文
    broken = html.replace('<span class="k">Vendor 供应商</span>',
                          '<span class="k">供应商</span>')
    chunks = visible_chunks(broken)
    hits = [txt for _, txt in chunks if txt == "供应商"]
    assert hits, "模板字符串里的纯中文标签「供应商」应被检出"
