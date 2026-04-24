"""
シーパス（Seapass）国際配送 API クライアント

シーパス: https://www.seapass.co.jp/
転送倉庫・国際配送代行サービス（日本→海外）

※ シーパスの API は契約後に提供されます。
  本モジュールはシーパスの API 仕様に準拠したスタブ実装です。

対応機能:
  - 国際送料計算（重量・サイズ・仕向け国・サービス種別）
  - 出荷依頼
  - 追跡番号取得・追跡照会

設定 (.env):
    SEAPASS_API_KEY=xxxx
    SEAPASS_CUSTOMER_CODE=xxxx
    SEAPASS_SANDBOX=false
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

import requests

logger = logging.getLogger(__name__)

_PROD_BASE    = "https://api.seapass.co.jp/v2"
_SANDBOX_BASE = "https://api-sandbox.seapass.co.jp/v2"

# シーパス対応サービス
SEAPASS_SERVICES = {
    "EMS":          "EMS（国際スピード郵便）",
    "SAL":          "SAL（航空・船便混載）",
    "AIRMAIL":      "国際航空便",
    "SURFACE":      "国際船便",
    "DHL_EXPRESS":  "DHL Express",
    "FEDEX_IP":     "FedEx 国際プライオリティ",
    "UPS_EXPRESS":  "UPS Express",
}

# 3文字 → 2文字 ISO 変換
_COUNTRY_ISO2 = {
    "USA": "US", "SGP": "SG", "MYS": "MY", "THA": "TH",
    "PHL": "PH", "IDN": "ID", "VNM": "VN", "TWN": "TW",
    "HKG": "HK", "AUS": "AU", "GBR": "GB", "DEU": "DE",
    "CAN": "CA", "KOR": "KR",
}


@dataclass
class ShippingRate:
    """送料見積もり結果"""
    service_code: str
    service_name: str
    charge_jpy: float
    transit_days_min: Optional[int]
    transit_days_max: Optional[int]
    weight_kg: float
    max_weight_kg: Optional[float]
    tracking_available: bool
    notes: str = ""


@dataclass
class SeapassShipment:
    """出荷依頼パラメータ"""
    order_id: str
    service_code: str               # EMS / DHL_EXPRESS など
    dest_country: str               # 3文字コード
    recipient_name: str
    recipient_address1: str
    recipient_address2: str = ""
    recipient_city: str = ""
    recipient_state: str = ""
    recipient_postal: str = ""
    recipient_phone: str = ""
    weight_kg: float = 1.0
    length_cm: float = 30.0
    width_cm: float = 20.0
    height_cm: float = 10.0
    declared_value_usd: float = 0.0
    contents_description: str = ""
    quantity: int = 1


@dataclass
class SeapassShipmentResult:
    success: bool
    seapass_order_id: Optional[str] = None
    tracking_number: Optional[str] = None
    carrier: Optional[str] = None
    ship_date: Optional[str] = None
    label_url: Optional[str] = None    # 送り状PDF URL
    error: Optional[str] = None


class SeapassClient:
    """シーパス国際配送 API クライアント"""

    def __init__(self) -> None:
        self.api_key       = os.getenv("SEAPASS_API_KEY", "")
        self.customer_code = os.getenv("SEAPASS_CUSTOMER_CODE", "")
        self.sandbox       = os.getenv("SEAPASS_SANDBOX", "false").lower() == "true"
        self.base          = _SANDBOX_BASE if self.sandbox else _PROD_BASE

    def is_configured(self) -> bool:
        return bool(self.api_key and self.customer_code)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization":   f"Bearer {self.api_key}",
            "X-Customer-Code": self.customer_code,
            "Content-Type":    "application/json",
            "Accept":          "application/json",
        }

    # ── 送料計算 ───────────────────────────────────────────────────

    def calculate_rate(
        self,
        weight_kg: float,
        dest_country: str,
        length_cm: float = 30.0,
        width_cm: float = 20.0,
        height_cm: float = 10.0,
        services: Optional[List[str]] = None,
    ) -> Tuple[List[ShippingRate], Optional[str]]:
        """
        国際送料を見積もる。

        Args:
            weight_kg: 重量（kg）
            dest_country: 仕向け国コード（3文字 or 2文字 ISO）
            services: 取得するサービス種別リスト（None = 全サービス）

        Returns:
            (ShippingRate リスト, エラーメッセージ or None)
        """
        if not self.is_configured():
            return [], "シーパス APIキーが未設定です（設定画面 → 物流設定で入力）"

        dest_iso2 = _COUNTRY_ISO2.get(dest_country.upper(), dest_country[:2].upper())

        payload: Dict[str, Any] = {
            "origin_country": "JP",
            "dest_country":   dest_iso2,
            "weight_kg":      weight_kg,
            "dimensions": {
                "length_cm": length_cm,
                "width_cm":  width_cm,
                "height_cm": height_cm,
            },
        }
        if services:
            payload["services"] = services

        try:
            resp = requests.post(
                f"{self.base}/rates",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                return [], f"シーパス API エラー {resp.status_code}: {resp.text[:200]}"

            rates: List[ShippingRate] = []
            for row in resp.json().get("rates", []):
                rates.append(ShippingRate(
                    service_code=row.get("service_code", ""),
                    service_name=SEAPASS_SERVICES.get(
                        row.get("service_code", ""), row.get("service_name", "")),
                    charge_jpy=float(row.get("charge_jpy", 0)),
                    transit_days_min=row.get("transit_days_min"),
                    transit_days_max=row.get("transit_days_max"),
                    weight_kg=weight_kg,
                    max_weight_kg=row.get("max_weight_kg"),
                    tracking_available=bool(row.get("tracking_available", True)),
                    notes=row.get("notes", ""),
                ))
            # 料金順ソート
            rates.sort(key=lambda r: r.charge_jpy)
            return rates, None

        except requests.exceptions.Timeout:
            return [], "シーパス API タイムアウト（15秒）"
        except Exception as e:
            logger.exception("Seapass rate error")
            return [], f"シーパス API 例外: {e}"

    # ── 出荷依頼 ───────────────────────────────────────────────────

    def create_shipment(self, shipment: SeapassShipment) -> SeapassShipmentResult:
        """
        国際出荷を依頼する。

        Returns:
            SeapassShipmentResult（追跡番号・ラベル URL を含む）
        """
        if not self.is_configured():
            return SeapassShipmentResult(success=False,
                                         error="シーパス APIキーが未設定です")

        dest_iso2 = _COUNTRY_ISO2.get(shipment.dest_country.upper(),
                                       shipment.dest_country[:2].upper())
        payload = {
            "order_id":      shipment.order_id,
            "service_code":  shipment.service_code,
            "dest_country":  dest_iso2,
            "recipient": {
                "name":     shipment.recipient_name,
                "address1": shipment.recipient_address1,
                "address2": shipment.recipient_address2,
                "city":     shipment.recipient_city,
                "state":    shipment.recipient_state,
                "postal":   shipment.recipient_postal,
                "phone":    shipment.recipient_phone,
            },
            "parcel": {
                "weight_kg": shipment.weight_kg,
                "length_cm": shipment.length_cm,
                "width_cm":  shipment.width_cm,
                "height_cm": shipment.height_cm,
            },
            "customs": {
                "declared_value_usd":  shipment.declared_value_usd,
                "contents":            shipment.contents_description,
                "quantity":            shipment.quantity,
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
                return SeapassShipmentResult(
                    success=True,
                    seapass_order_id=data.get("seapass_order_id"),
                    tracking_number=data.get("tracking_number"),
                    carrier=data.get("carrier"),
                    ship_date=data.get("ship_date"),
                    label_url=data.get("label_url"),
                )
            else:
                try:
                    err = resp.json().get("message", resp.text[:200])
                except Exception:
                    err = resp.text[:200]
                return SeapassShipmentResult(success=False,
                                             error=f"API エラー {resp.status_code}: {err}")

        except requests.exceptions.Timeout:
            return SeapassShipmentResult(success=False, error="タイムアウト（20秒）")
        except Exception as e:
            logger.exception("Seapass shipment error")
            return SeapassShipmentResult(success=False, error=f"例外: {e}")

    # ── 追跡番号照会 ───────────────────────────────────────────────

    def track_shipment(
        self, tracking_number: str
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """追跡番号で配送状況を照会する"""
        if not self.is_configured():
            return {}, "シーパス APIキーが未設定です"

        try:
            resp = requests.get(
                f"{self.base}/tracking/{tracking_number}",
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json(), None
            return {}, f"API エラー {resp.status_code}"
        except Exception as e:
            return {}, f"例外: {e}"

    def test_connection(self) -> Tuple[bool, str]:
        """接続テスト"""
        if not self.is_configured():
            return False, "❌ シーパス APIキーが未設定です"

        try:
            resp = requests.get(
                f"{self.base}/ping",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code in (200, 204):
                env = "🧪 サンドボックス" if self.sandbox else "🟢 本番環境"
                return True, f"✅ シーパス API 接続OK（{env}）"
            return False, f"❌ シーパス API エラー {resp.status_code}"
        except Exception as e:
            return False, f"❌ シーパス 接続失敗: {e}"


def get_seapass_client() -> SeapassClient:
    return SeapassClient()
