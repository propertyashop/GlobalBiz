"""
配送料計算モジュール

国内配送: 日本郵便・ヤマト運輸・佐川急便の目安料金
国際配送: EMS・DHL・FedEx の目安料金

注意: 料金は2024年基準の目安値です。実際の送料は各社に確認してください。
"""

from dataclasses import dataclass
from enum import Enum


class DomesticCarrier(str, Enum):
    JAPAN_POST = "japan_post"   # 日本郵便（ゆうパック）
    YAMATO = "yamato"           # ヤマト運輸（宅急便）
    SAGAWA = "sagawa"           # 佐川急便


class IntlCarrier(str, Enum):
    EMS = "ems"          # 国際スピード郵便（日本郵便）
    DHL = "dhl"          # DHL
    FEDEX = "fedex"      # FedEx


class SizeClass(str, Enum):
    S60 = "60"   # 3辺合計 60cm以内・2kg以内
    S80 = "80"   # 3辺合計 80cm以内・5kg以内
    S100 = "100" # 3辺合計 100cm以内・10kg以内
    S120 = "120" # 3辺合計 120cm以内・15kg以内
    S140 = "140" # 3辺合計 140cm以内・20kg以内
    S160 = "160" # 3辺合計 160cm以内・25kg以内


@dataclass
class DomesticShippingResult:
    carrier: str
    carrier_name: str
    size_class: str
    weight_g: float
    fee_jpy: float
    note: str


@dataclass
class IntlShippingResult:
    carrier: str
    carrier_name: str
    country_code: str
    zone: int
    weight_g: float
    fee_jpy: float
    estimated_days: str
    note: str


# ===== 国内配送料テーブル =====
# ヤマト宅急便（2024年標準料金・関東発）
YAMATO_RATES: dict[str, int] = {
    "60":  1100,
    "80":  1210,
    "100": 1430,
    "120": 1650,
    "140": 1870,
    "160": 2090,
}

# 日本郵便ゆうパック（2024年標準料金・関東発）
JAPAN_POST_RATES: dict[str, int] = {
    "60":  1090,
    "80":  1200,
    "100": 1420,
    "120": 1640,
    "140": 1860,
    "160": 2080,
}

# 佐川急便（2024年標準料金・関東発）
SAGAWA_RATES: dict[str, int] = {
    "60":  1060,
    "80":  1180,
    "100": 1400,
    "120": 1620,
    "140": 1840,
    "160": 2060,
}

# 重量(g) → サイズクラスのデフォルトマッピング
WEIGHT_TO_SIZE: list[tuple[int, str]] = [
    (500,   "60"),
    (1000,  "60"),
    (2000,  "60"),
    (5000,  "80"),
    (10000, "100"),
    (15000, "120"),
    (20000, "140"),
    (25000, "160"),
]


def _weight_to_size_class(weight_g: float) -> str:
    for limit, size in WEIGHT_TO_SIZE:
        if weight_g <= limit:
            return size
    return "160"


def calculate_domestic_shipping(
    weight_g: float,
    size_cm_l: float = 0,
    size_cm_w: float = 0,
    size_cm_h: float = 0,
    carrier: DomesticCarrier = DomesticCarrier.YAMATO,
) -> DomesticShippingResult:
    """
    国内送料を計算する。

    Args:
        weight_g: 重量（グラム）
        size_cm_l/w/h: 縦横高（cm）。0の場合は重量から推定
        carrier: 配送業者

    Returns:
        DomesticShippingResult
    """
    # サイズクラス決定（3辺合計 vs 重量の大きい方）
    girth = size_cm_l + size_cm_w + size_cm_h
    size_by_girth = (
        "60" if girth <= 60 else
        "80" if girth <= 80 else
        "100" if girth <= 100 else
        "120" if girth <= 120 else
        "140" if girth <= 140 else "160"
    ) if girth > 0 else None

    size_by_weight = _weight_to_size_class(weight_g)
    # 大きい方（= 高い料金）を採用
    if size_by_girth:
        size_class = max(size_by_girth, size_by_weight, key=lambda s: int(s))
    else:
        size_class = size_by_weight

    if carrier == DomesticCarrier.YAMATO:
        fee = YAMATO_RATES.get(size_class, 2090)
        name = "ヤマト宅急便"
    elif carrier == DomesticCarrier.JAPAN_POST:
        fee = JAPAN_POST_RATES.get(size_class, 2080)
        name = "日本郵便ゆうパック"
    else:
        fee = SAGAWA_RATES.get(size_class, 2060)
        name = "佐川急便"

    return DomesticShippingResult(
        carrier=carrier.value,
        carrier_name=name,
        size_class=size_class,
        weight_g=weight_g,
        fee_jpy=float(fee),
        note=f"{size_class}サイズ・{weight_g:.0f}g（関東発・税込目安）",
    )


def get_all_domestic_estimates(
    weight_g: float,
    size_cm_l: float = 0,
    size_cm_w: float = 0,
    size_cm_h: float = 0,
) -> list[DomesticShippingResult]:
    """3社の国内送料をまとめて取得する"""
    return [
        calculate_domestic_shipping(weight_g, size_cm_l, size_cm_w, size_cm_h, c)
        for c in DomesticCarrier
    ]


# ===== 国際配送料テーブル =====
# EMS ゾーン分類
EMS_ZONES: dict[str, int] = {
    "USA": 2, "CAN": 2,
    "GBR": 1, "DEU": 1,
    "AUS": 3,
    "SGP": 4, "MYS": 4, "THA": 4, "PHL": 4, "IDN": 4, "VNM": 4,
    "TWN": 4, "HKG": 4, "KOR": 4,
}

# EMS料金テーブル: { ゾーン: { 重量kg(上限): 料金JPY } }
EMS_RATES: dict[int, list[tuple[float, int]]] = {
    1: [(0.5, 1400), (1.0, 1800), (1.5, 2200), (2.0, 2600), (3.0, 3200), (4.0, 3800), (5.0, 4400)],
    2: [(0.5, 1600), (1.0, 2100), (1.5, 2600), (2.0, 3100), (3.0, 3900), (4.0, 4700), (5.0, 5500)],
    3: [(0.5, 1700), (1.0, 2250), (1.5, 2800), (2.0, 3350), (3.0, 4200), (4.0, 5050), (5.0, 5900)],
    4: [(0.5, 1550), (1.0, 2050), (1.5, 2550), (2.0, 3050), (3.0, 3800), (4.0, 4550), (5.0, 5300)],
}

EMS_DAYS: dict[int, str] = {1: "3〜5日", 2: "4〜6日", 3: "5〜8日", 4: "3〜5日"}

# DHL料金（概算、円）: { ゾーン: 基本料金+kg単価 }
DHL_BASE: dict[int, tuple[int, int]] = {
    1: (2200, 900),   # 欧州
    2: (2500, 1100),  # 北米
    3: (2600, 1200),  # 豪州
    4: (1800, 700),   # アジア
}

# FedEx料金（概算）
FEDEX_BASE: dict[int, tuple[int, int]] = {
    1: (2400, 950),
    2: (2700, 1150),
    3: (2800, 1250),
    4: (2000, 750),
}


def _get_ems_fee(weight_g: float, zone: int) -> int:
    weight_kg = weight_g / 1000
    rates = EMS_RATES.get(zone, EMS_RATES[2])
    for limit_kg, fee in rates:
        if weight_kg <= limit_kg:
            return fee
    # 5kg超: 追加料金
    extra_kg = weight_kg - 5.0
    extra_steps = int(extra_kg / 0.5) + 1
    return rates[-1][1] + extra_steps * 400


def _get_carrier_fee(weight_g: float, zone: int, base_table: dict) -> int:
    base, per_kg = base_table.get(zone, base_table[2])
    weight_kg = weight_g / 1000
    return int(base + per_kg * weight_kg)


def calculate_international_shipping(
    weight_g: float,
    country_code: str,
    carrier: IntlCarrier = IntlCarrier.EMS,
) -> IntlShippingResult:
    """
    国際送料を計算する。

    Args:
        weight_g: 重量（グラム）
        country_code: 仕向け国コード
        carrier: 配送業者

    Returns:
        IntlShippingResult
    """
    zone = EMS_ZONES.get(country_code, 2)

    if carrier == IntlCarrier.EMS:
        fee = _get_ems_fee(weight_g, zone)
        name = "EMS（国際スピード郵便）"
        days = EMS_DAYS.get(zone, "5〜10日")
    elif carrier == IntlCarrier.DHL:
        fee = _get_carrier_fee(weight_g, zone, DHL_BASE)
        name = "DHL"
        days = "2〜4日"
    else:
        fee = _get_carrier_fee(weight_g, zone, FEDEX_BASE)
        name = "FedEx"
        days = "2〜4日"

    return IntlShippingResult(
        carrier=carrier.value,
        carrier_name=name,
        country_code=country_code,
        zone=zone,
        weight_g=weight_g,
        fee_jpy=float(fee),
        estimated_days=days,
        note=f"Zone {zone}・{weight_g:.0f}g・目安料金",
    )


def get_all_intl_estimates(
    weight_g: float,
    country_code: str,
) -> list[IntlShippingResult]:
    """3社の国際送料をまとめて取得する"""
    return [
        calculate_international_shipping(weight_g, country_code, c)
        for c in IntlCarrier
    ]


if __name__ == "__main__":
    # 動作テスト
    r = calculate_domestic_shipping(300, 20, 15, 10)
    print(f"国内: {r.carrier_name} ¥{r.fee_jpy:.0f} ({r.note})")

    r2 = calculate_international_shipping(500, "USA", IntlCarrier.EMS)
    print(f"国際: {r2.carrier_name} → {r2.country_code} ¥{r2.fee_jpy:.0f} ({r2.estimated_days})")
