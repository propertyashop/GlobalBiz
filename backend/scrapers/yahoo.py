"""
Yahoo!ショッピング Item Search API v3 クライアント

Yahoo 商品コードまたはキーワードで商品情報を取得する。

設定 (.env):
    YAHOO_CLIENT_ID: Yahoo Developer Network の Client ID (appid)

使用方法:
    from backend.scrapers.yahoo import fetch_product_by_item_code, search_products
    result = fetch_product_by_item_code("shopcode_item-001")
    results = search_products("ワイヤレスイヤホン", limit=5)

API リファレンス:
    https://developer.yahoo.co.jp/webapi/shopping/shopping/v3/itemsearch.html
"""

import os
import time
import random
import logging
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────────────
_API_ENDPOINT = "https://shopping.yahoo.co.jp/webservice/v3/itemSearch"
_TIMEOUT = 15  # 秒


# ── カテゴリマッピング（category.id 先頭文字列で判断）──────────────
def _map_category_id(category_id: str) -> str:
    """Yahoo カテゴリ ID → 内部カテゴリ値に変換"""
    if category_id.startswith("2502"):
        return "electronics"
    if category_id.startswith("2345"):
        return "clothing"
    if category_id.startswith("2503"):
        return "cosmetics"
    return "other"


# ── レート制限 ────────────────────────────────────────────────────────
def _rate_limit() -> None:
    """0.5〜1.5 秒のランダム待機"""
    wait = random.uniform(0.5, 1.5)
    logger.debug("Rate limit: sleeping %.2f sec", wait)
    time.sleep(wait)


# ── レスポンス変換 ────────────────────────────────────────────────────
def _parse_hit(hit: Dict[str, Any], item_code: str = "") -> Dict[str, Any]:
    """Yahoo API の hits[] エントリを内部 dict 形式に変換"""
    # 商品コード
    parsed_item_code: str = item_code or hit.get("code", "")

    # 商品名
    name: str = hit.get("name", "")

    # 英語名（Yahoo API には提供なし）
    name_en: str = ""

    # 価格
    price_raw = hit.get("price")
    price: Optional[float] = float(price_raw) if price_raw is not None else None

    # 画像
    images: List[str] = []
    image_obj = hit.get("image", {})
    if isinstance(image_obj, dict):
        medium_url = image_obj.get("medium", "")
        if medium_url:
            images.append(medium_url)
    # 追加画像があれば収集
    for extra in hit.get("images", []):
        if isinstance(extra, dict):
            url = extra.get("medium", "") or extra.get("url", "")
        else:
            url = str(extra)
        if url and url not in images:
            images.append(url)
    images = images[:9]

    # カテゴリ
    category_obj = hit.get("category", {})
    category_id: str = str(category_obj.get("id", "")) if isinstance(category_obj, dict) else ""
    category: str = _map_category_id(category_id)

    # 在庫
    in_stock = hit.get("inStock", True)
    availability: str = "在庫あり" if in_stock else "在庫なし"

    # JAN コード
    jan_code: Optional[str] = hit.get("janCode") or None

    # 商品 URL
    url: str = hit.get("url", "")

    return {
        "item_code": parsed_item_code,
        "name": name,
        "name_en": name_en,
        "price": price,
        "images": images,
        "category": category,
        "availability": availability,
        "jan_code": jan_code,
        "url": url,
        "error": None,
    }


def _error_result(item_code: str, error: str) -> Dict[str, Any]:
    return {
        "item_code": item_code,
        "name": "",
        "name_en": "",
        "price": None,
        "images": [],
        "category": "other",
        "availability": "不明",
        "jan_code": None,
        "url": "",
        "error": error,
    }


# ── API 呼び出し共通処理 ──────────────────────────────────────────────
def _call_api(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Yahoo!ショッピング Item Search API v3 を呼び出し、JSON を返す。

    Returns:
        API のレスポンス dict。エラー時は {"error": str} を返す。
    """
    client_id: Optional[str] = os.getenv("YAHOO_CLIENT_ID")
    if not client_id:
        return {"error": "YAHOO_CLIENT_ID が設定されていません。.env ファイルを確認してください。"}

    params["appid"] = client_id
    params["image_size"] = 640

    _rate_limit()

    try:
        resp = requests.get(_API_ENDPOINT, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"タイムアウト（{_TIMEOUT}秒）。Yahoo API サーバーへの接続が遅延しています。"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP エラー: {e.response.status_code} — {e}"}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"接続エラー: {e}"}
    except Exception as e:
        logger.exception("Unexpected error calling Yahoo Shopping API")
        return {"error": f"予期しないエラー: {e}"}


# ── メイン関数 ────────────────────────────────────────────────────────

def fetch_product_by_item_code(item_code: str) -> Dict[str, Any]:
    """
    Yahoo 商品コードから商品情報を取得する。

    Args:
        item_code: Yahoo 商品コード。
                   "shopcode_itemcode" 形式を "shopcode:itemcode" に変換して
                   ?itemcode= パラメータで検索する。

    Returns:
        {
            "item_code": str,
            "name": str,           # 商品名（日本語）
            "name_en": str,        # 英語名（常に空文字）
            "price": float|None,   # 価格（円）
            "images": [str],       # 画像 URL リスト（最大9枚）
            "category": str,       # 内部カテゴリ値
            "availability": str,   # 在庫状況
            "jan_code": str|None,  # JAN コード
            "url": str,            # 商品ページ URL
            "error": str|None,     # エラーメッセージ（成功時 None）
        }
    """
    item_code = item_code.strip()
    logger.info("Yahoo fetch by item_code: %s", item_code)

    # "shopcode_itemcode" → "shopcode:itemcode" 形式に変換
    # 最初の "_" のみコロンに変換（itemcode 側にアンダースコアが含まれる可能性を考慮）
    api_item_code = item_code.replace("_", ":", 1)

    data = _call_api({"itemcode": api_item_code})
    if "error" in data and data.get("hits") is None:
        return _error_result(item_code, data["error"])

    hits: List[Dict[str, Any]] = data.get("hits", [])
    if not hits:
        return _error_result(item_code, f"商品コード「{item_code}」に該当する商品が見つかりません。")

    return _parse_hit(hits[0], item_code=item_code)


def search_products(keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    キーワードで Yahoo!ショッピング商品を検索する。

    Args:
        keyword: 検索キーワード
        limit: 取得件数（デフォルト 10）

    Returns:
        fetch_product_by_item_code と同じ構造の dict のリスト
    """
    keyword = keyword.strip()
    logger.info("Yahoo search: keyword=%s limit=%d", keyword, limit)

    data = _call_api({"query": keyword, "results": limit})
    if "error" in data and data.get("hits") is None:
        logger.error("Yahoo search error: %s", data["error"])
        return []

    hits: List[Dict[str, Any]] = data.get("hits", [])
    results: List[Dict[str, Any]] = []
    for hit in hits:
        results.append(_parse_hit(hit))
    return results


# ── テスト実行 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = "search"

    if mode == "item" and len(sys.argv) > 2:
        code = sys.argv[2]
        print(f"\n商品コード {code} を取得中...")
        result = fetch_product_by_item_code(code)
        if result["error"]:
            print(f"エラー: {result['error']}")
        else:
            print(f"商品名: {result['name']}")
            print(f"価格: {result['price']} 円" if result["price"] else "価格: 不明")
            print(f"カテゴリ: {result['category']}")
            print(f"在庫: {result['availability']}")
            print(f"JAN: {result['jan_code'] or '—'}")
            print(f"画像数: {len(result['images'])} 枚")
            print(f"URL: {result['url']}")
    else:
        keyword = sys.argv[2] if len(sys.argv) > 2 else "ワイヤレスイヤホン"
        print(f"\nキーワード「{keyword}」で検索中...")
        results = search_products(keyword, limit=3)
        if not results:
            print("結果が取得できませんでした。")
        else:
            for i, r in enumerate(results, 1):
                print(f"\n[{i}] {r['name'][:50]}")
                print(f"    価格: {r['price']} 円 / カテゴリ: {r['category']}")
                print(f"    URL: {r['url'][:80]}")
