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

注意:
  フジヤエービックのページ構造が変わると、JANコード・ランク・特記事項の
  抽出がずれる可能性があります（商品名・ブランド・価格・写真は構造化データ
  から取得しているため比較的安定しています）。実際に何件か試して、
  抽出結果がおかしい場合は教えてください。
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from html import unescape
from urllib.parse import urlparse

import requests

JST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

GOODS_DETAIL_RE = re.compile(r'name="etm:goods_detail"\s+content="([^"]+)"')
JAN_RE = re.compile(r'JAN[コードCode]{0,4}[^0-9]{0,40}([0-9]{8,13})', re.S)
RANK_RE = re.compile(r'中古[:：]\s*(未使用/未開封品|現状品|コレクション|AB\+|AB-|AB|A|B)')
NOTE_RE = re.compile(r'特記事項[^<]{0,20}<[^>]*>\s*([^<\n]{1,200})', re.S)
IMG_RE = re.compile(r'/img/goods/[^"\'\s]+?\.(?:jpg|jpeg|png)', re.I)


def extract_goods_code(url):
    m = re.search(r'/g/g(\d+)/?', url)
    return m.group(1) if m else None


def fetch_listing(url):
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    html = res.text

    data = {}
    m = GOODS_DETAIL_RE.search(html)
    if m:
        try:
            data = json.loads(unescape(m.group(1)))
        except (json.JSONDecodeError, TypeError):
            data = {}

    goods_code = data.get("item_code") or extract_goods_code(url)

    jan = None
    jm = JAN_RE.search(html)
    if jm:
        jan = jm.group(1)

    rank = None
    rm = RANK_RE.search(html)
    if rm:
        rank = rm.group(1)

    note = None
    nm = NOTE_RE.search(html)
    if nm:
        note = nm.group(1).strip()

    # 画像URL収集（メイン画像・詳細画像。重複除去して順序維持）
    img_paths = list(dict.fromkeys(IMG_RE.findall(html)))
    image_urls = [f"https://www.fujiya-avic.co.jp{p}" for p in img_paths]

    return {
        "goods_code": goods_code,
        "name": data.get("name"),
        "brand": data.get("brand_name"),
        "price": data.get("price"),
        "rank": rank,
        "jan": jan,
        "note": note,
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
        "仕入れ価格(税込)", "カテゴリ", "特記事項", "フジヤURL",
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
            "特記事項": item["note"],
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
