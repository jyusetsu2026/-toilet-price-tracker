#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_prices.py
-----------------
products.json に書かれたトイレ商品ごとに、Yahoo!ショッピングの検索結果から
「店舗名」と「価格」の組み合わせを集めます。

商品ごとに
  ・安い順トップ5（店舗名つき）
  ・高い順トップ5（店舗名つき）
を作り、docs/index.html と docs/report.json に書き出します。

GitHub Actions から毎日10時(日本時間)に自動実行される想定です。
詳しい使い方は README.md とマニュアルを参照してください。
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "products.json"
DOCS_DIR = BASE_DIR / "docs"
REPORT_JSON = DOCS_DIR / "report.json"
INDEX_HTML = DOCS_DIR / "index.html"

SEARCH_URL = "https://shopping.yahoo.co.jp/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

JST = timezone(timedelta(hours=9))
PRICE_PATTERN = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,7})\s*円")
STORE_LINK_PATTERN = re.compile(r"/store/")


def fetch_store_prices(query: str, max_items: int = 15):
    """
    指定したキーワードで検索し、「店舗名」と「価格」の組み合わせのリストを返す。

    Yahoo!ショッピングの検索結果ページには、同じ商品を複数の店舗が
    出品していることが多いため、価格の近くにある店舗リンクを手がかりに
    店舗名を拾う。サイトのHTML構造が変わると取得できなくなることがある。
    """
    params = {"p": query}
    try:
        res = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
        res.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [警告] 検索に失敗しました: {query} ({exc})")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    found = []
    seen_stores = {}

    # ページ内の「◯◯円」というテキストを1つずつたどり、
    # その近くの祖先要素から店舗リンク（/store/を含むリンク）を探す。
    for text_node in soup.find_all(string=PRICE_PATTERN):
        match = PRICE_PATTERN.search(text_node)
        if not match:
            continue
        raw = match.group(1).replace(",", "")
        try:
            price = int(raw)
        except ValueError:
            continue
        if not (5000 <= price <= 500000):
            continue

        store_name = None
        container = text_node.parent
        for _ in range(6):  # 直近の祖先要素をさかのぼって店舗リンクを探す
            if container is None:
                break
            store_link = container.find("a", href=STORE_LINK_PATTERN)
            if store_link and store_link.get_text(strip=True):
                store_name = store_link.get_text(strip=True)
                break
            container = container.parent

        if not store_name:
            continue

        # 同じ店舗が複数ヒットした場合は、一番安い価格だけを残す
        if store_name not in seen_stores or price < seen_stores[store_name]:
            seen_stores[store_name] = price

        if len(seen_stores) >= max_items:
            break

    for store_name, price in seen_stores.items():
        found.append({"store": store_name, "price": price})

    return found


def build_report(products):
    results = []
    for product in products:
        print(f"検索中: {product['name']} ...")
        store_prices = fetch_store_prices(product["query"])
        time.sleep(2)  # サイトへの負荷を減らすための待機

        if not store_prices:
            print("  → 店舗・価格が見つかりませんでした")
            results.append(
                {
                    "id": product["id"],
                    "name": product["name"],
                    "query": product["query"],
                    "cheapest_top5": [],
                    "expensive_top5": [],
                    "store_count": 0,
                }
            )
            continue

        sorted_by_price = sorted(store_prices, key=lambda r: r["price"])
        cheapest_top5 = sorted_by_price[:5]
        expensive_top5 = sorted(store_prices, key=lambda r: r["price"], reverse=True)[:5]

        results.append(
            {
                "id": product["id"],
                "name": product["name"],
                "query": product["query"],
                "cheapest_top5": cheapest_top5,
                "expensive_top5": expensive_top5,
                "store_count": len(store_prices),
            }
        )
        print(f"  → {len(store_prices)}店舗を確認"
              f"（最安 {cheapest_top5[0]['price']:,}円 / 最高 {expensive_top5[0]['price']:,}円）")

    return results


def render_html(results, generated_at: str) -> str:
    def rows(items):
        if not items:
            return "<tr><td colspan='3' class='empty'>店舗情報が見つかりませんでした</td></tr>"
        out = []
        for i, r in enumerate(items, start=1):
            out.append(
                f"<tr><td>{i}</td><td>{r['store']}</td>"
                f"<td class='price'>{r['price']:,}円</td></tr>"
            )
        return "\n".join(out)

    product_sections = []
    for r in results:
        product_sections.append(f"""
    <section class="product">
      <h2>{r['name']}</h2>
      <div class="store-count">確認できた店舗数: {r['store_count']}店舗</div>
      <div class="cards">
        <div class="card low">
          <h3>安い順 TOP5</h3>
          <table>
            <tr><th>#</th><th>店舗名</th><th>価格</th></tr>
            {rows(r['cheapest_top5'])}
          </table>
        </div>
        <div class="card high">
          <h3>高い順 TOP5</h3>
          <table>
            <tr><th>#</th><th>店舗名</th><th>価格</th></tr>
            {rows(r['expensive_top5'])}
          </table>
        </div>
      </div>
    </section>""")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>トイレ商品 価格ランキング（店舗別）</title>
<style>
  body {{ font-family: "Hiragino Sans", "Yu Gothic", sans-serif; background:#f5f6f8; color:#222; margin:0; padding:24px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .updated {{ color:#666; font-size:13px; margin-bottom:28px; }}
  .product {{ background:#fff; border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.1); padding:16px 20px; margin-bottom:24px; }}
  .product h2 {{ font-size:16px; margin:0 0 4px; }}
  .store-count {{ color:#888; font-size:12px; margin-bottom:12px; }}
  .cards {{ display:flex; gap:20px; flex-wrap:wrap; }}
  .card {{ flex:1; min-width:280px; }}
  .card h3 {{ font-size:14px; margin:0 0 8px; }}
  .card.low h3 {{ color:#2471a3; }}
  .card.high h3 {{ color:#c0392b; }}
  table {{ width:100%; border-collapse: collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:6px; border-bottom:1px solid #eee; }}
  th {{ color:#888; font-weight:normal; }}
  .price {{ font-weight:bold; text-align:right; }}
  .empty {{ color:#aaa; text-align:center; padding:14px 0; }}
</style>
</head>
<body>
  <h1>🚽 トイレ商品 価格ランキング（商品別・店舗名つき）</h1>
  <div class="updated">最終更新: {generated_at}（毎日10:00に自動更新）</div>
  {''.join(product_sections)}
</body>
</html>
"""
    return html


def main():
    DOCS_DIR.mkdir(exist_ok=True)
    products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))

    results = build_report(products)
    generated_at = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M (JST)")

    REPORT_JSON.write_text(
        json.dumps(
            {"generated_at": generated_at, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    INDEX_HTML.write_text(render_html(results, generated_at), encoding="utf-8")
    print("完了しました。docs/index.html と docs/report.json を更新しました。")


if __name__ == "__main__":
    main()
