"""
楽天市場 Ichiba Item Search API クライアント

楽天商品コードまたはキーワードで商品情報を取得する。

設定 (.env):
    RAKUTEN_APP_ID: 楽天 API アプリケーション ID

使用方法:
    from backend.scrapers.rakuten import fetch_product_by_item_code, search_products
    result = fetch_product_by_item_code("shop:item-001")
    results = search_products("ワイヤレスイヤホン", limit=5)

API リファレンス:
    https://webservice.rakuten.co.jp/documentation/ichiba-item-search
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
_API_ENDPOINT = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
_TIMEOUT = 15  # 秒


# ── カテゴリマッピング（genreId 先頭文字列で判断）──────────────────
def _map_genre_to_category(genre_id: str) -> str:
    """楽天 genreId → 内部カテゴリ値に変換"""
    if genre_id.startswith("215"):
        return "electronics"
    if genre_id.startswith("551"):
        return "clothing"
    if genre_id.startswith("216"):
        return "cosmetics"
    if genre_id.startswith("100") or genre_id.startswith("500"):
        return "home"
    return "other"


# ── レート制限 ────────────────────────────────────────────────────────
def _rate_limit() -> None:
    """0.5〜1.5 秒のランダム待機"""
    wait = random.uniform(0.5, 1.5)
    logger.debug("Rate limit: sleeping %.2f sec", wait)
    time.sleep(wait)


# ── レスポンス変換 ────────────────────────────────────────────────────
def _parse_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """楽天 API の Items[].Item を内部 dict 形式に変換"""
    item_code: str = item.get("itemCode", "")
    name: str = item.get("itemName", "")
    price: Optional[float] = float(item["itemPrice"]) if item.get("itemPrice") is not None else None

    # 画像（imageFlag=1 の場合に mediumImageUrls が入る）
    images: List[str] = []
    for img_obj in item.get("mediumImageUrls", []):
        if isinstance(img_obj, dict):
            url = img_obj.get("imageUrl", "")
        else:
            url = str(img_obj)
        if url:
            images.append(url)
    images = images[:9]

    # カテゴリ
    genre_id: str = str(item.get("genreId", ""))
    category: str = _map_genre_to_category(genre_id)

    # 在庫
    availability_flag = item.get("availability", 1)
    availability: str = "在庫あり" if availability_flag == 1 else "在庫なし"

    # JAN コード
    jan_code: Optional[str] = item.get("janCode") or None

    # 商品 URL
    url: str = item.get("itemUrl", "")

    return {
        "item_code": item_code,
        "name": name,
        "name_en": "",
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
    楽天 Ichiba Item Search API を呼び出し、JSON を返す。

    Returns:
        API のレスポンス dict。エラー時は {"error": str} を返す。
    """
    app_id: Optional[str] = os.getenv("RAKUTEN_APP_ID")
    if not app_id:
        return {"error": "RAKUTEN_APP_ID が設定されていません。.env ファイルを確認してください。"}

    params["applicationId"] = app_id
    params["imageFlag"] = 1
    params["format"] = "json"

    _rate_limit()

    try:
        resp = requests.get(_API_ENDPOINT, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"タイムアウト（{_TIMEOUT}秒）。楽天 API サーバーへの接続が遅延しています。"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP エラー: {e.response.status_code} — {e}"}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"接続エラー: {e}"}
    except Exception as e:
        logger.exception("Unexpected error calling Rakuten API")
        return {"error": f"予期しないエラー: {e}"}


# ── メイン関数 ────────────────────────────────────────────────────────

def fetch_product_by_item_code(item_code: str) -> Dict[str, Any]:
    """
    楽天商品コードから商品情報を取得する。

    Args:
        item_code: 楽天商品コード（例: "shopcode:itemcode-001"）

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
    logger.info("Rakuten fetch by item_code: %s", item_code)

    data = _call_api({"itemCode": item_code})
    if "error" in data and data.get("Items") is None:
        return _error_result(item_code, data["error"])

    items: List[Dict[str, Any]] = data.get("Items", [])
    if not items:
        return _error_result(item_code, f"商品コード「{item_code}」に該当する商品が見つかりません。")

    # Items は [{"Item": {...}}, ...] 形式
    first = items[0]
    raw_item: Dict[str, Any] = first.get("Item", first)
    return _parse_item(raw_item)


def search_products(keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    キーワードで楽天商品を検索する。

    Args:
        keyword: 検索キーワード
        limit: 取得件数（デフォルト 10、最大 30）

    Returns:
        fetch_product_by_item_code と同じ構造の dict のリスト
    """
    keyword = keyword.strip()
    logger.info("Rakuten search: keyword=%s limit=%d", keyword, limit)

    data = _call_api({"keyword": keyword, "hits": min(limit, 30)})
    if "error" in data and data.get("Items") is None:
        logger.error("Rakuten search error: %s", data["error"])
        return []

    items: List[Dict[str, Any]] = data.get("Items", [])
    results: List[Dict[str, Any]] = []
    for entry in items:
        raw_item: Dict[str, Any] = entry.get("Item", entry)
        results.append(_parse_item(raw_item))
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
