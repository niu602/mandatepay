#!/usr/bin/env python
r"""LLM 端点预检：录视频前跑一次，确认 Z.ai 端点现在是"快"还是"限流"。

为什么需要这个
--------------
Z.ai 免费档会被限流（429 / 1305 "service may be temporarily overloaded"）。
`agent/llm_intent._invoke_with_retry` 遇 429 会**指数退避重试**，
单次调用最坏能拖 200 秒以上（实测 207s）。而演示主路径
（`/api/agent/parse`）一次要走 parse + insight + explain **三次**调用 ——
端点一限流，页面上的"AI 洞察 / 决策解释"就会退化成模板文案，甚至整页卡住。

所以**录制前先跑这个脚本**，别等到录制现场才发现端点在限流。

用法
----
```bash
python scripts/check_llm_health.py            # 只探活
python scripts/check_llm_health.py --url https://<线上域名>   # 再探一次线上页面
```

判定标准（单次调用耗时）
----------------------
- ≤ 3s   → ✅ 绿，放心录
- ≤ 15s  → ⚠️ 黄，能录但可能偶发变慢，建议先热身一遍
- \> 15s / 失败 → ❌ 红，**先别录**，等限流过去（通常几分钟到十几分钟）

注意：脚本**不打印 API Key**，只打印耗时与状态码。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()   # 读本地 .env（只影响本脚本的探活，不改服务配置）

sys.stdout.reconfigure(encoding="utf-8")

GREEN, YELLOW, RED = "✅", "⚠️", "❌"


def probe(timeout: float = 30.0) -> tuple[bool, str, float]:
    """打一次最小请求。返回 (是否可用, 说明, 耗时秒)。"""
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    model = os.getenv("LLM_MODEL", "")
    key = os.getenv("LLM_API_KEY", "")
    if not (base and model and key):
        return False, "本地环境变量不完整（LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）", 0.0

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "reply with the single word: ok"}],
        "max_tokens": 32,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        elapsed = time.time() - t0
        data = json.loads(raw)
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if not content.strip():
            # GLM 会把 token 花在 reasoning_content 上，content 可能为空 —— 不算失败
            return True, f"HTTP 200（content 为空，token 花在 reasoning 上）", elapsed
        return True, "HTTP 200", elapsed
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        detail = e.read()[:120].decode("utf-8", "replace")
        return False, f"HTTP {e.code}｜{detail}", elapsed
    except Exception as e:                                   # noqa: BLE001
        elapsed = time.time() - t0
        return False, f"{type(e).__name__}: {str(e)[:110]}", elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 端点预检（录视频前必跑）")
    ap.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数")
    ap.add_argument("--url", default="", help="顺带探一下线上首页是否响应")
    args = ap.parse_args()

    print("=" * 66)
    print("LLM 端点预检")
    print("=" * 66)
    print(f"端点: {os.getenv('LLM_BASE_URL', '(未配置)')}｜模型: {os.getenv('LLM_MODEL', '(未配置)')}")
    print(f"Key : 已配置（长度 {len(os.getenv('LLM_API_KEY', ''))}，不打印内容）\n")

    ok, detail, elapsed = probe(timeout=args.timeout)
    if ok and elapsed <= 3:
        mark, verdict = GREEN, "端点很快 —— 现在就可以录"
    elif ok and elapsed <= 15:
        mark, verdict = YELLOW, "端点偏慢 —— 能录，但建议先把三处 LLM 调用各热身一遍"
    else:
        mark, verdict = RED, "端点限流/不可用 —— 先别录，等限流过去"

    print(f"{mark} 单次调用耗时 {elapsed:.1f}s｜{detail}")
    print(f"   → {verdict}")

    if args.url:
        t0 = time.time()
        try:
            req = urllib.request.Request(args.url)
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
            print(f"\n{GREEN} 线上首页 {r.status}，耗时 {time.time() - t0:.1f}s")
        except Exception as e:                               # noqa: BLE001
            print(f"\n{RED} 线上首页探测失败：{type(e).__name__}: {str(e)[:100]}")

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
