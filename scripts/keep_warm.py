"""Azure F1 保温探针 —— 低频 ping + 冷启动耗时记录。

为什么需要这个脚本
------------------
Azure App Service F1 **不支持 Always On**：闲置后实例可能被平台卸载，
下次访问要走冷启动（实测 2-5 CPU 秒）。而 F1 有 **60 CPU 分钟/天硬上限**，
所以保温必须是"低频"的 —— 高频 ping 本身耗 CPU，可能把配额 ping 挂。

关键约束（v4 实测核算）
----------------------
探针**必须跑在 F1 实例外面**。如果探针跑在同一个实例里，实例被卸载时
探针也一起没了 —— 根本起不到保温作用。本脚本设计为在**本地机器**
（或任何常开设备）上跑，通过公网 URL 访问线上服务。

CPU 预算核算（保守假设：1/3 的探针落在冷启动上）
------------------------------------------------
    间隔      次数/天   冷启动 CPU 秒   热实例 CPU 秒   合计/天    占 60 分钟预算
    10 分钟      144         245            29         4.56 min7.6%   ← v1/v2 的方案，作废
    30 分钟       48          82            10         1.52 min     2.5%   ← 默认
    60 分钟       24          41             5         0.76 min     1.3%

→ 30 分钟间隔约 1.5 CPU 分钟/天，远低于工单 DoD 的"当天< 10 CPU 分钟"。

用法
----
    # 单次探测（手动预热 / 一次性检查）
    python scripts/keep_warm.py --url https://xxx.azurewebsites.net --once

    # 常驻保温（默认 30 分钟一次）
    python scripts/keep_warm.py --url https://xxx.azurewebsites.net

    # 演示前预热：连打几个页面把首屏缓存与 JIT 都热起来
    python scripts/keep_warm.py --url https://xxx.azurewebsites.net --warm

    # 只测冷启动耗时（部署后验收用）
    python scripts/keep_warm.py --url https://xxx.azurewebsites.net --measure

本文件不硬编码任何密钥，也不硬编码任何 URL —— URL 必须由参数传入。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 默认间隔 30 分钟 —— 见上方 CPU 预算核算
DEFAULT_INTERVAL_MIN = 30

# 演示前预热要打 的页面（顺序有意义：先轻后重，唤醒实例优先打最轻的）
WARM_PATHS = (
    "/healthz",
    "/",
    "/constraints",
    "/approvals",
    "/audit",
    "/budget",
)

# 探测超时。设长一点，因为冷启动本身可能就要 5 秒以上；
# 但不能太长，否则脚本会挂住而不是快速失败。
DEFAULT_TIMEOUT = 30


def _get(url: str, timeout: int = DEFAULT_TIMEOUT, use_proxy: bool = True,
         max_bytes: int = 2048) -> tuple[int, float, str]:
    """发一个 GET，返回 (状态码, 耗时秒, 响应体前若干字符)。

    刻意用 urllib 而不是 requests —— 保温脚本要能在任何只有标准库的
    环境里跑（评审期不能假设目标机器装了依赖）。

    **代理陷阱（实测踩到）**：`urllib` 默认读环境变量 `http_proxy` / `https_proxy`。
    开发机上如果设了代理，本地探针会被代理拦成 502/连接失败，
    误判成"服务已死"。所以：
      - 默认对 localhost / 127.0.0.1 **强制绕过代理**（本地自测场景）
      - 线上探测建议也绕过（公网 URL 不该走开发机的代理）
      - 真需要走代理时用 `--use-proxy`
    """
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "mandatepay-keepwarm/1.0"})

    opener = urllib.request.build_opener()
    if not use_proxy:
        # ProxyHandler({}) 表示"不使用任何代理"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes).decode("utf-8", "replace")
            return resp.status, time.perf_counter() - t0, body
    except urllib.error.HTTPError as exc:
        # 服务起来了但返回错误码 —— 对保温来说仍是"活的"
        return exc.code, time.perf_counter() - t0, ""
    except Exception as exc:
        # 连接失败 / 超时 —— 实例可能已被卸载
        return 0, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"


def _normalize(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _should_bypass_proxy(base: str, use_proxy: bool) -> bool:
    """决定是否绕过代理。

    默认策略：**一律绕过**。
    - localhost / 127.0.0.1：必须绕过（本地自测）
    - 公网 URL：也绕过 —— 开发机的代理是给"访问外网"用的，
      而我们要探测的是"自己的服务是否活着"，走代理只会在代理层看到 502
    只有显式 --use-proxy 才走代理。
    """
    if use_proxy:
        return False
    return True


def probe_once(base: str, path: str = "/healthz", timeout: int = DEFAULT_TIMEOUT,
               use_proxy: bool = False, max_bytes: int = 2048) -> dict:
    """探测一次，返回结果字典。

    max_bytes：读多少响应体。检查页面内容时要传足够大
    （budget.html 约 19KB，CDN 引用在第 203 行附近，默认 2048 会截断掉）。
    """
    url = base + path
    code, elapsed, body = _get(url, timeout, use_proxy=use_proxy, max_bytes=max_bytes)
    return {
        "url": url,
        "path": path,
        "status": code,
        "elapsed_s": round(elapsed, 3),
        "body_head": body[:200],
        "_body": body,
    }


def cmd_once(base: str, timeout: int, use_proxy: bool = False) -> int:
    r = probe_once(base, "/healthz", timeout, use_proxy)
    ok = r["status"] == 200
    print(f"[{'✅' if ok else '❌'}] {r['url']}")
    print(f"    状态 {r['status']}　耗时 {r['elapsed_s']:.3f}s")
    if r["body_head"]:
        print(f"    响应 {r['body_head']}")
    return 0 if ok else 1


def cmd_measure(base: str, timeout: int, use_proxy: bool = False) -> int:
    """测冷启动耗时：连续打 3 次 /healthz。

    第一次可能命中冷启动（实例刚被卸载），后面是热实例。
    这个对比就是 DoD 要的"首访vs 唤醒后"两个数据点。
    """
    print("=" * 66)
    print("冷启动耗时测量 —— 连续 3 次 /healthz")
    print("=" * 66)
    results = []
    for i in range(1, 4):
        r = probe_once(base, "/healthz", timeout, use_proxy)
        results.append(r)
        label = "第 1 次（可能命中冷启动）" if i == 1 else f"第 {i} 次（热实例）"
        status = "✅" if r["status"] == 200 else "❌"
        print(f"  {status} {label:<24} 状态 {r['status']:>3}　耗时 {r['elapsed_s']:>7.3f}s")
        # 让实例稳定再打下一发
        if i < 3:
            time.sleep(2)

    ok_results = [r for r in results if r["status"] == 200]
    print("-" * 66)
    if not ok_results:
        print("❌ 三次都没通 —— 服务未启动或 URL 不对")
        return 1
    first = results[0]["elapsed_s"]
    warm = [r["elapsed_s"] for r in ok_results[1:]] or [first]
    print(f"首访耗时     : {first:.3f}s")
    print(f"热实例耗时   : {min(warm):.3f}s（取最快的一次）")
    if first > 3:
        print(f"⚠️  首访 {first:.1f}s 明显偏高 → 命中冷启动。"
              f"演示前务必先访问一次预热。")
    else:
        print("✅ 首访够快（< 3s），演示前仍建议预热一次以防万一。")

    # 写一份 JSON 便于贴进 W-07 的验收记录
    out = {
        "url": base,
        "first_s": first,
        "warm_s": min(warm),
        "samples": [{"status": r["status"], "elapsed_s": r["elapsed_s"]} for r in results],
    }
    path = "cold_start_measure.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"已写入 {path}")
    return 0


def cmd_warm(base: str, timeout: int, use_proxy: bool = False) -> int:
    """演示前预热：按 WARM_PATHS 顺序打一遍。

    目的不只是"唤醒实例" —— 还要让浏览器/CDN 侧的缓存与 JIT 都热起来，
    评委第一次点开时才是秒开。
    """
    print("=" * 66)
    print(f"演示前预热 —— {base}")
    print("=" * 66)
    total = 0.0
    failed = []
    for path in WARM_PATHS:
        r = probe_once(base, path, timeout, use_proxy)
        total += r["elapsed_s"]
        ok = r["status"] == 200
        if not ok:
            failed.append(path)
        print(f"  {'✅' if ok else '❌'} {path:<14} 状态 {r['status']:>3}　{r['elapsed_s']:>7.3f}s")
        # 页面之间留一点间隔，别把刚起来的实例又压回去
        time.sleep(0.5)

    print("-" * 66)
    print(f"合计 {total:.3f}s")
    if failed:
        print(f"❌ 失败路径: {', '.join(failed)}")
        print("   ⚠️  其中 /budget 若失败，评委看到的会是 classic 回退视图"
              "（功能正常，但不是 AG Studio 渲染）")
        return 1
    print("✅ 全部路径可用，实例已热")
    return 0


def estimate_cpu_minutes_per_day(interval_min: float,
                                  cold_seconds: float = 5.0,
                                  warm_seconds: float = 0.3,
                                  cold_ratio: float = 0.34) -> dict:
    """估算保温探针的 CPU 消耗（F1 硬上限 60 CPU 分钟/天）。

    参数取保守值：
      - cold_seconds=5：冷启动实测 2-5 CPU 秒，取上限
      - warm_seconds=0.3：热实例一次静态响应 < 0.05 秒，
        但算上路由 + 中间件开销取 0.3 秒
      - cold_ratio=0.34：假设 1/3 的探针落在实例被卸载后的冷启动上

    F1 不支持 Always On，闲置后实例可能被卸载 → 冷启动比例不可忽略。
    """
    per_day = 24 * 60 / max(interval_min, 1e-9)
    cold_cost = per_day * cold_ratio * cold_seconds
    warm_cost = per_day * (1 - cold_ratio) * warm_seconds
    total_min = (cold_cost + warm_cost) / 60
    return {
        "pings_per_day": round(per_day, 1),
        "cold_seconds_total": round(cold_cost, 1),
        "warm_seconds_total": round(warm_cost, 1),
        "cpu_minutes_per_day": round(total_min, 2),
        "budget_minutes": 60.0,
        "pct_of_budget": round(total_min / 60.0 * 100, 1),
        "under_dod_threshold": total_min < 10.0,  # 工单 DoD：当天 < 10 CPU 分钟
    }


def cmd_loop(base: str, interval_min: float, timeout: int, max_pings: int,
             use_proxy: bool = False) -> int:
    """常驻保温循环。Ctrl-C 退出。

    max_pings > 0 时打满这么多次就退出（用于测试 / 有限时长运行）。
    """
    interval_s = interval_min * 60
    est = estimate_cpu_minutes_per_day(interval_min)
    print("=" * 66)
    print(f"保温探针启动 —— {base}")
    print(f"间隔 {interval_min:g} 分钟　超时 {timeout}s")
    print(f"CPU 预算估算：约 {est['cpu_minutes_per_day']} CPU 分钟/天"
          f"（{est['pings_per_day']:.0f} 次/天，占 F1 的 60 分钟预算 {est['pct_of_budget']}%）")
    if not est["under_dod_threshold"]:
        print(f"⚠️  超过工单 DoD 的「当天 < 10 CPU 分钟」—— 请把间隔调大")
    if interval_min < 30:
        print("⚠️  间隔低于 30 分钟 —— F1 只有 60 CPU 分钟/天，高频 ping 可能把配额 ping 挂")
    print("Ctrl-C 退出")
    print("=" * 66)

    n = 0
    consecutive_fail = 0
    try:
        while max_pings <= 0 or n < max_pings:
            n += 1
            r = probe_once(base, "/healthz", timeout, use_proxy)
            ts = time.strftime("%H:%M:%S")
            if r["status"] == 200:
                print(f"[{ts}] 第 {n} 次 ✅ 状态 200　{r['elapsed_s']:.3f}s")
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                print(f"[{ts}] 第 {n} 次 ❌ 状态 {r['status']}　{r['elapsed_s']:.3f}s"
                      f"　{r['body_head'][:80]}")
                if consecutive_fail >= 3:
                    print("   ⚠️ 连续 3 次失败 —— 服务可能已停或被卸载，"
                          "检查 Azure 门户的实例状态")
            if max_pings <= 0 or n < max_pings:
                # 睡成小段，避免 Ctrl-C 在长 sleep 里没反应
                deadline = time.time() + interval_s
                while time.time() < deadline:
                    time.sleep(min(1.0, max(0.0, deadline - time.time())))
    except KeyboardInterrupt:
        print(f"\n已停止（共 {n} 次）")
    return 0


def cmd_budget(interval_min: float) -> int:
    """打印不同间隔下的 CPU 预算，供部署前定间隔用。

    DoD 要求「配额页面当天用量 < 10 CPU 分钟」——
    本地先算一遍，避免上线后才发现 ping 太频繁。
    """
    print("=" * 72)
    print("F1 CPU 预算核算 —— 保温间隔怎么定")
    print("=" * 72)
    print("F1 硬上限：60 CPU 分钟/天　　工单 DoD：当天 < 10 CPU 分钟")
    print()
    print(f"{'间隔':>8}{'次/天':>8}{'冷启动秒':>11}{'热实例秒':>11}"
          f"{'合计分钟':>11}{'占预算':>9}  判定")
    print("-" * 72)
    for iv in (10, 20, 30, 45, 60, 120):
        e = estimate_cpu_minutes_per_day(iv)
        flag = "✅ 达标" if e["under_dod_threshold"] else "❌ 超 DoD"
        if iv < 30:
            flag = "⚠️ 频率过高" + ("（但仍达标）" if e["under_dod_threshold"] else "")
        print(f"{iv:>7}分{e['pings_per_day']:>8.0f}{e['cold_seconds_total']:>11.0f}"
              f"{e['warm_seconds_total']:>11.0f}{e['cpu_minutes_per_day']:>11.2f}"
              f"{e['pct_of_budget']:>8.1f}%  {flag}")
    print("-" * 72)
    print("假设：冷启动 5 CPU 秒（取实测上限 2-5 的上界）、热实例 0.3 秒、")
    print("      1/3 的探针落在实例被卸载后的冷启动上（保守）。")
    print()
    print("结论：30 分钟间隔约 1.5 CPU 分钟/天，占预算 2.5% —— 余量充足。")
    print("      10 分钟间隔（v1/v2 作废的方案）也只占 7.6%，但工单已明确")
    print("      「高频 ping 反而会吃掉 CPU 配额」，且 F1 不支持 Always On、")
    print("      实例卸载时机不可控 —— 30 分钟是稳妥选择。")
    return 0


def cmd_check_cdn(base: str, timeout: int, use_proxy: bool = False) -> int:
    """确认线上 /budget 的 AG Studio 走 CDN 而非 7MB 本地 vendor。

    DoD 要求这一项：`static/vendor/` 已 gitignore，平台构建**不会**装它，
    所以线上应该回退到 jsdelivr CDN。若vendor 真被挂上去，
    7MB × 访问次数会很快吃掉 **165MB 带宽/天** 上限。

    检查两件事：
      1. `/static/vendor/ag-studio.mjs` 是否可达 —— 期望 **404**（不可达）
      2. 页面探测逻辑是否在（budget.html 里应有 CDN 回退分支）
    """
    print("=" * 72)
    print("部署后自检 —— AG Studio 是否走 CDN 回退")
    print("=" * 72)
    print(f"目标：{base}")
    print()

    # 1) 本地 vendor 是否可达（期望 404 = 没装 = 走 CDN）
    r = probe_once(base, "/static/vendor/ag-studio.mjs", timeout, use_proxy)
    vendor_reachable = r["status"] == 200
    print(f"  {'❌' if vendor_reachable else '✅'} /static/vendor/ag-studio.mjs → "
          f"状态 {r['status']}")
    if vendor_reachable:
        print("     ⚠️  本地 vendor 在线上可访问！")
        print("        这意味着每个访客要下载 7MB —— 几次访问就吃满 165MB 带宽/天。")
        print("        排查：平台构建是否执行了 scripts/setup_ag_studio.py，")
        print("        以及 .dockerignore / .gitignore 是否真的挡住了 static/vendor/。")
    else:
        print("     → 不可达 = 平台没装 vendor = 页面会回退到 jsdelivr CDN ✅")

    # 2) 页面是否含 CDN 回退逻辑（要读完整页面 —— CDN 引用在第 203 行附近）
    print()
    r2 = probe_once(base, "/budget", timeout, use_proxy, max_bytes=64 * 1024)
    if r2["status"] == 200:
        has_cdn = "jsdelivr" in r2["_body"]
        print(f"  {'✅' if has_cdn else '❌'} /budget 页面 200，含 CDN 回退标记: {has_cdn}")
        if not has_cdn:
            print("     ⚠️  页面里没看到 jsdelivr 引用 —— 探测逻辑可能被改坏了，")
            print("        AG Studio 加载失败时会没有回退。")
    else:
        print(f"  ❌ /budget 状态 {r2['status']} —— 页面本身就不通，先修这个")

    print()
    ok = (not vendor_reachable) and r2["status"] == 200
    print("✅ 通过：走 CDN 回退，带宽不会被本地 vendor 吃掉" if ok
          else "❌ 未通过：见上面的排查建议")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Azure F1 保温探针（低频 ping + 冷启动测量 + 演示前预热）",
    )
    ap.add_argument("--url", default="", help="线上服务根 URL（不含路径）；--budget 模式可省略")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_MIN,
                    help=f"保温间隔（分钟），默认 {DEFAULT_INTERVAL_MIN}。"
                         "不要低于 30 —— F1 只有 60 CPU 分钟/天（支持小数，仅便于测试）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"单次请求超时（秒），默认 {DEFAULT_TIMEOUT}")
    ap.add_argument("--once", action="store_true", help="只探测一次")
    ap.add_argument("--warm", action="store_true", help="演示前预热：打一遍所有页面")
    ap.add_argument("--measure", action="store_true", help="测冷启动耗时（连打 3 次）")
    ap.add_argument("--budget", action="store_true",
                    help="打印各间隔下的 F1 CPU 预算表（不需要 URL）")
    ap.add_argument("--check-cdn", action="store_true",
                    help="部署后自检：确认 /budget 走 CDN 回退而非本地 7MB vendor")
    ap.add_argument("--max-pings", type=int, default=0,
                    help="常驻模式下最多打多少次（0 = 不限）")
    ap.add_argument("--use-proxy", action="store_true",
                    help="走环境变量里的代理（默认绕过 —— 见 _get 的代理陷阱说明）")
    args = ap.parse_args()

    # --budget 不需要 URL
    if args.budget:
        return cmd_budget(args.interval if args.interval != DEFAULT_INTERVAL_MIN else 30)

    if not args.url:
        ap.error("需要 --url（或用 --budget 做纯本地核算）")

    base = _normalize(args.url)
    use_proxy = args.use_proxy

    if args.once:
        return cmd_once(base, args.timeout, use_proxy)
    if args.warm:
        return cmd_warm(base, args.timeout, use_proxy)
    if args.measure:
        return cmd_measure(base, args.timeout, use_proxy)
    if args.check_cdn:
        return cmd_check_cdn(base, args.timeout, use_proxy)
    return cmd_loop(base, args.interval, args.timeout, args.max_pings, use_proxy)


if __name__ == "__main__":
    raise SystemExit(main())
