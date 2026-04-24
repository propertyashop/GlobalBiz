"""
Shopee Open Platform API v2 連携モジュール
- HMAC-SHA256 署名認証
- 商品出品・価格更新・在庫更新

公式ドキュメント: https://open.shopee.com/documents
"""

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

SHOPEE_PARTNER_ID  = int(os.getenv("SHOPEE_PARTNER_ID", "0") or 0)
SHOPEE_PARTNER_KEY = os.getenv("SHOPEE_PARTNER_KEY", "")
SHOPEE_SHOP_ID     = int(os.getenv("SHOPEE_SHOP_ID", "0") or 0)
SHOPEE_ACCESS_TOKEN = os.getenv("SHOPEE_ACCESS_TOKEN", "")
SHOPEE_SANDBOX     = os.getenv("SHOPEE_SANDBOX", "false").lower() == "true"

BASE_URL = (
    "https://partner.test-stable.shopeemobile.com"
    if SHOPEE_SANDBOX
    else "https://partner.shopeemobile.com"
)

# Shopee カテゴリーマッピング（category_id）
CATEGORY_MAP: dict[str, int] = {
    "electronics":  100644,   # Electronics
    "clothing":     100631,   # Women's Apparel
    "accessories":  100590,   # Accessories
    "toys":         100564,   # Toys, Kids & Babies
    "food":         100570,   # Food & Beverages
    "cosmetics":    100508,   # Health & Beauty
    "health":       100508,   # Health & Beauty
    "sports":       100563,   # Sports & Outdoors
    "home":         100542,   # Home & Living
    "books":        100652,   # Books, Magazines & Others
    "auto":         100657,   # Automotive
    "other":        100599,   # Others
}


@dataclass
class ListingResult:
    success: bool
    item_id: Optional[int]
    url: Optional[str]
    error: Optional[str]
    raw: Optional[dict]


@dataclass
class UpdateResult:
    success: bool
    error: Optional[str]


class ShopeeClient:
    """Shopee Open Platform API v2 クライアント"""

    def __init__(self):
        self.partner_id  = SHOPEE_PARTNER_ID
        self.partner_key = SHOPEE_PARTNER_KEY
        self.shop_id     = SHOPEE_SHOP_ID
        self.access_token = SHOPEE_ACCESS_TOKEN
        self.base_url    = BASE_URL

    def _sign(self, path: str, timestamp: int) -> str:
        """HMAC-SHA256 署名を生成する"""
        base_str = f"{self.partner_id}{path}{timestamp}{self.access_token}{self.shop_id}"
        return hmac.new(
            self.partner_key.encode("utf-8"),
            base_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Tuple[int, dict]:
        ts = int(time.time())
        sign = self._sign(path, ts)
        base_params = {
            "partner_id": self.partner_id,
            "timestamp": ts,
            "access_token": self.access_token,
            "shop_id": self.shop_id,
            "sign": sign,
        }
        if params:
            base_params.update(params)

        url = f"{self.base_url}{path}"
        resp = requests.request(
            method, url,
            params=base_params,
            json=json_body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return resp.status_code, body

    # ===== 商品出品 =====
    def add_item(self, product: Any, price_sgd: float) -> ListingResult:
        """
        Shopee に商品を出品する。

        Args:
            product: backend.db.models.Product インスタンス
            price_sgd: 販売価格（SGD）
        """
        category_id = CATEGORY_MAP.get(
            product.product_category.value if product.product_category else "other",
            100599,
        )

        body: dict = {
            "original_price": round(price_sgd, 2),
            "description": (product.description or product.name)[:3000],
            "item_name": product.name[:120],
            "normal_stock": int(product.current_stock or 0),
            "weight": round((product.weight_g or 500) / 1000, 2),  # kg
            "category_id": category_id,
            "condition": "NEW",
            "logistics": [
                {
                    "logistic_id": 80011,    # Shopee Standard Delivery（SGP）
                    "enabled": True,
                    "is_free": False,
                    "shipping_fee": round(
                        (product.intl_shipping_cost or 1000) / 112, 2
                    ),  # JPY → SGD 概算
                }
            ],
            "images": [],
        }

        # 画像追加
        if product.image_url:
            body["images"].append({"image_url_list": [product.image_url]})

        # 重量・サイズ設定
        if product.size_cm_l and product.size_cm_w and product.size_cm_h:
            body["dimension"] = {
                "package_length": int(product.size_cm_l),
                "package_width":  int(product.size_cm_w),
                "package_height": int(product.size_cm_h),
            }

        status, resp = self._request("POST", "/api/v2/product/add_item", json_body=body)

        if resp.get("error") == "" or resp.get("item_id"):
            item_id = resp.get("response", {}).get("item_id") or resp.get("item_id")
            url = f"https://shopee.sg/product/{self.shop_id}/{item_id}" if item_id else None
            return ListingResult(
                success=True,
                item_id=item_id,
                url=url,
                error=None,
                raw=resp,
            )

        return ListingResult(
            success=False,
            item_id=None,
            url=None,
            error=resp.get("message", json.dumps(resp)),
            raw=resp,
        )

    # ===== 価格更新 =====
    def update_price(self, item_id: int, price_sgd: float) -> UpdateResult:
        """商品価格を更新する"""
        body = {
            "item_id": item_id,
            "price_list": [
                {
                    "model_id": 0,
                    "original_price": round(price_sgd, 2),
                }
            ],
        }
        _, resp = self._request("POST", "/api/v2/product/update_price", json_body=body)
        if resp.get("error") == "":
            return UpdateResult(success=True, error=None)
        return UpdateResult(success=False, error=resp.get("message", str(resp)))

    # ===== 在庫更新 =====
    def update_stock(self, item_id: int, stock: int) -> UpdateResult:
        """在庫数を更新する"""
        body = {
            "item_id": item_id,
            "stock_list": [
                {
                    "model_id": 0,
                    "normal_stock": stock,
                }
            ],
        }
        _, resp = self._request("POST", "/api/v2/product/update_stock", json_body=body)
        if resp.get("error") == "":
            return UpdateResult(success=True, error=None)
        return UpdateResult(success=False, error=resp.get("message", str(resp)))

    # ===== 出品停止 =====
    def delete_item(self, item_id: int) -> UpdateResult:
        """商品を削除（出品停止）する"""
        body = {"item_id_list": [item_id]}
        _, resp = self._request("POST", "/api/v2/product/delete_item", json_body=body)
        if resp.get("error") == "":
            return UpdateResult(success=True, error=None)
        return UpdateResult(success=False, error=resp.get("message", str(resp)))

    # ===== Access Token 取得 =====
    def get_access_token(self, code: str) -> dict:
        """認証コードを使って Access Token を取得する"""
        ts = int(time.time())
        path = "/api/v2/auth/token/get"
        base_str = f"{self.partner_id}{path}{ts}"
        sign = hmac.new(
            self.partner_key.encode(),
            base_str.encode(),
            hashlib.sha256,
        ).hexdigest()
        body = {"code": code, "shop_id": self.shop_id, "partner_id": self.partner_id}
        params = {"partner_id": self.partner_id, "timestamp": ts, "sign": sign}
        resp = requests.post(f"{self.base_url}{path}", params=params, json=body, timeout=30)
        return resp.json()

    def is_configured(self) -> bool:
        return bool(self.partner_id and self.partner_key and self.shop_id and self.access_token)

    def test_connection(self) -> Tuple[bool, str]:
        """接続テスト"""
        if not self.is_configured():
            return False, "SHOPEE_PARTNER_ID / PARTNER_KEY / SHOP_ID / ACCESS_TOKEN のいずれかが未設定です"
        status, resp = self._request("GET", "/api/v2/shop/get_shop_info")
        if resp.get("error") == "":
            name = resp.get("response", {}).get("shop_name", "")
            return True, f"接続OK（店舗名: {name}）"
        return False, resp.get("message", f"エラー: {resp}")


_client: Optional[ShopeeClient] = None

def get_shopee_client() -> ShopeeClient:
    global _client
    if _client is None:
        _client = ShopeeClient()
    return _client
