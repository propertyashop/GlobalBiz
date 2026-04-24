"""
イーロジコム（elogicom）物流 API クライアント

イーロジコム: https://www.elogicom.jp/
在庫預かり・出荷代行サービス

※ イーロジコムの API は個別契約が必要です。
  本モジュールは API 仕様に準拠したスタブ実装です。
  実際の API エンドポイント・認証方式は契約後に確認してください。

設定 (.env):
    ELOGICOM_API_KEY=xxxx
    ELOGICOM_SHOP_CODE=xxxx
    ELOGICOM_SANDBOX=false
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any

import requests

logger = logging.getLogger(__name__)

_PROD_BASE    = "https://api.elogicom.jp/v1"
_SANDBOX_BASE = "https://api-sandbox.elogicom.jp/v1"


@dataclass
class StockItem:
    """在庫照会結果の1アイテム"""
    sku: str
    jan_code: Optional[str]
    product_name: str
    available_qty: int      # 出荷可能数
    reserved_qty: int       # 引当済み数
    total_qty: int          # 総在庫数
    location: str           # 保管場所コード


@dataclass
class ShipmentRequest:
    """出荷依頼パラメータ"""
    order_id: str                   # 注文ID（ユニーク）
    sku: str                        # 出荷するSKU
    quantity: int                   # 数量
    recipient_name: str             # 受取人名
    recipient_postal: str           # 郵便番号
    recipient_address: str          # 住所
    recipient_phone: str            # 電話番号
    delivery_service: str = "yamato"  # yamato / japanpost / sagawa
    gift_message: str = ""
    fragile: bool = False


@dataclass
class ShipmentResult:
    """出荷依頼結果"""
    success: bool
    shipment_id: Optional[str] = None   # イーロジコム内部ID
    tracking_number: Optional[str] = None
    carrier: Optional[str] = None
    estimated_ship_date: Optional[str] = None
    error: Optional[str] = None


class ElogicomClient:
    """イーロジコム API クライアント"""

    def __init__(self) -> None:
        self.api_key   = os.getenv("ELOGICOM_API_KEY", "")
        self.shop_code = os.getenv("ELOGICOM_SHOP_CODE", "")
        self.sandbox   = os.getenv("ELOGICOM_SANDBOX", "false").lower() == "true"
        self.base      = _SANDBOX_BASE if self.sandbox else _PROD_BASE

    def is_configured(self) -> bool:
        return bool(self.api_key and self.shop_code)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Shop-Code":   self.shop_code,
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    # ── 在庫照会 ───────────────────────────────────────────────────

    def get_stock(
        self,
        sku: Optional[str] = None,
        jan_code: Optional[str] = None,
    ) -> Tuple[List[StockItem], Optional[str]]:
        """
        在庫数を照会する。

        Args:
            sku: SKU コード（指定しない場合は全件）
            jan_code: JAN コード（sku と排他）

        Returns:
            (StockItem リスト, エラーメッセージ or None)
        """
        if not self.is_configured():
            return [], "イーロジコム APIキーが未設定です（設定画面 → 物流設定で入力）"

        params: Dict[str, str] = {}
        if sku:
            params["sku"] = sku
        if jan_code:
            params["jan_code"] = jan_code

        try:
            resp = requests.get(
                f"{self.base}/stocks",
                params=params,
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                return [], f"イーロジコム API エラー {resp.status_code}: {resp.text[:200]}"

            items: List[StockItem] = []
            for row in resp.json().get("stocks", []):
                items.append(StockItem(
                    sku=row.get("sku", ""),
                    jan_code=row.get("jan_code"),
                    product_name=row.get("product_name", ""),
                    available_qty=int(row.get("available_qty", 0)),
                    reserved_qty=int(row.get("reserved_qty", 0)),
                    total_qty=int(row.get("total_qty", 0)),
                    location=row.get("location", ""),
                ))
            return items, None

        except requests.exceptions.Timeout:
            return [], "イーロジコム API タイムアウト（15秒）"
        except Exception as e:
            logger.exception("Elogicom get_stock error")
            return [], f"イーロジコム API 例外: {e}"

    def get_stock_by_sku(self, sku: str) -> Tuple[Optional[StockItem], Optional[str]]:
        """SKU 指定で在庫1件を取得"""
        items, err = self.get_stock(sku=sku)
        if err:
            return None, err
        if not items:
            return None, f"SKU '{sku}' の在庫情報が見つかりません"
        return items[0], None

    # ── 出荷依頼 ───────────────────────────────────────────────────

    def request_shipment(self, req: ShipmentRequest) -> ShipmentResult:
        """
        出荷依頼を送信する。

        Returns:
            ShipmentResult（tracking_number が含まれる）
        """
        if not self.is_configured():
            return ShipmentResult(success=False,
                                  error="イーロジコム APIキーが未設定です")

        payload = {
            "order_id":          req.order_id,
            "sku":               req.sku,
            "quantity":          req.quantity,
            "delivery_service":  req.delivery_service,
            "recipient": {
                "name":    req.recipient_name,
                "postal":  req.recipient_postal,
                "address": req.recipient_address,
                "phone":   req.recipient_phone,
            },
            "options": {
                "gift_message": req.gift_message,
                "fragile":      req.fragile,
            },
        }

        try:
            resp = requests.post(
                f"{self.base}/shipments",
                json=payload,
                headers=self._headers(),
                timeout=20,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return ShipmentResult(
                    success=True,
                    shipment_id=data.get("shipment_id"),
                    tracking_number=data.get("tracking_number"),
                    carrier=data.get("carrier"),
                    estimated_ship_date=data.get("estimated_ship_date"),
                )
            else:
                try:
                    err_msg = resp.json().get("message", resp.text[:200])
                except Exception:
                    err_msg = resp.text[:200]
                return ShipmentResult(success=False,
                                      error=f"API エラー {resp.status_code}: {err_msg}")

        except requests.exceptions.Timeout:
            return ShipmentResult(success=False, error="タイムアウト（20秒）")
        except Exception as e:
            logger.exception("Elogicom shipment error")
            return ShipmentResult(success=False, error=f"例外: {e}")

    # ── 出荷状況確認 ───────────────────────────────────────────────

    def get_shipment_status(self, shipment_id: str) -> Tuple[Dict[str, Any], Optional[str]]:
        """出荷依頼の状況を確認する"""
        if not self.is_configured():
            return {}, "イーロジコム APIキーが未設定です"

        try:
            resp = requests.get(
                f"{self.base}/shipments/{shipment_id}",
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json(), None
            return {}, f"API エラー {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return {}, f"例外: {e}"

    def test_connection(self) -> Tuple[bool, str]:
        """接続テスト（在庫照会APIで確認）"""
        if not self.is_configured():
            return False, "❌ イーロジコム APIキーが未設定です"

        try:
            resp = requests.get(
                f"{self.base}/ping",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code in (200, 204):
                env = "🧪 サンドボックス" if self.sandbox else "🟢 本番環境"
                return True, f"✅ イーロジコム API 接続OK（{env}）"
            return False, f"❌ イーロジコム API エラー {resp.status_code}"
        except Exception as e:
            return False, f"❌ イーロジコム 接続失敗: {e}"


def get_elogicom_client() -> ElogicomClient:
    return ElogicomClient()
