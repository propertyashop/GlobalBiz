"""
FedEx Ship API クライアント

FedEx Developer Portal: https://developer.fedex.com/api/ja-jp/home.html
APIバージョン: v1 (REST)

対応機能:
  - 国内送料見積もり（重量・サイズ・発送元・発送先）
  - 国際送料見積もり（重量・国・サービス種別）
  - OAuth2 トークン取得（client_credentials）

設定 (.env):
    FEDEX_CLIENT_ID=xxxx
    FEDEX_CLIENT_SECRET=xxxx
    FEDEX_ACCOUNT_NUMBER=xxxx
    FEDEX_SANDBOX=false
"""

import os
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple

import requests

logger = logging.getLogger(__name__)

# ── エンドポイント ─────────────────────────────────────────────────
_PROD_BASE    = "https://apis.fedex.com"
_SANDBOX_BASE = "https://apis-sandbox.fedex.com"

# ── FedEx サービス種別 ─────────────────────────────────────────────
DOMESTIC_SERVICES = {
    "FEDEX_IP_1": "FedEx 国際プライオリティ",
    "FEDEX_IE":   "FedEx 国際エコノミー",
    "GROUND_HOME_DELIVERY": "FedEx Ground",
}
INTL_SERVICES = {
    "INTERNATIONAL_PRIORITY":       "国際プライオリティ",
    "INTERNATIONAL_ECONOMY":        "国際エコノミー",
    "INTERNATIONAL_FIRST":          "国際ファースト",
    "FEDEX_INTERNATIONAL_PRIORITY": "FedEx 国際プライオリティ",
}

# 日本の郵便番号 → FedEx 発送元デフォルト
DEFAULT_ORIGIN_POSTAL = "153-0061"  # 東京都目黒区
DEFAULT_ORIGIN_COUNTRY = "JP"


@dataclass
class RateResult:
    service_name: str
    total_charge: float
    currency: str
    transit_days: Optional[int]
    delivery_date: Optional[str]
    error: Optional[str] = None


class FedExClient:
    """FedEx Rate & Transit API クライアント"""

    def __init__(self) -> None:
        self.client_id     = os.getenv("FEDEX_CLIENT_ID", "")
        self.client_secret = os.getenv("FEDEX_CLIENT_SECRET", "")
        self.account_num   = os.getenv("FEDEX_ACCOUNT_NUMBER", "")
        self.sandbox       = os.getenv("FEDEX_SANDBOX", "false").lower() == "true"
        self.base          = _SANDBOX_BASE if self.sandbox else _PROD_BASE
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.account_num)

    # ── OAuth2 ─────────────────────────────────────────────────────

    def _get_token(self) -> Optional[str]:
        """OAuth2 access_token を取得・キャッシュ"""
        now = time.time()
        if self._token and now < self._token_expires - 30:
            return self._token

        try:
            resp = requests.post(
                f"{self.base}/oauth/token",
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token         = data.get("access_token")
            self._token_expires = now + int(data.get("expires_in", 3600))
            return self._token
        except Exception as e:
            logger.error("FedEx token error: %s", e)
            return None

    def _headers(self) -> dict:
        token = self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "X-locale":      "ja_JP",
        }

    # ── 料金見積もり共通 ───────────────────────────────────────────

    def _rate_request(self, payload: dict) -> Tuple[List[RateResult], Optional[str]]:
        """FedEx Rates API を呼び出して RateResult リストを返す"""
        if not self.is_configured():
            return [], "FedEx APIキーが未設定です（設定画面 → 物流設定で入力）"

        token = self._get_token()
        if not token:
            return [], "FedEx 認証トークンの取得に失敗しました"

        try:
            resp = requests.post(
                f"{self.base}/rate/v1/rates/quotes",
                json=payload,
                headers=self._headers(),
                timeout=20,
            )
            if resp.status_code != 200:
                try:
                    err = resp.json().get("errors", [{}])[0].get("message", resp.text[:200])
                except Exception:
                    err = resp.text[:200]
                return [], f"FedEx API エラー {resp.status_code}: {err}"

            data = resp.json()
            results: List[RateResult] = []
            for rate_reply in data.get("output", {}).get("rateReplyDetails", []):
                svc = rate_reply.get("serviceType", "")
                svc_name = INTL_SERVICES.get(svc, DOMESTIC_SERVICES.get(svc, svc))
                for detail in rate_reply.get("ratedShipmentDetails", []):
                    charge = detail.get("totalNetChargeWithDutiesAndTaxes", {})
                    results.append(RateResult(
                        service_name=svc_name,
                        total_charge=float(charge.get("amount", 0)),
                        currency=charge.get("currency", "JPY"),
                        transit_days=rate_reply.get("operationalDetail", {}).get("transitTime"),
                        delivery_date=rate_reply.get("operationalDetail", {}).get("deliveryDate"),
                    ))
            return results, None

        except requests.exceptions.Timeout:
            return [], "FedEx API タイムアウト（20秒）"
        except Exception as e:
            logger.exception("FedEx rate error")
            return [], f"FedEx API 例外: {e}"

    # ── 国内送料見積もり ───────────────────────────────────────────

    def get_domestic_rate(
        self,
        weight_kg: float,
        length_cm: float,
        width_cm: float,
        height_cm: float,
        origin_postal: str = DEFAULT_ORIGIN_POSTAL,
        dest_postal: str = "060-0001",   # 札幌（遠距離の例）
        service: str = "GROUND_HOME_DELIVERY",
    ) -> Tuple[List[RateResult], Optional[str]]:
        """国内送料見積もり"""
        payload = {
            "accountNumber": {"value": self.account_num},
            "requestedShipment": {
                "shipper": {
                    "address": {
                        "postalCode":  origin_postal.replace("-", ""),
                        "countryCode": "JP",
                    }
                },
                "recipient": {
                    "address": {
                        "postalCode":  dest_postal.replace("-", ""),
                        "countryCode": "JP",
                    }
                },
                "pickupType": "USE_SCHEDULED_PICKUP",
                "rateRequestType": ["LIST"],
                "requestedPackageLineItems": [{
                    "weight":     {"units": "KG", "value": weight_kg},
                    "dimensions": {
                        "length": int(length_cm),
                        "width":  int(width_cm),
                        "height": int(height_cm),
                        "units":  "CM",
                    },
                }],
            },
        }
        return self._rate_request(payload)

    # ── 国際送料見積もり ───────────────────────────────────────────

    def get_international_rate(
        self,
        weight_kg: float,
        dest_country: str,
        length_cm: float = 30.0,
        width_cm: float = 20.0,
        height_cm: float = 10.0,
        service: str = "INTERNATIONAL_PRIORITY",
    ) -> Tuple[List[RateResult], Optional[str]]:
        """国際送料見積もり（発送元: 日本）

        Args:
            dest_country: 2文字 ISO 国コード（例: "US", "SG", "TW"）
        """
        # 3文字コード → 2文字
        country_map = {
            "USA": "US", "SGP": "SG", "MYS": "MY", "THA": "TH",
            "PHL": "PH", "IDN": "ID", "VNM": "VN", "TWN": "TW",
            "HKG": "HK", "AUS": "AU", "GBR": "GB", "DEU": "DE",
            "CAN": "CA", "KOR": "KR",
        }
        dest_iso2 = country_map.get(dest_country.upper(), dest_country[:2].upper())

        payload = {
            "accountNumber": {"value": self.account_num},
            "requestedShipment": {
                "shipper": {
                    "address": {
                        "postalCode":  DEFAULT_ORIGIN_POSTAL.replace("-", ""),
                        "countryCode": "JP",
                    }
                },
                "recipient": {
                    "address": {
                        "countryCode": dest_iso2,
                        "residential": False,
                    }
                },
                "pickupType": "USE_SCHEDULED_PICKUP",
                "rateRequestType": ["LIST"],
                "requestedPackageLineItems": [{
                    "weight":     {"units": "KG", "value": weight_kg},
                    "dimensions": {
                        "length": int(length_cm),
                        "width":  int(width_cm),
                        "height": int(height_cm),
                        "units":  "CM",
                    },
                }],
            },
        }
        return self._rate_request(payload)

    def test_connection(self) -> Tuple[bool, str]:
        """接続テスト（トークン取得確認）"""
        if not self.is_configured():
            return False, "❌ FedEx APIキーが未設定です"
        token = self._get_token()
        if token:
            env = "🧪 サンドボックス" if self.sandbox else "🟢 本番環境"
            return True, f"✅ FedEx API 接続OK（{env}）"
        return False, "❌ FedEx 認証失敗（Client ID/Secret を確認してください）"


def get_fedex_client() -> FedExClient:
    return FedExClient()
