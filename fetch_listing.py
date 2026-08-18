"""
フジヤエービック 商品データ取得スクリプト
==========================================
指定した商品ページURLから、写真・商品名・ブランド・ランク・JANコード・
価格などを取得し、
  - output/<商品コード>/ に写真を保存
  - listings.csv に1行追加（eBayタイトル・価格は空欄のまま）
する。

使い方:
  python fetch_listing.py "https://www.fujiya-avic.co.jp/shop/g/g240001179986/"
  もしくは環境変数 FUJIYA_URL に指定して実行（GitHub Actionsから利用する場合）
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

IMG_RE = re.compile(r'/img/goods/[^"\'\s]+?\.(?:jpg|jpeg|png)', re.I)
JAN_RE = re.compile(r'JAN[コードCode]{0,4}\s*[:：]?\s*\n?\s*([0-9]{8,13})')

# コンディション比較テーブルから拾いたい項目
CONDITION_LABELS = ("ランク", "元箱", "中古保証期間", "特記事項", "欠品情報")


def extract_goods_code(url):
    m = re.search(r'/g/g(\d+)/?', url)
    return m.group(1) if m else None


def fetch_listing(url):
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    # 1) 埋め込みの構造化データ（商品名・ブランド・価格・カテゴリ）
    data = {}
    meta_tag = soup.find("meta", attrs={"name": "etm:goods_detail"})
    print(f"  [debug] goods_detail metaタグ検出: {bool(meta_tag)}")
    if meta_tag and meta_tag.get("content"):
        try:
            data = json.loads(meta_tag["content"])
        except (json.JSONDecodeError, TypeError) as e:
            print(f"  [warn] goods_detail JSONの解析に失敗: {e}")

    goods_code = data.get("item_code") or extract_goods_code(url)

    # 2) コンディション比較テーブル（ランク・元箱・特記事項・欠品情報など）
    condition = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                if label in CONDITION_LABELS:
                    condition[label] = value
    print(f"  [debug] コンディション表から取得した項目: {list(condition.keys())}")

    # 3) JANコード（ページ全文テキストから）
    page_text = soup.get_text("\n")
    jan = None
    jm = JAN_RE.search(page_text)
    if jm:
        jan = jm.group(1)

    # 4) 画像URL収集（メイン画像・詳細画像。重複除去して順序維持）
    img_paths = list(dict.fromkeys(IMG_RE.findall(html)))
    image_urls = [f"https://www.fujiya-avic.co.jp{p}" for p in img_paths]

    return {
        "goods_code": goods_code,
        "name": data.get("name"),
        "brand": data.get("brand_name"),
        "price": data.get("price"),
        "rank": condition.get("ランク"),
        "jan": jan,
        "original_box": condition.get("元箱"),
        "note": condition.get("特記事項"),
        "missing_items": condition.get("欠品情報"),
        "category_name": data.get("category_name"),
        "url": url,
        "image_urls": image_urls,
    }


def download_images(goods_code, image_urls, out_dir="output"):
    folder = os.path.join(out_dir, goods_code or "unknown")
    os.makedirs(folder, exist_ok=True)
    saved = []
    for i, img_url in enumerate(image_urls, start=1):
        try:
            r = requests.get(img_url, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  [warn] 画像取得失敗: {img_url} ({e})")
            continue
        ext = os.path.splitext(urlparse(img_url).path)[1] or ".jpg"
        fname = f"{i:02d}{ext}"
        fpath = os.path.join(folder, fname)
        with open(fpath, "wb") as f:
            f.write(r.content)
        saved.append(fpath)
        print(f"  [ok] 画像保存: {fpath}")
    return folder, saved


def append_listing_csv(item, csv_path="listings.csv"):
    file_exists = os.path.exists(csv_path)
    fieldnames = [
        "取得日時", "商品コード", "商品名", "ブランド", "ランク", "JANコード",
        "仕入れ価格(税込)", "カテゴリ", "元箱", "特記事項", "欠品情報", "フジヤURL",
        "eBayタイトル", "eBay価格", "画像フォルダ",
    ]
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "取得日時": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
            "商品コード": item["goods_code"],
            "商品名": item["name"],
            "ブランド": item["brand"],
            "ランク": item["rank"],
            "JANコード": item["jan"],
            "仕入れ価格(税込)": item["price"],
            "カテゴリ": item["category_name"],
            "元箱": item["original_box"],
            "特記事項": item["note"],
            "欠品情報": item["missing_items"],
            "フジヤURL": item["url"],
            "eBayタイトル": "",
            "eBay価格": "",
            "画像フォルダ": f"output/{item['goods_code']}",
        })


def main():
    url = os.environ.get("FUJIYA_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url:
        print("エラー: 商品URLが指定されていません。")
        sys.exit(1)

    print(f"取得中: {url}")
    item = fetch_listing(url)

    if not item["goods_code"]:
        print("エラー: 商品コードを取得できませんでした。URLを確認してください。")
        sys.exit(1)

    print(f"商品名: {item['name']}")
    print(f"ブランド: {item['brand']} / ランク: {item['rank']} / 価格: {item['price']}")
    print(f"画像 {len(item['image_urls'])} 枚を検出")

    folder, saved = download_images(item["goods_code"], item["image_urls"])
    append_listing_csv(item)

    print(f"完了: {len(saved)}枚の画像を {folder} に保存し、listings.csv に1行追加しました。")


if __name__ == "__main__":
    main()
