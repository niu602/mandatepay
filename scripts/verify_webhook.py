"""Webhook 链路自检：确认注册的 URL / 修正 URL / 让 PayPal 真实投递一个事件。

用途：
  1) 换隧道或换域名后，确认 PayPal 侧注册的 URL 是否已经指向新地址；
  2) 需要时用 PATCH 把 URL 改到指定值（只改 URL，不重建，webhook id 保持不变）；
  3) 用官方 simulate-event 让 PayPal 真正发起一次带签名的投递，
     用来验证「公网 → 隧道 → 验签 → 入库」整条链路是否通。

用法：
  python scripts/verify_webhook.py --info
  python scripts/verify_webhook.py --set-url https://xxx.ngrok-free.dev/api/webhook
  python scripts/verify_webhook.py --simulate PAYMENT.CAPTURE.COMPLETED
  python scripts/verify_webhook.py --info --simulate PAYMENT.CAPTURE.COMPLETED

注意：
  simulate-event 发出的 resource 是 PayPal 造的样例数据，里面没有我们自己的
  order_id，所以业务侧找不到对应 mandate 属正常；本条只验证签名与投递链路。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app import config  # noqa: E402
from app import paypal_client  # noqa: E402


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {paypal_client.get_access_token()}",
    }


def get_webhook(webhook_id: str) -> dict:
    resp = requests.get(
        f"{config.PAYPAL_API_BASE}/v1/notifications/webhooks/{webhook_id}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def patch_url(webhook_id: str, new_url: str) -> int:
    """只替换 URL，保留 id 与事件订阅。"""
    resp = requests.patch(
        f"{config.PAYPAL_API_BASE}/v1/notifications/webhooks/{webhook_id}",
        headers=_headers(),
        json=[{"op": "replace", "path": "/url", "value": new_url}],
        timeout=20,
    )
    return resp.status_code


def simulate(webhook_id: str, event_type: str, resource_version: str = "2.0") -> dict:
    """让 PayPal 向已注册的 URL 真实投递一个事件。

    注意路径是 /v1/notifications/simulate-event（不带 webhook id），
    webhook_id 放在 body 里；写成 /webhooks/{id}/simulate-event 会 404。
    """
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v1/notifications/simulate-event",
        headers=_headers(),
        json={
            "webhook_id": webhook_id,
            "event_type": event_type,
            "resource_version": resource_version,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        return {"error": resp.status_code, "detail": resp.text[:500]}
    return resp.json()


def cmd_info(webhook_id: str) -> dict:
    d = get_webhook(webhook_id)
    print(f"id     = {d.get('id')}")
    print(f"url    = {d.get('url')}")
    print("events = " + ", ".join(e.get("name", "?") for e in d.get("event_types", [])))
    print(f"links  = {d.get('links') and 'present'}")
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--info", action="store_true", help="打印当前 webhook 详情")
    ap.add_argument("--set-url", default="", help="把 webhook URL 改成该值（PATCH，保留 id）")
    ap.add_argument("--simulate", default="", help="让 PayPal 投递一个指定类型的事件")
    ap.add_argument("--wait", type=int, default=8, help="模拟事件后等待投递的秒数")
    args = ap.parse_args()

    wid = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()
    if not wid:
        print("❌ .env 里没有 PAYPAL_WEBHOOK_ID", file=sys.stderr)
        return 1
    print(f"webhook_id = {wid}\n")

    if args.set_url:
        code = patch_url(wid, args.set_url)
        print(f"PATCH /url -> HTTP {code}")
        if code >= 400:
            print("❌ 更新失败", file=sys.stderr)
            return 1
        print(f"✅ URL 已指向 {args.set_url}\n")

    if args.info or not args.simulate:
        cmd_info(wid)
        print()

    if args.simulate:
        print(f"→ 让 PayPal 投递 {args.simulate} ...")
        r = simulate(wid, args.simulate)
        if "error" in r:
            print(f"❌ simulate-event 失败: {r}", file=sys.stderr)
            return 1
        print(f"  event id   = {r.get('id')}")
        print(f"  event_type = {r.get('event_type')}")
        print(f"  等待 {args.wait}s 让它经隧道投递到本地服务 ...")
        time.sleep(args.wait)

        # 从本地库里确认是否收到、是否验签通过
        try:
            from agent import store

            rows = store.list_webhook_events(limit=10)
            hit = next((x for x in rows if x.get("event_id") == r.get("id")), None)
            if hit:
                print(f"\n✅ 本地已收到该事件：verified={hit.get('verified')} "
                      f"type={hit.get('event_type')} resource={hit.get('resource_id')}")
            else:
                print("\n⚠️ 本地库中未找到该 event_id —— 可能投递较慢，"
                      "请查服务日志或稍后重跑 --info")
                print("   已收到的最新 3 条：")
                for x in rows[:3]:
                    print(f"     {x.get('event_id')} {x.get('event_type')} verified={x.get('verified')}")
        except Exception as e:  # noqa: BLE001
            print(f"（查询本地库失败，忽略：{e}）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
