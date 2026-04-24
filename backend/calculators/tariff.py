"""
関税計算モジュール

データソース参考:
  - 米国: HTS (Harmonized Tariff Schedule)
  - シンガポール: ACDD (Singapore Customs)
  - ASEAN各国: 一般関税率 (MFN)
  - 台湾・香港: 財政部関税署

注意: 本テーブルは概算値です。実際の申告には正式な HS コード照会が必要です。
"""

from dataclasses import dataclass
from typing import NamedTuple


# ===== 国コード定義 =====
COUNTRY_NAMES: dict[str, str] = {
    "USA": "アメリカ",
    "SGP": "シンガポール",
    "MYS": "マレーシア",
    "THA": "タイ",
    "PHL": "フィリピン",
    "IDN": "インドネシア",
    "VNM": "ベトナム",
    "TWN": "台湾",
    "HKG": "香港",
    "AUS": "オーストラリア",
    "GBR": "イギリス",
    "DEU": "ドイツ（EU）",
    "CAN": "カナダ",
    "KOR": "韓国",
}

# eBay の主要販売先
EBAY_COUNTRIES = ["USA", "AUS", "GBR", "DEU", "CAN", "TWN", "HKG"]
# Shopee の主要販売先
SHOPEE_COUNTRIES = ["SGP", "MYS", "THA", "PHL", "IDN", "VNM", "TWN"]


# ===== 関税率テーブル =====
# 構造: { "国コード": { "カテゴリ": 税率(0.0〜1.0) } }
# カテゴリは ProductCategory の value に対応
TARIFF_RATES: dict[str, dict[str, float]] = {
    "USA": {
        "electronics":  0.00,   # 家電: 多くはゼロ（IT協定）
        "clothing":     0.12,   # 衣類: 平均12%
        "accessories":  0.065,  # アクセサリー: 6.5%
        "toys":         0.00,   # おもちゃ: ゼロ
        "food":         0.05,   # 食品: 5%（品目により異なる）
        "cosmetics":    0.00,   # 化粧品: ゼロ
        "health":       0.00,   # 健康: ゼロ
        "sports":       0.04,   # スポーツ: 4%
        "home":         0.035,  # 家具: 3.5%
        "books":        0.00,   # 書籍: ゼロ
        "auto":         0.025,  # 自動車部品: 2.5%
        "other":        0.04,
    },
    "SGP": {
        # シンガポール: 基本的に輸入関税ゼロ（GST 9% は別途）
        "electronics":  0.00,
        "clothing":     0.00,
        "accessories":  0.00,
        "toys":         0.00,
        "food":         0.00,
        "cosmetics":    0.00,
        "health":       0.00,
        "sports":       0.00,
        "home":         0.00,
        "books":        0.00,
        "auto":         0.00,
        "other":        0.00,
    },
    "MYS": {
        "electronics":  0.00,
        "clothing":     0.20,
        "accessories":  0.25,
        "toys":         0.10,
        "food":         0.00,
        "cosmetics":    0.10,
        "health":       0.00,
        "sports":       0.10,
        "home":         0.15,
        "books":        0.00,
        "auto":         0.30,
        "other":        0.10,
    },
    "THA": {
        "electronics":  0.00,
        "clothing":     0.30,
        "accessories":  0.30,
        "toys":         0.20,
        "food":         0.30,
        "cosmetics":    0.20,
        "health":       0.10,
        "sports":       0.20,
        "home":         0.20,
        "books":        0.00,
        "auto":         0.80,
        "other":        0.20,
    },
    "PHL": {
        "electronics":  0.00,
        "clothing":     0.15,
        "accessories":  0.15,
        "toys":         0.10,
        "food":         0.05,
        "cosmetics":    0.10,
        "health":       0.00,
        "sports":       0.10,
        "home":         0.15,
        "books":        0.00,
        "auto":         0.30,
        "other":        0.10,
    },
    "IDN": {
        "electronics":  0.00,
        "clothing":     0.25,
        "accessories":  0.20,
        "toys":         0.15,
        "food":         0.05,
        "cosmetics":    0.10,
        "health":       0.00,
        "sports":       0.10,
        "home":         0.20,
        "books":        0.00,
        "auto":         0.40,
        "other":        0.15,
    },
    "VNM": {
        "electronics":  0.00,
        "clothing":     0.20,
        "accessories":  0.25,
        "toys":         0.15,
        "food":         0.05,
        "cosmetics":    0.20,
        "health":       0.00,
        "sports":       0.15,
        "home":         0.20,
        "books":        0.00,
        "auto":         0.70,
        "other":        0.15,
    },
    "TWN": {
        "electronics":  0.00,
        "clothing":     0.123,
        "accessories":  0.05,
        "toys":         0.00,
        "food":         0.10,
        "cosmetics":    0.05,
        "health":       0.00,
        "sports":       0.05,
        "home":         0.05,
        "books":        0.00,
        "auto":         0.175,
        "other":        0.05,
    },
    "HKG": {
        # 香港: 輸入関税ゼロ
        "electronics":  0.00,
        "clothing":     0.00,
        "accessories":  0.00,
        "toys":         0.00,
        "food":         0.00,
        "cosmetics":    0.00,
        "health":       0.00,
        "sports":       0.00,
        "home":         0.00,
        "books":        0.00,
        "auto":         0.00,
        "other":        0.00,
    },
    "AUS": {
        "electronics":  0.00,
        "clothing":     0.05,
        "accessories":  0.05,
        "toys":         0.00,
        "food":         0.00,
        "cosmetics":    0.00,
        "health":       0.00,
        "sports":       0.05,
        "home":         0.05,
        "books":        0.00,
        "auto":         0.05,
        "other":        0.05,
    },
    "GBR": {
        "electronics":  0.00,
        "clothing":     0.12,
        "accessories":  0.065,
        "toys":         0.00,
        "food":         0.00,
        "cosmetics":    0.065,
        "health":       0.00,
        "sports":       0.04,
        "home":         0.035,
        "books":        0.00,
        "auto":         0.065,
        "other":        0.04,
    },
    "DEU": {  # EU共通税率
        "electronics":  0.00,
        "clothing":     0.12,
        "accessories":  0.065,
        "toys":         0.00,
        "food":         0.095,
        "cosmetics":    0.065,
        "health":       0.00,
        "sports":       0.04,
        "home":         0.035,
        "books":        0.00,
        "auto":         0.065,
        "other":        0.04,
    },
    "CAN": {
        "electronics":  0.00,
        "clothing":     0.18,
        "accessories":  0.07,
        "toys":         0.00,
        "food":         0.00,
        "cosmetics":    0.00,
        "health":       0.00,
        "sports":       0.035,
        "home":         0.065,
        "books":        0.00,
        "auto":         0.065,
        "other":        0.05,
    },
    "KOR": {
        "electronics":  0.00,
        "clothing":     0.13,
        "accessories":  0.08,
        "toys":         0.00,
        "food":         0.30,
        "cosmetics":    0.065,
        "health":       0.00,
        "sports":       0.08,
        "home":         0.08,
        "books":        0.00,
        "auto":         0.08,
        "other":        0.08,
    },
}

# デフォルト税率（国が未定義の場合）
DEFAULT_TARIFF_RATE = 0.05

# 免税枠（USD）: この金額以下は関税免除の国
DE_MINIMIS_USD: dict[str, float] = {
    "USA": 800,
    "SGP": 400,
    "MYS": 0,
    "THA": 0,
    "PHL": 10,
    "IDN": 3,
    "VNM": 0,
    "TWN": 50,
    "HKG": 9999,  # 事実上全額免除
    "AUS": 1000,
    "GBR": 150,
    "DEU": 150,
    "CAN": 40,
    "KOR": 150,
}


@dataclass
class TariffResult:
    country_code: str
    country_name: str
    category: str
    product_price_jpy: float
    product_price_usd: float
    tariff_rate: float
    tariff_amount_usd: float
    tariff_amount_jpy: float
    is_de_minimis: bool          # 免税枠以下か
    de_minimis_threshold_usd: float
    note: str


def calculate_tariff(
    price_jpy: float,
    country_code: str,
    category: str,
    exchange_rate_usd: float = 150.0,
) -> TariffResult:
    """
    関税を計算する。

    Args:
        price_jpy: 商品価格（円）
        country_code: 仕向け国コード（"USA", "SGP" など）
        category: 商品カテゴリ（ProductCategory の value）
        exchange_rate_usd: 円→USD レート（デフォルト 150）

    Returns:
        TariffResult: 計算結果詳細
    """
    price_usd = price_jpy / exchange_rate_usd
    country_name = COUNTRY_NAMES.get(country_code, country_code)

    # 国別・カテゴリ別税率を取得
    country_rates = TARIFF_RATES.get(country_code, {})
    rate = country_rates.get(category, DEFAULT_TARIFF_RATE)

    # 免税枠チェック
    threshold = DE_MINIMIS_USD.get(country_code, 0)
    is_exempt = price_usd <= threshold

    if is_exempt:
        tariff_usd = 0.0
        note = f"免税枠（${threshold:.0f} USD）以下のため関税ゼロ"
    else:
        tariff_usd = price_usd * rate
        note = f"関税率 {rate*100:.1f}%（{country_name}・{category}）"

    tariff_jpy = tariff_usd * exchange_rate_usd

    return TariffResult(
        country_code=country_code,
        country_name=country_name,
        category=category,
        product_price_jpy=price_jpy,
        product_price_usd=price_usd,
        tariff_rate=rate,
        tariff_amount_usd=tariff_usd,
        tariff_amount_jpy=tariff_jpy,
        is_de_minimis=is_exempt,
        de_minimis_threshold_usd=threshold,
        note=note,
    )


def calculate_tariff_multi_country(
    price_jpy: float,
    country_codes: list[str],
    category: str,
    exchange_rate_usd: float = 150.0,
) -> dict[str, TariffResult]:
    """複数国の関税を一括計算する"""
    return {
        code: calculate_tariff(price_jpy, code, category, exchange_rate_usd)
        for code in country_codes
    }


if __name__ == "__main__":
    # 動作テスト
    result = calculate_tariff(30000, "USA", "electronics")
    print(f"{result.country_name}: 関税 ¥{result.tariff_amount_jpy:.0f} ({result.note})")

    result2 = calculate_tariff(5000, "USA", "clothing")
    print(f"{result2.country_name}: 関税 ¥{result2.tariff_amount_jpy:.0f} ({result2.note})")
