"""通过 PayPal REST API 管理 Webhook 订阅（无需手动点 Dashboard）。

用法：
    python scripts\\setup_webhook.py --list
    python scripts\\setup_webhook.py --create --url https://xxxx/api/webhook
    python scripts\\setup_webhook.py --create --url https://xxxx/api/webhook --force   # 已存在则先删再建
    python scripts\\setup_webhook.py --delete --webhook-id <id>

创建成功后把返回的 id 填进 .env 的 PAYPAL_WEBHOOK_ID，重启服务即可验签。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import config, paypal_client  # noqa: E402

# MandatePay 订阅的 4 个支付事件
EVENTS = [
    "PAYMENT.CAPTURE.COMPLETED",
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.PENDING",
]


def cmd_list() -> None:
    hooks = paypal_client.list_webhooks()
    if not hooks:
        print("当前没有已注册的 Webhook。")
        return
    for h in hooks:
        types = ", ".join(e["name"] for e in h.get("event_types", []))
        print(f"id={h['id']}\n  url={h.get('url')}\n  events={types}\n")


def cmd_create(url: str, force: bool) -> None:
    import requests

    existing = paypal_client.list_webhooks()
    if force:
        for h in existing:
            print(f"删除旧 webhook: {h['id']}")
            cmd_delete(h["id"])
        existing = []

    for h in existing:
        if h.get("url", "").rstrip("/") == url.rstrip("/"):
            print("已存在指向同一 URL 的 Webhook：")
            print(json.dumps(h, indent=2, ensure_ascii=False))
            return

    token = paypal_client.get_access_token()
    resp = requests.post(
        f"{config.PAYPAL_API_BASE}/v1/notifications/webhooks",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"url": url, "event_types": [{"name": n} for n in EVENTS]},
        timeout=20,
    )
    if resp.status_code >= 400:
        print(f"创建失败 HTTP {resp.status_code}: {resp.text[:600]}")
        raise SystemExit(1)

    hook = resp.json()
    print("✅ Webhook 创建成功")
    print(f"   id  = {hook['id']}")
    print(f"   url = {hook.get('url')}")
    print("   events = " + ", ".join(e["name"] for e in hook.get("event_types", [])))
    print("\n请把下面这行写进 .env 并重启服务：")
    print(f"PAYPAL_WEBHOOK_ID={hook['id']}")

    # 顺手写进 .env，省一步手工
    env_path = ROOT / ".env"
    txt = env_path.read_text(encoding="utf-8")
    import re

    if re.search(r"(?m)^PAYPAL_WEBHOOK_ID=", txt):
        txt = re.sub(r"(?m)^PAYPAL_WEBHOOK_ID=.*$", f"PAYPAL_WEBHOOK_ID={hook['id']}", txt)
    else:
        if not txt.endswith("\n"):
            txt += "\n"
        txt += f"PAYPAL_WEBHOOK_ID={hook['id']}\n"
    env_path.write_text(txt, encoding="utf-8")
    print(f"   已自动写入 {env_path}")


def cmd_delete(webhook_id: str) -> None:
    import requests

    token = paypal_client.get_access_token()
    resp = requests.delete(
        f"{config.PAYPAL_API_BASE}/v1/notifications/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    print(f"删除 {webhook_id}: HTTP {resp.status_code}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--url", default=os.getenv("PUBLIC_WEBHOOK_URL", ""), help="公网回调地址")
    ap.add_argument("--webhook-id", default="")
    ap.add_argument("--force", action="store_true", help="创建前先删除已存在的 webhook")
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return 0
    if args.create:
        if not args.url:
            print("需要 --url https://<公网域名>/api/webhook")
            return 2
        cmd_create(args.url, args.force)
        return 0
    if args.delete:
        if not args.webhook_id:
            print("需要 --webhook-id")
            return 2
        cmd_delete(args.webhook_id)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
