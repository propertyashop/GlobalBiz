"""
関税・輸入税計算モジュール

GlobalBiz の利益計算プレビューに使用。
HSコード × 販売先国 から関税率を引き、輸入消費税(VAT/GST)、
De Minimis(少額免税枠) を加味して総コストを算出する。

マスタ: frontend/data/tariff_master.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# ============================================================
# 3文字ISOコード → 2文字コード変換（後方互換）
# ============================================================

_ISO3_TO_2: dict[str, str] = {
    "USA": "US",
    "SGP": "SG",
    "TWN": "TW",
    "MYS": "MY",
    "PHL": "PH",
}


def _normalize_country(code: str) -> str:
    """3文字または2文字のISOコードを2文字に正規化する。"""
    return _ISO3_TO_2.get(code.upper(), code.upper())


# ============================================================
# マスタ読み込み
# ============================================================

_MASTER_CACHE: Optional[dict] = None


def _master_path() -> Path:
    """tariff_master.json の絶対パスを返す。
    backend/calculators/ → backend/ → project_root/ → frontend/data/
    """
    return (
        Path(__file__).resolve().parent.parent.parent
        / "frontend"
        / "data"
        / "tariff_master.json"
    )


def load_master(force_reload: bool = False) -> dict:
    """関税マスタを読み込む（キャッシュあり）。"""
    global _MASTER_CACHE
    if _MASTER_CACHE is None or force_reload:
        path = _master_path()
        if not path.exists():
            raise FileNotFoundError(
                f"関税マスタが見つかりません: {path}\n"
                "frontend/data/tariff_master.json を作成してください。"
            )
        with open(path, "r", encoding="utf-8") as f:
            _MASTER_CACHE = json.load(f)
    return _MASTER_CACHE


def get_country_list() -> list[tuple[str, str]]:
    """[(国コード, 表示名), ...] を返す。UI のセレクト用。"""
    master = load_master()
    return [(code, info["name"]) for code, info in master["countries"].items()]


def get_country_info(country_code: str) -> dict:
    """国コード（2文字または3文字）から国情報dictを返す。"""
    master = load_master()
    code2 = _normalize_country(country_code)
    if code2 not in master["countries"]:
        raise ValueError(f"未対応の国コード: {country_code} (正規化後: {code2})")
    return master["countries"][code2]


def get_exchange_rate(currency: str) -> float:
    """通貨→JPYのレートを返す。"""
    master = load_master()
    rates = master["_meta"]["exchange_rate_jpy"]
    return rates.get(currency, 1.0)


def is_supported_country(country_code: str) -> bool:
    """tariff_master.json で対応済みの国コードかを返す。"""
    try:
        master = load_master()
        code2 = _normalize_country(country_code)
        return code2 in master["countries"]
    except Exception:
        return False


# ============================================================
# 関税率の解決
# ============================================================

def resolve_duty_rate(country_code: str, hs_code: Optional[str]) -> float:
    """
    販売先国とHSコードから関税率を返す。
    HSコードが hs_overrides にあればそれを優先、なければ default_duty_rate。
    """
    info = get_country_info(country_code)
    overrides = info.get("hs_overrides", {})

    if hs_code:
        # 完全一致
        if hs_code in overrides:
            return overrides[hs_code]
        # 上位HS（先頭4桁）でフォールバック
        hs_prefix = hs_code.split(".")[0] if "." in hs_code else hs_code[:4]
        for key, rate in overrides.items():
            if key.startswith(hs_prefix):
                return rate

    return info["default_duty_rate"]


# ============================================================
# 計算結果のデータクラス
# ============================================================

@dataclass
class TariffBreakdown:
    """関税・税金の内訳。すべて円建て。"""

    # 入力
    cost_jpy: float = 0.0             # 仕入れ値
    shipping_jpy: float = 0.0         # 国際送料
    sale_price_jpy: float = 0.0       # 販売価格(円換算)
    country_code: str = ""
    country_name: str = ""
    hs_code: Optional[str] = None

    # 関税計算
    cif_value_jpy: float = 0.0        # CIF価額（仕入+送料）
    cif_value_local: float = 0.0      # 現地通貨建てCIF
    local_currency: str = ""          # 現地通貨コード
    de_minimis_local: Optional[float] = None
    is_under_de_minimis: bool = False

    duty_rate: float = 0.0
    duty_jpy: float = 0.0             # 関税額

    vat_rate: float = 0.0
    vat_name: str = ""
    vat_jpy: float = 0.0              # 輸入消費税

    # プラットフォーム手数料
    platform: str = ""                # "eBay" or "Shopee"
    platform_fee_rate: float = 0.0
    platform_fee_jpy: float = 0.0

    # 関税負担方式
    duty_bearer: str = "DDP"          # "DDP"=セラー負担 / "DDU"=バイヤー負担

    # 利益
    total_cost_jpy: float = 0.0       # セラー負担総コスト
    profit_jpy: float = 0.0
    profit_rate: float = 0.0
    buyer_burden_jpy: float = 0.0     # DDU時のバイヤー負担(参考表示)

    # 注記
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ============================================================
# メイン計算ロジック
# ============================================================

def calculate(
    *,
    cost_jpy: float,
    sale_price_jpy: float,
    country_code: str,
    hs_code: Optional[str] = None,
    shipping_jpy: float = 0.0,
    platform: str = "ebay",       # "ebay" or "shopee"
    duty_bearer: str = "DDP",     # "DDP" or "DDU"
) -> TariffBreakdown:
    """
    関税・税金・手数料を含めた利益計算を実行。

    Parameters
    ----------
    cost_jpy : float
        仕入れ値（円）
    sale_price_jpy : float
        販売価格を日本円換算した値
    country_code : str
        販売先国コード（"US"/"USA", "SG"/"SGP", "TW"/"TWN", "MY"/"MYS", "PH"/"PHL"）
    hs_code : str | None
        HSコード（"8517.13" 形式）
    shipping_jpy : float
        国際送料（円）
    platform : str
        "ebay" または "shopee"
    duty_bearer : str
        "DDP"（セラー負担）or "DDU"（バイヤー負担）

    Returns
    -------
    TariffBreakdown
    """
    master = load_master()
    info = get_country_info(country_code)
    code2 = _normalize_country(country_code)

    bd = TariffBreakdown(
        cost_jpy=cost_jpy,
        shipping_jpy=shipping_jpy,
        sale_price_jpy=sale_price_jpy,
        country_code=code2,
        country_name=info["name"],
        hs_code=hs_code,
        duty_bearer=duty_bearer,
        local_currency=info["currency"],
    )

    # ---- CIF価額（仕入＋送料）----
    bd.cif_value_jpy = cost_jpy + shipping_jpy
    fx_rate = get_exchange_rate(info["currency"])
    bd.cif_value_local = bd.cif_value_jpy / fx_rate if fx_rate else 0.0
    bd.de_minimis_local = info.get("de_minimis_local")

    # ---- De Minimis 判定 ----
    if bd.de_minimis_local is not None and bd.cif_value_local <= bd.de_minimis_local:
        bd.is_under_de_minimis = True
        bd.duty_rate = 0.0
        bd.duty_jpy = 0.0
        bd.vat_rate = 0.0
        bd.vat_jpy = 0.0
        bd.vat_name = info.get("vat_name", "")
        de_note = info.get("de_minimis_note", "")
        bd.note = (
            f"少額免税枠（{info['currency']} {bd.de_minimis_local:,.0f}）以下のため関税・輸入税ゼロ。"
            + (f" {de_note}" if de_note else "")
        )
    else:
        bd.is_under_de_minimis = False
        # ---- 関税 ----
        bd.duty_rate = resolve_duty_rate(code2, hs_code)
        bd.duty_jpy = bd.cif_value_jpy * bd.duty_rate

        # ---- 輸入消費税（VAT/GST等）----
        # VAT は通常 (CIF + 関税) に対して課税
        bd.vat_rate = info.get("vat_rate", 0.0)
        bd.vat_name = info.get("vat_name", "")
        bd.vat_jpy = (bd.cif_value_jpy + bd.duty_jpy) * bd.vat_rate

    # ---- プラットフォーム手数料 ----
    fees = master.get("platform_fees", {})
    if platform.lower() == "ebay":
        bd.platform = "eBay"
        bd.platform_fee_rate = fees.get("ebay", {}).get("rate", 0.13)
    elif platform.lower() == "shopee":
        bd.platform = "Shopee"
        shopee_fees = fees.get("shopee", {})
        bd.platform_fee_rate = shopee_fees.get(code2, {}).get("rate", 0.08)
    else:
        bd.platform = platform
        bd.platform_fee_rate = 0.0

    bd.platform_fee_jpy = sale_price_jpy * bd.platform_fee_rate

    # ---- 利益計算 ----
    if duty_bearer == "DDP":
        # セラーが関税・VATを負担
        bd.total_cost_jpy = (
            cost_jpy
            + shipping_jpy
            + bd.duty_jpy
            + bd.vat_jpy
            + bd.platform_fee_jpy
        )
        bd.buyer_burden_jpy = 0.0
    else:  # DDU
        # バイヤーが関税・VATを負担（セラー側原価には含めない）
        bd.total_cost_jpy = (
            cost_jpy
            + shipping_jpy
            + bd.platform_fee_jpy
        )
        bd.buyer_burden_jpy = bd.duty_jpy + bd.vat_jpy

    bd.profit_jpy = sale_price_jpy - bd.total_cost_jpy
    bd.profit_rate = (bd.profit_jpy / sale_price_jpy) if sale_price_jpy > 0 else 0.0

    return bd


# ============================================================
# UI 表示用ヘルパー
# ============================================================

def format_breakdown_lines(bd: TariffBreakdown) -> list[tuple[str, str]]:
    """
    利益プレビューの内訳を [(ラベル, 値), ...] で返す。
    Streamlit の表表示で使う。
    """
    exempt_suffix = "（免税）" if bd.is_under_de_minimis else ""

    lines: list[tuple[str, str]] = [
        ("販売価格",                     f"¥{bd.sale_price_jpy:,.0f}"),
        ("仕入れ値",                     f"¥{bd.cost_jpy:,.0f}"),
        ("国際送料",                     f"¥{bd.shipping_jpy:,.0f}"),
        (
            f"関税 ({bd.duty_rate * 100:.1f}%)",
            f"¥{bd.duty_jpy:,.0f}{exempt_suffix}",
        ),
        (
            f"{bd.vat_name or 'VAT'} ({bd.vat_rate * 100:.1f}%)",
            f"¥{bd.vat_jpy:,.0f}{exempt_suffix}",
        ),
        (
            f"{bd.platform}手数料 ({bd.platform_fee_rate * 100:.2f}%)",
            f"¥{bd.platform_fee_jpy:,.0f}",
        ),
        ("─────────────", "─────────"),
        (f"総コスト（{bd.duty_bearer}）",  f"¥{bd.total_cost_jpy:,.0f}"),
        ("純利益",                       f"¥{bd.profit_jpy:,.0f}"),
        ("利益率",                       f"{bd.profit_rate * 100:.1f}%"),
    ]
    if bd.duty_bearer == "DDU" and bd.buyer_burden_jpy > 0:
        lines.append(("バイヤー負担（参考）", f"¥{bd.buyer_burden_jpy:,.0f}"))
    return lines
