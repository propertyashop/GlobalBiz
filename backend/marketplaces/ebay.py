"""
eBay REST API 連携モジュール
- Sell API (出品・在庫・価格更新)
- OAuth 2.0 認証

公式ドキュメント: https://developer.ebay.com/develop/apis/restful-apis/sell-apis
"""

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

EBAY_APP_ID    = os.getenv("EBAY_APP_ID", "")
EBAY_CERT_ID   = os.getenv("EBAY_CERT_ID", "")
EBAY_DEV_ID    = os.getenv("EBAY_DEV_ID", "")
EBAY_TOKEN     = os.getenv("EBAY_TOKEN", "")          # User Access Token
EBAY_SANDBOX   = os.getenv("EBAY_SANDBOX", "false").lower() == "true"

BASE_URL = (
    "https://api.sandbox.ebay.com"
    if EBAY_SANDBOX
    else "https://api.ebay.com"
)

# eBay カテゴリーマッピング（商品カテゴリ → eBay CategoryID）
CATEGORY_MAP: dict[str, str] = {
    "electronics":  "293",     # Consumer Electronics
    "clothing":     "11450",   # Clothing, Shoes & Accessories
    "accessories":  "14223",   # Jewelry & Watches
    "toys":         "220",     # Toys & Hobbies
    "food":         "14308",   # Food & Beverages
    "cosmetics":    "26395",   # Health & Beauty
    "health":       "26395",   # Health & Beauty
    "sports":       "888",     # Sporting Goods
    "home":         "11700",   # Home & Garden
    "books":        "267",     # Books
    "auto":         "6000",    # eBay Motors > Parts & Accessories
    "other":        "99",      # Everything Else
}

# 配送ポリシー（要事前作成）- .env で上書き可
EBAY_FULFILLMENT_POLICY_ID = os.getenv("EBAY_FULFILLMENT_POLICY_ID", "")
EBAY_PAYMENT_POLICY_ID     = os.getenv("EBAY_PAYMENT_POLICY_ID", "")
EBAY_RETURN_POLICY_ID      = os.getenv("EBAY_RETURN_POLICY_ID", "")
EBAY_MERCHANT_LOCATION_KEY = os.getenv("EBAY_MERCHANT_LOCATION_KEY", "")


@dataclass
class ListingResult:
    success: bool
    listing_id: Optional[str]
    url: Optional[str]
    error: Optional[str]
    raw: Optional[dict]


@dataclass
class UpdateResult:
    success: bool
    error: Optional[str]


class EbayClient:
    """eBay Sell API クライアント"""

    def __init__(self):
        self.token = EBAY_TOKEN
        self.base_url = BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        })

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Tuple[int, dict]:
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, json=json_body, params=params, timeout=30)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return resp.status_code, body

    # ===== Inventory Item =====
    def create_or_update_inventory_item(self, sku: str, product: Any) -> UpdateResult:
        """在庫アイテムを作成/更新する"""
        # product は backend.db.models.Product インスタンス
        body = {
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": int(product.current_stock or 0)
                }
            },
            "condition": "NEW",
            "product": {
                "title": product.name[:80],
                "description": product.description or product.name,
                "aspects": {},
                "imageUrls": [product.image_url] if product.image_url else [],
            },
            "packageWeightAndSize": {},
        }

        # 重量設定
        if product.weight_g and product.weight_g > 0:
            body["packageWeightAndSize"]["weight"] = {
                "unit": "GRAM",
                "value": product.weight_g,
            }

        # JANコード設定
        if product.jan_code:
            body["product"]["aspects"]["EAN"] = [product.jan_code]
        if product.asin:
            body["product"]["aspects"]["ASIN"] = [product.asin]

        status, resp = self._request(
            "PUT",
            f"/sell/inventory/v1/inventory_item/{sku}",
            json_body=body,
        )
        if status in (200, 204):
            return UpdateResult(success=True, error=None)
        return UpdateResult(success=False, error=json.dumps(resp.get("errors", resp)))

    # ===== Offer =====
    def create_offer(self, sku: str, price_usd: float, product: Any) -> Tuple[bool, str, Optional[str]]:
        """
        Offer を作成して listing_id を返す。
        Returns: (success, offer_id_or_error, listing_url)
        """
        category_id = CATEGORY_MAP.get(
            product.product_category.value if product.product_category else "other",
            "99"
        )
        body: dict = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "FIXED_PRICE",
            "availableQuantity": int(product.current_stock or 0),
            "categoryId": category_id,
            "listingDescription": product.description or product.name,
            "listingPolicies": {},
            "pricingSummary": {
                "price": {
                    "currency": "USD",
                    "value": f"{price_usd:.2f}",
                }
            },
        }

        # ポリシーが設定されている場合のみ追加
        if EBAY_FULFILLMENT_POLICY_ID:
            body["listingPolicies"]["fulfillmentPolicyId"] = EBAY_FULFILLMENT_POLICY_ID
        if EBAY_PAYMENT_POLICY_ID:
            body["listingPolicies"]["paymentPolicyId"] = EBAY_PAYMENT_POLICY_ID
        if EBAY_RETURN_POLICY_ID:
            body["listingPolicies"]["returnPolicyId"] = EBAY_RETURN_POLICY_ID
        if EBAY_MERCHANT_LOCATION_KEY:
            body["merchantLocationKey"] = EBAY_MERCHANT_LOCATION_KEY

        status, resp = self._request("POST", "/sell/inventory/v1/offer", json_body=body)
        if status == 201:
            offer_id = resp.get("offerId", "")
            return True, offer_id, None
        return False, json.dumps(resp.get("errors", resp)), None

    def publish_offer(self, offer_id: str) -> ListingResult:
        """Offer を公開して listing URL を取得する"""
        status, resp = self._request(
            "POST",
            f"/sell/inventory/v1/offer/{offer_id}/publish",
        )
        if status == 200:
            listing_id = resp.get("listingId", offer_id)
            url = f"https://www.ebay.com/itm/{listing_id}"
            return ListingResult(
                success=True,
                listing_id=listing_id,
                url=url,
                error=None,
                raw=resp,
            )
        return ListingResult(
            success=False,
            listing_id=None,
            url=None,
            error=json.dumps(resp.get("errors", resp)),
            raw=resp,
        )

    # ===== 高レベル API =====
    def create_listing(self, product: Any, price_usd: float) -> ListingResult:
        """
        商品を eBay に出品する（在庫登録 → Offer作成 → 公開）。
        """
        # 1. Inventory Item 作成/更新
        inv_result = self.create_or_update_inventory_item(product.sku, product)
        if not inv_result.success:
            return ListingResult(
                success=False, listing_id=None, url=None,
                error=f"Inventory error: {inv_result.error}", raw=None,
            )

        # 2. Offer 作成
        ok, offer_id_or_err, _ = self.create_offer(product.sku, price_usd, product)
        if not ok:
            return ListingResult(
                success=False, listing_id=None, url=None,
                error=f"Offer error: {offer_id_or_err}", raw=None,
            )

        # 3. 公開
        return self.publish_offer(offer_id_or_err)

    def update_listing(self, listing_id: str, price_usd: Optional[float] = None, stock: Optional[int] = None) -> UpdateResult:
        """価格・在庫を更新する"""
        errors = []
        if price_usd is not None:
            body = {"price": {"currency": "USD", "value": f"{price_usd:.2f}"}}
            status, resp = self._request(
                "PUT",
                f"/sell/inventory/v1/offer/{listing_id}/update_compliance",
                json_body=body,
            )
            if status not in (200, 204):
                errors.append(f"price: {resp}")

        if stock is not None:
            body2 = {"shipToLocationAvailability": {"quantity": stock}}
            status2, resp2 = self._request(
                "PUT",
                f"/sell/inventory/v1/inventory_item/{listing_id}",
                json_body=body2,
            )
            if status2 not in (200, 204):
                errors.append(f"stock: {resp2}")

        if errors:
            return UpdateResult(success=False, error="; ".join(str(e) for e in errors))
        return UpdateResult(success=True, error=None)

    def end_listing(self, listing_id: str) -> UpdateResult:
        """出品を停止する"""
        status, resp = self._request(
            "DELETE",
            f"/sell/inventory/v1/offer/{listing_id}",
        )
        if status in (200, 204):
            return UpdateResult(success=True, error=None)
        return UpdateResult(success=False, error=json.dumps(resp))

    def is_configured(self) -> bool:
        return bool(self.token)

    def test_connection(self) -> Tuple[bool, str]:
        """接続テスト"""
        if not self.token:
            return False, "EBAY_TOKEN が未設定です"
        status, resp = self._request("GET", "/sell/inventory/v1/inventory_item", params={"limit": 1})
        if status == 200:
            return True, "接続OK"
        if status == 401:
            return False, "認証エラー: EBAY_TOKEN を確認してください"
        return False, f"エラー {status}: {resp}"


# モジュールレベルのシングルトン
_client: Optional[EbayClient] = None

def get_ebay_client() -> EbayClient:
    global _client
    if _client is None:
        _client = EbayClient()
    return _client
