#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_prices.py
-----------------
products.json に書かれたトイレ商品ごとに、
  ・楽天市場 商品検索API
  ・Yahoo!ショッピング 商品検索API
の2つの公式APIを使って「店舗名」と「価格」の組み合わせを集めます。

（以前はWebページを機械的に読み取る方式でしたが、検索サイト側にブロックされて
価格が全く取得できなくなったため、各モールが公式に提供している無料APIを
使う方式に変更しました。）

商品ごとに
  ・安い順トップ5（店舗名つき）
  ・高い順トップ5（店舗名つき）
を作り、docs/index.html と docs/report.json に書き出します。

■ 必要な準備
  GitHub の Settings → Secrets and variables → Actions で、以下の2つを
  登録しておく必要があります（詳しくはマニュアル参照）。
    - RAKUTEN_APP_ID   … 楽天ウェブサービスで発行したApplication ID
    - YAHOO_CLIENT_ID  … Yahoo!デベロッパーネットワークで発行したClient ID

  どちらか片方しか登録していない場合、そのモールの分だけスキップして
  もう片方のモールの結果だけで集計します（エラーにはなりません）。

GitHub Actions から毎日10時(日本時間)に自動実行される想定です。
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "products.json"
DOCS_DIR = BASE_DIR / "docs"
REPORT_JSON = DOCS_DIR / "report.json"
INDEX_HTML = DOCS_DIR / "index.html"

JST = timezone(timedelta(hours=9))

RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID", "").strip()
YAHOO_CLIENT_ID = os.environ.get("YAHOO_CLIENT_ID", "").strip()

RAKUTEN_URL = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"
YAHOO_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"

HEADERS = {"User-Agent": "toilet-price-tracker/1.0"}


def fetch_rakuten(query: str, hits: int = 30):
    """楽天市場 商品検索APIから (店舗名, 価格) のリストを取得する。"""
    if not RAKUTEN_APP_ID:
        return []

    params = {
        "applicationId": RAKUTEN_APP_ID,
        "keyword": query,
        "hits": hits,
        "sort": "+itemPrice",  # 安い順
        "format": "json",
    }
    try:
        res = requests.get(RAKUTEN_URL, params=params, headers=HEADERS, timeout=15)
        res.raise_for_status()
        data = res.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [警告] 楽天APIの取得に失敗しました: {query} ({exc})")
        return []

    results = []
    for entry in data.get("Items", []):
        item = entry.get("Item", {})
        shop_name = item.get("shopName")
        price = item.get("itemPrice")
        if shop_name and isinstance(price, int):
            results.append({"store": f"{shop_name}（楽天）", "price": price})
    return results


def fetch_yahoo(query: str, results_count: int = 30):
    """Yahoo!ショッピング 商品検索APIから (店舗名, 価格) のリストを取得する。"""
    if not YAHOO_CLIENT_ID:
        return []

    params = {
        "appid": YAHOO_CLIENT_ID,
        "query": query,
        "results": results_count,
        "sort": "+price",  # 安い順
    }
    try:
        res = requests.get(YAHOO_URL, params=params, headers=HEADERS, timeout=15)
        res.raise_for_status()
        data = res.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [警告] Yahoo!APIの取得に失敗しました: {query} ({exc})")
        return []

    results = []
    for hit in data.get("hits", []):
        seller = hit.get("seller", {})
        shop_name = seller.get("name")
        price = hit.get("price")
        if shop_name and isinstance(price, int):
            results.append({"store": f"{shop_name}（Yahoo!）", "price": price})
    return results


def fetch_store_prices(query: str):
    """楽天とYahoo!の両方から集めた (店舗名, 価格) のリストを返す（店舗ごとに最安値のみ）。"""
    combined = fetch_rakuten(query) + fetch_yahoo(query)

    cheapest_per_store = {}
    for entry in combined:
        store = entry["store"]
        price = entry["price"]
        if store not in cheapest_per_store or price < cheapest_per_store[store]:
            cheapest_per_store[store] = price

    return [{"store": s, "price": p} for s, p in cheapest_per_store.items()]


def build_report(products):
    if not RAKUTEN_APP_ID and not YAHOO_CLIENT_ID:
        print("[警告] RAKUTEN_APP_ID と YAHOO_CLIENT_ID のどちらも設定されていません。"
              "GitHubのSecretsを確認してください。")

    results = []
    for product in products:
        print(f"検索中: {product['name']} ...")
        store_prices = fetch_store_prices(product["query"])
        time.sleep(1)  # 各APIの利用制限（1クエリー/秒）を守るための待機

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

        cheapest_top5 = sorted(store_prices, key=lambda r: r["price"])[:5]
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
      <div class="store-count">確認できた店舗数: {r['store_count']}店舗（楽天市場＋Yahoo!ショッピング）</div>
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
  <div class="updated">最終更新: {generated_at}（毎日10:00に自動更新／楽天市場＋Yahoo!ショッピング公式APIより取得）</div>
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
