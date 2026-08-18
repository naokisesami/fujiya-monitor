"""
フジヤエービック 在庫監視スクリプト
====================================
products.csv に登録した商品ページを巡回し、「在庫あり → 売り切れ/削除」に
変化した商品があればメールで通知する。

- 在庫判定は商品ページに埋め込まれている構造化データ (etm:goods_detail の
  stock_status) を最優先で使用し、取得できない場合は "SOLD OUT" 等の
  表示文言で補助判定する。
- 前回のチェック結果は state.json に保存し、差分（新規に売り切れたものだけ）
  を通知する。
"""

import csv
import json
import os
import re
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from html import unescape

import requests

STATE_FILE = "state.json"
PRODUCTS_FILE = "products.csv"
JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# 商品ページに埋め込まれている構造化データを拾う正規表現
GOODS_DETAIL_RE = re.compile(r'name="etm:goods_detail"\s+content="([^"]+)"')


def load_products():
    products = []
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("fujiya_url") or "").strip()
            if not url:
                continue
            products.append(
                {
                    "fujiya_url": url,
                    "ebay_item": (row.get("ebay_item") or "").strip(),
                    "memo": (row.get("memo") or "").strip(),
                }
            )
    return products


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_stock(url):
    """
    フジヤエービックの商品ページの在庫状況を調べる。
    戻り値: "in_stock" / "sold_out" / "not_found" / "unknown"
    """
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"  [warn] 取得失敗: {e}")
        return "unknown"

    if res.status_code == 404:
        # 中古の個体ページは売り切れ後に削除されることがある
        return "not_found"
    if res.status_code != 200:
        print(f"  [warn] HTTP {res.status_code}")
        return "unknown"

    html = res.text

    # 1) 埋め込みJSON(stock_status)を最優先で見る
    m = GOODS_DETAIL_RE.search(html)
    if m:
        try:
            data = json.loads(unescape(m.group(1)))
            stock_status = str(data.get("stock_status", ""))
            if stock_status == "1":
                return "in_stock"
            elif stock_status != "":
                return "sold_out"
        except (json.JSONDecodeError, TypeError):
            pass

    # 2) 補助チェック: SOLD OUT表記・カートボタンの有無
    if "SOLD OUT" in html or "売り切れ" in html:
        return "sold_out"
    if "カートに入れる" in html:
        return "in_stock"

    return "unknown"


def send_email(subject, body):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    notify_to = os.environ.get("NOTIFY_TO") or gmail_user

    if not gmail_user or not gmail_pass:
        print("  [warn] メール設定(GMAIL_USER/GMAIL_APP_PASSWORD)がないため通知をスキップしました")
        print(f"  --- {subject} ---\n{body}")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = notify_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, [notify_to], msg.as_string())
    print(f"  [ok] メール通知を送信しました -> {notify_to}")


def main():
    products = load_products()
    state = load_state()
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    if not products:
        print("products.csv に商品が登録されていません。処理を終了します。")
        return

    newly_sold_out = []

    for p in products:
        url = p["fujiya_url"]
        print(f"チェック中: {url}")
        status = check_stock(url)
        prev = state.get(url, {}).get("status")
        print(f"  結果: {status} (前回: {prev})")

        if status in ("sold_out", "not_found") and prev == "in_stock":
            newly_sold_out.append(p)

        if status != "unknown":
            state[url] = {
                "status": status,
                "checked_at": now,
                "memo": p["memo"],
                "ebay_item": p["ebay_item"],
            }

    save_state(state)

    if newly_sold_out:
        lines = [
            "フジヤエービックで以下の商品が売り切れ（またはページ削除）になりました。",
            "eBay側の出品状況をご確認ください。",
            "",
        ]
        for p in newly_sold_out:
            lines.append(f"・{p['memo'] or '(メモなし)'}")
            lines.append(f"  フジヤURL: {p['fujiya_url']}")
            if p["ebay_item"]:
                lines.append(f"  eBay商品: {p['ebay_item']}")
            lines.append("")
        body = "\n".join(lines)
        send_email(f"[在庫アラート] フジヤエービックで{len(newly_sold_out)}件売り切れ", body)
    else:
        print("新たに売り切れになった商品はありませんでした。")


if __name__ == "__main__":
    main()
