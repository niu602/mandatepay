#!/usr/bin/env python
"""冻结前自检：/budget 双视图可切换 + 控制台无 License 横幅（T5）。

为什么不用 --dump-dom
---------------------
`chrome --headless --dump-dom` 只能抓一次静态 DOM，**点不了按钮**。
"双视图可切换"要真的点一下那个按钮再读 DOM，所以这里用 CDP
（Chrome DevTools Protocol）驱动：起一个 Chrome，连上 `WebSocket`，
发 `Page.navigate` / `Runtime.evaluate` / `Page.captureScreenshot`。

"无痕窗口"等价怎么保证
----------------------
不继承任何浏览器 profile：每次跑都用**全新的临时 `--user-data-dir`**，
里面没有任何 cookie / localStorage，效果等同于开一个干净的无痕窗口。

抓什么
------
1. 五个页面各导航一次，记录 title、可见文本节点里是否出现控制台级错误
2. `/budget`：AG Studio 视图是否真的渲染出内容（`#studio` 文本量）
3. 点「切换到原版视图」→ 读 `#classicCard` 是否显示，KPI 卡片是否填充
4. 再点「切回 AG Studio」→ 回到 ①，证明**双向**可切换
5. 全程收集 console / log 事件，断言没有 `License Key Not Found`

用法
----
```bash
python scripts/check_budget_views.py --url https://xxx.azurewebsites.net
```
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PAGES = ["/", "/constraints", "/approvals", "/audit", "/budget"]
BANNER_MARK = "license key not found"


# ── 极简 CDP 客户端 ────────────────────────────────────────────────
class CDP:
    def __init__(self, ws, chrome_proc):
        self.ws = ws
        self.proc = chrome_proc
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.console: list[tuple[str, str]] = []   # (source, text)
        self.bad_responses: list[tuple[int, str]] = []   # (status, url)
        self._pump = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._pending:
                    fut = self._pending.pop(msg["id"])
                    if not fut.done():
                        fut.set_result(msg)
                    continue
                method = msg.get("method", "")
                p = msg.get("params", {}) or {}
                if method == "Runtime.consoleAPICalled":
                    txt = " ".join(
                        str(a.get("value", a.get("description", "")))
                        for a in p.get("args", [])
                    )
                    self.console.append((p.get("type", "log"), txt))
                elif method == "Log.entryAdded":
                    e = p.get("entry", {})
                    self.console.append((e.get("level", "log"), e.get("text", "")))
                elif method == "Runtime.exceptionThrown":
                    d = p.get("exceptionDetails", {})
                    self.console.append(("exception", d.get("text", "") + " " +
                                         str((d.get("exception") or {}).get("description", ""))))
                elif method == "Network.responseReceived":
                    r = p.get("response", {})
                    if int(r.get("status", 0) or 0) >= 400:
                        self.bad_responses.append((int(r["status"]), r.get("url", "")))
        except Exception:
            pass

    async def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout=60)

    async def js(self, expr: str):
        r = await self.send("Runtime.evaluate", expression=expr,
                            returnByValue=True, awaitPromise=True)
        return (r.get("result", {}).get("result") or {}).get("value")

    async def close(self):
        self._pump.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()


async def _wait_ws(port: int, timeout: float = 25.0) -> str:
    import websockets
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
                targets = json.load(r)
            for t in targets:
                if t.get("type") == "page":
                    return t["webSocketDebuggerUrl"]
            last = "no page target"
        except Exception as e:                                  # noqa: BLE001
            last = repr(e)
        await asyncio.sleep(0.4)
    raise RuntimeError(f"Chrome 调试端口未就绪: {last}")


STATE_JS = r"""
(() => {
  const g = (id) => document.getElementById(id);
  const txt = (el) => el ? (el.innerText || '').trim() : null;
  return JSON.stringify({
    currentView: window.currentView,
    agHidden: g('agCard') ? g('agCard').hidden : null,
    classicHidden: g('classicCard') ? g('classicCard').hidden : null,
    studioChars: txt(g('studio')) ? txt(g('studio')).length : 0,
    studioHead: (txt(g('studio')) || '').slice(0, 60).replace(/\n/g, ' | '),
    agStatus: txt(g('agStatus')),
    kpiCards: g('kpis') ? g('kpis').children.length : 0,
    vendorChars: txt(g('vendors')) ? txt(g('vendors')).length : 0,
    thresholdRows: g('thresholds') ? g('thresholds').children.length : 0,
    alert: txt(g('alert')),
    banner: (document.body.innerText.match(/License Key[^\n]*/i) || [''])[0]
  });
})()
"""


async def run(base: str, outdir: Path, wait: float) -> int:
    import websockets

    port = 9344
    profile = Path(tempfile.mkdtemp(prefix="cdp-profile-"))
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-extensions", "--no-first-run", "--no-default-browser-check",
         f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}",
         "--window-size=1440,1400",
         "--hide-scrollbars",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    problems: list[str] = []
    try:
        ws_url = await _wait_ws(port)
        async with websockets.connect(ws_url, max_size=60 * 1024 * 1024) as ws:
            c = CDP(ws, proc)
            for m in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
                await c.send(m)

            print("=" * 74)
            print("T5 自检 —— 干净 profile（等效无痕）打开线上五页 + /budget 双视图")
            print("=" * 74)
            print(f"目标: {base}\n")

            # ① 五页扫一遍，只看能否加载、有没有 error 级日志
            for p in PAGES:
                before = len(c.console)
                bad_before = len(c.bad_responses)
                await c.send("Page.navigate", url=base + p)
                await asyncio.sleep(3.0)
                title = await c.js("document.title")
                h1 = await c.js("(document.querySelector('h1')||{}).innerText||''")
                errs = [t for (lv, t) in c.console[before:]
                        if lv in ("error", "exception")]
                raw_bad = c.bad_responses[bad_before:]
                bad = [(s, u) for (s, u) in raw_bad if "favicon" not in u]
                favicon_404 = [u for (s, u) in raw_bad if "favicon" in u]
                # 控制台里那条 404 如果只对应 favicon.ico，就不算问题
                if errs and not bad:
                    errs = [e for e in errs if "404" not in e]
                flag = "OK " if (not errs and not bad) else "ERR"
                print(f"  [{flag}] {p:<14} title={str(title)[:38]:<40} h1={str(h1)[:26]}")
                for e in errs[:2]:
                    print(f"          └ error: {e[:110]}")
                    problems.append(f"{p} 有 error 级日志: {e[:80]}")
                for s, u in bad[:3]:
                    print(f"          └ HTTP {s}: {u[-70:]}")
                    problems.append(f"{p} 有 HTTP {s}: {u[-60:]}")
                if favicon_404:
                    print(f"          └ （忽略 favicon.ico 404 ×{len(favicon_404)}）")

            # ② /budget：AG Studio 视图（轮询等渲染，B1 冷启动需要时间）
            before = len(c.console)
            await c.send("Page.navigate", url=base + "/budget")
            s1 = None
            for i in range(int(max(wait, 5))):
                await asyncio.sleep(1.0)
                s1 = json.loads(await c.js(STATE_JS))
                if s1["studioChars"] >= 30:
                    print(f"     （AG Studio 于第 {i + 1}s 渲染完成）")
                    break
            await asyncio.sleep(1.0)
            s1 = json.loads(await c.js(STATE_JS))
            shot1 = await c.send("Page.captureScreenshot", format="png")
            (outdir / "budget-ag.png").write_bytes(base64.b64decode(shot1["result"]["data"]))

            print("\n  ① AG Studio 视图")
            print(f"     currentView={s1['currentView']}  agCard.hidden={s1['agHidden']}  "
                  f"classicCard.hidden={s1['classicHidden']}")
            print(f"     #studio 文本 {s1['studioChars']} 字符 → {s1['studioHead'][:50]}")
            print(f"     #agStatus = {str(s1['agStatus'])[:70]}")
            if s1.get("alert"):
                print(f"     #alert = {str(s1['alert'])[:100]}")
            if s1["currentView"] != "ag" or s1["agHidden"] is not False:
                problems.append("AG Studio 视图未处于默认显示状态")
            if s1["studioChars"] < 30:
                problems.append(f"AG Studio 视图内容过少({s1['studioChars']} 字符)")

            # ③ 点按钮切到原版
            clicked = await c.js(
                "(()=>{const b=document.querySelector('#agCard button.btn.gray');"
                "if(!b) return 'no-button'; b.click(); return 'clicked';})()"
            )
            await asyncio.sleep(1.5)
            s2 = json.loads(await c.js(STATE_JS))
            for _ in range(10):                       # 原版视图数据也是异步拉的
                if s2["kpiCards"] >= 3:
                    break
                await asyncio.sleep(1.0)
                s2 = json.loads(await c.js(STATE_JS))
            shot2 = await c.send("Page.captureScreenshot", format="png")
            (outdir / "budget-classic.png").write_bytes(base64.b64decode(shot2["result"]["data"]))

            print("\n  ② 点击「切换到原版视图」后（按钮返回: %s）" % clicked)
            print(f"     currentView={s2['currentView']}  agCard.hidden={s2['agHidden']}  "
                  f"classicCard.hidden={s2['classicHidden']}")
            print(f"     KPI 卡片 {s2['kpiCards']} 张，供应商区 {s2['vendorChars']} 字符，"
                  f"阈值 {s2['thresholdRows']} 行")
            if s2["currentView"] != "classic" or s2["classicHidden"] is not False:
                problems.append("点击后未切到原版视图")
            if s2["kpiCards"] < 3:
                problems.append(f"原版视图 KPI 卡片过少({s2['kpiCards']})")

            # ④ 再切回去
            await c.js("(()=>{const b=document.querySelector('#classicCard button.btn.gray');"
                       "if(b) b.click();})()")
            await asyncio.sleep(1.2)
            s3 = json.loads(await c.js(STATE_JS))
            print("\n  ③ 点击「切回 AG Studio」后")
            print(f"     currentView={s3['currentView']}  agCard.hidden={s3['agHidden']}  "
                  f"classicCard.hidden={s3['classicHidden']}")
            if s3["currentView"] != "ag" or s3["agHidden"] is not False:
                problems.append("无法切回 AG Studio 视图")

            # ⑤ License 横幅
            msgs = c.console[before:]
            hits = [(lv, t) for (lv, t) in msgs if BANNER_MARK in t.lower()]
            dom_banner = s1.get("banner") or ""
            print("\n  ④ License 横幅检查（本轮 /budget 会话）")
            print(f"     控制台消息 {len(msgs)} 条，命中 'License Key Not Found' {len(hits)} 条")
            for lv, t in hits[:3]:
                print(f"       └ [{lv}] {t[:110]}")
            print(f"     DOM 可见文本命中: {dom_banner[:110] if dom_banner else '（无）'}")
            if hits or dom_banner:
                problems.append("出现 License Key Not Found 横幅")

            print("\n  截图: " + str(outdir / "budget-ag.png") + " , " + str(outdir / "budget-classic.png"))
            await c.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:                                        # noqa: BLE001
            try:
                proc.kill()
            except Exception:                                    # noqa: BLE001
                pass
        shutil.rmtree(profile, ignore_errors=True)

    print("\n" + "-" * 74)
    if problems:
        print(f"❌ 未通过，{len(problems)} 个问题：")
        for p in problems:
            print("   - " + p)
        return 1
    print("✅ 通过 —— 五页加载正常、/budget 双视图双向可切换、无 License Key Not Found")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="T5 /budget 双视图 + license 横幅自检")
    ap.add_argument("--url", required=True, help="站点根地址，如 https://xxx.azurewebsites.net")
    ap.add_argument("--out", default=".tmp_gh", help="截图输出目录")
    ap.add_argument("--wait", type=float, default=9.0, help="/budget 等待 AG Studio 渲染的秒数")
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(a.url.rstrip("/"), outdir, a.wait))


if __name__ == "__main__":
    sys.exit(main())
