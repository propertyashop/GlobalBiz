"""
Shopee Mass Upload CSV エクスポーター（多国対応版）

対応国:
  - SGP (シンガポール): SGD
  - TWN (台湾): TWD
  - MYS (マレーシア): MYR
  - PHL (フィリピン): PHP

公式テンプレート仕様:
https://seller.shopee.sg/edu/article/8370

出力形式: UTF-8 BOM付き（Shopeeの要件）
"""

import csv
import io
import json as _json
import zipfile
from datetime import datetime
from typing import List, Optional, Dict, Tuple

# ── Shopee 対応国設定 ──────────────────────────────────────────────
SHOPEE_COUNTRY_CONFIG: Dict[str, Dict] = {
    "SGP": {
        "name":        "シンガポール",
        "currency":    "SGD",
        "symbol":      "S$",
        "rate_env":    "DEFAULT_EXCHANGE_RATE_SGD",
        "rate_default": 112.0,
        "price_field": "calc_selling_price_sgd",
        "portal_url":  "https://seller.shopee.sg/",
    },
    "TWN": {
        "name":        "台湾",
        "currency":    "TWD",
        "symbol":      "NT$",
        "rate_env":    "DEFAULT_EXCHANGE_RATE_TWD",
        "rate_default": 4.7,
        "price_field": "calc_selling_price_twd",
        "portal_url":  "https://seller.shopee.tw/",
    },
    "MYS": {
        "name":        "マレーシア",
        "currency":    "MYR",
        "symbol":      "RM",
        "rate_env":    "DEFAULT_EXCHANGE_RATE_MYR",
        "rate_default": 33.0,
        "price_field": "calc_selling_price_myr",
        "portal_url":  "https://seller.shopee.com.my/",
    },
    "PHL": {
        "name":        "フィリピン",
        "currency":    "PHP",
        "symbol":      "₱",
        "rate_env":    "DEFAULT_EXCHANGE_RATE_PHP",
        "rate_default": 2.7,
        "price_field": "calc_selling_price_php",
        "portal_url":  "https://seller.shopee.ph/",
    },
}

SHOPEE_COUNTRY_NAMES = {k: v["name"] for k, v in SHOPEE_COUNTRY_CONFIG.items()}

# ── Shopee カテゴリ辞書（SGP/MYS 基準 - 共通 ID） ──────────────
SHOPEE_CATEGORIES: Dict[str, int] = {
    # 家電・電子機器
    "Electronics > Audio":            100644,
    "Electronics > Cameras":          100628,
    "Electronics > Computer":         100629,
    "Electronics > Games":            100635,
    "Electronics > Mobile":           100636,
    "Electronics > TV / Video":       100649,
    "Electronics > Wearables":        100651,
    "Electronics > Other":            100644,
    # ファッション
    "Fashion > Men Clothes":          100630,
    "Fashion > Women Clothes":        100631,
    "Fashion > Kids Clothes":         100632,
    "Fashion > Bags":                 100633,
    "Fashion > Shoes":                100634,
    "Fashion > Accessories":          100590,
    # 生活・インテリア
    "Home & Living > Furniture":      100542,
    "Home & Living > Kitchen":        100543,
    "Home & Living > Bath":           100544,
    "Home & Living > Bedding":        100545,
    "Home & Living > Lighting":       100546,
    # 美容・健康
    "Health & Beauty > Skincare":     100508,
    "Health & Beauty > Makeup":       100509,
    "Health & Beauty > Hair Care":    100510,
    "Health & Beauty > Healthcare":   100512,
    "Health & Beauty > Vitamins":     100513,
    # 食品
    "Food & Beverages > Snacks":      100570,
    "Food & Beverages > Drinks":      100571,
    "Food & Beverages > Fresh":       100572,
    # スポーツ
    "Sports > Outdoor":               100563,
    "Sports > Equipment":             100564,
    "Sports > Fitness":               100565,
    # おもちゃ・ベビー
    "Toys & Kids > Toys":             100564,
    "Toys & Kids > Baby Care":        100560,
    "Toys & Kids > Baby Clothes":     100561,
    # 本・文具
    "Books & Media > Books":          100652,
    "Books & Media > Music":          100653,
    "Books & Media > Stationery":     100655,
    # 自動車
    "Automotive > Parts":             100657,
    "Automotive > Accessories":       100658,
    # その他
    "Others":                         100599,
}

CATEGORY_DEFAULT_MAP: Dict[str, int] = {
    "electronics": 100644,
    "clothing":    100631,
    "accessories": 100590,
    "toys":        100564,
    "food":        100570,
    "cosmetics":   100508,
    "health":      100512,
    "sports":      100563,
    "home":        100542,
    "books":       100652,
    "auto":        100657,
    "other":       100599,
}

# Shopee Mass Upload カラム定義（国別推奨価格カラムは動的に付加）
SHOPEE_COLUMNS_BASE = [
    "Product Name",
    "Category ID",
    "Variation Name 1",
    "Variation Value 1",
    "Variation Name 2",
    "Variation Value 2",
    "Parent SKU",
    "SKU Reference",
    "Price",
    "Stock",
    "Weight (kg)",
    "Package Length (cm)",
    "Package Width (cm)",
    "Package Height (cm)",
    "Image 1",
    "Image 2",
    "Image 3",
    "Image 4",
    "Image 5",
    "Image 6",
    "Image 7",
    "Image 8",
    "Image 9",
    "Condition",
    "Product Description",
    "Brand",
    "Pre-Order",
    "Days to Ship",
    "Shopee Category ID (Internal)",
    "Source Site",
    "JAN Code",
    "UPC Code",
    "Cost Price (JPY)",
]


def _get_image_urls(product) -> List[str]:
    """商品から画像URLリストを取得する（最大9枚）"""
    urls: List[str] = []
    if product.image_urls:
        if isinstance(product.image_urls, list):
            urls = [u for u in product.image_urls if u]
        elif isinstance(product.image_urls, str):
            try:
                urls = _json.loads(product.image_urls) or []
            except Exception:
                urls = [product.image_urls]
    if product.image_url and product.image_url not in urls:
        urls.insert(0, product.image_url)
    return urls[:9]


def _resolve_shopee_category_id(product) -> int:
    if product.shopee_category_id:
        return int(product.shopee_category_id)
    if product.product_category:
        cat_val = (product.product_category.value
                   if hasattr(product.product_category, "value")
                   else str(product.product_category))
        return CATEGORY_DEFAULT_MAP.get(cat_val, 100599)
    return 100599


def _get_price_for_country(product, country_code: str, rate: float) -> float:
    """国別の推奨価格を返す（フォールバック: 仕入れ値 × 1.3 / レート）"""
    cfg = SHOPEE_COUNTRY_CONFIG.get(country_code, {})
    field = cfg.get("price_field", "")
    price = getattr(product, field, None) if field else None

    if not price and product.cost_price:
        price = round(product.cost_price * 1.3 / rate, 2)
    return round(float(price or 0), 2)


def product_to_shopee_row(
    product,
    country_code: str = "SGP",
    rate: float = 112.0,
) -> dict:
    """
    Product → Shopee Mass Upload の1行に変換する。

    Args:
        product: Product インスタンス
        country_code: 仕向け国コード（SGP/TWN/MYS/PHL）
        rate: その国の JPY→現地通貨レート
    """
    cfg = SHOPEE_COUNTRY_CONFIG.get(country_code, SHOPEE_COUNTRY_CONFIG["SGP"])
    currency = cfg["currency"]

    name_en = product.product_name_en or product.name
    desc_en = product.product_description_en or product.description or name_en
    price   = _get_price_for_country(product, country_code, rate)
    stock   = int(product.current_stock or 0)
    weight_kg = round((product.weight_g or 500) / 1000, 3)
    size_l  = product.size_cm_l or 20
    size_w  = product.size_cm_w or 15
    size_h  = product.size_cm_h or 10
    images  = _get_image_urls(product)
    image_dict = {f"Image {i+1}": (images[i] if i < len(images) else "") for i in range(9)}
    cat_id  = _resolve_shopee_category_id(product)
    condition = getattr(product, "condition", "New") or "New"
    source  = str(product.source_site.value
                  if hasattr(product.source_site, "value")
                  else product.source_site)

    row = {
        "Product Name":              name_en[:120],
        "Category ID":               cat_id,
        "Variation Name 1":          "",
        "Variation Value 1":         "",
        "Variation Name 2":          "",
        "Variation Value 2":         "",
        "Parent SKU":                product.sku,
        "SKU Reference":             product.sku,
        "Price":                     f"{price:.2f}",
        "Stock":                     stock,
        "Weight (kg)":               weight_kg,
        "Package Length (cm)":       int(size_l),
        "Package Width (cm)":        int(size_w),
        "Package Height (cm)":       int(size_h),
        "Condition":                 condition,
        "Product Description":       desc_en[:3000],
        "Brand":                     "",
        "Pre-Order":                 "No",
        "Days to Ship":              3,
        "Shopee Category ID (Internal)": cat_id,
        "Source Site":               source,
        "JAN Code":                  product.jan_code or "",
        "UPC Code":                  product.upc_code or "",
        "Cost Price (JPY)":          int(product.cost_price or 0),
        f"Recommended Price ({currency})": f"{price:.2f}",
        **image_dict,
    }
    return row


def _build_columns(currency: str) -> List[str]:
    return SHOPEE_COLUMNS_BASE + [f"Recommended Price ({currency})"]


def export_shopee_csv(
    products: list,
    country_code: str = "SGP",
    rate: float = 112.0,
    # 後方互換: sgd_rate
    sgd_rate: Optional[float] = None,
) -> bytes:
    """
    商品リストを Shopee Mass Upload CSV に変換してバイト列を返す。

    Args:
        products: Product リスト
        country_code: 仕向け国コード（SGP/TWN/MYS/PHL）
        rate: JPY→現地通貨レート
        sgd_rate: 後方互換パラメータ（SGP のみ, 廃止予定）

    Returns:
        UTF-8 BOM付き CSV バイト列
    """
    # 後方互換
    if sgd_rate is not None and country_code == "SGP":
        rate = sgd_rate

    cfg     = SHOPEE_COUNTRY_CONFIG.get(country_code, SHOPEE_COUNTRY_CONFIG["SGP"])
    columns = _build_columns(cfg["currency"])

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=columns,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for product in products:
        writer.writerow(product_to_shopee_row(product, country_code, rate))

    return ("\ufeff" + output.getvalue()).encode("utf-8")


def export_shopee_csv_all_countries(
    products: list,
    rates: Optional[Dict[str, float]] = None,
) -> Dict[str, bytes]:
    """
    全 Shopee 対応国（SGP/TWN/MYS/PHL）分の CSV をまとめて返す。

    Args:
        products: Product リスト
        rates: {country_code: rate} 辞書（None の場合はデフォルト値を使用）

    Returns:
        {country_code: CSV bytes} 辞書
    """
    if rates is None:
        rates = {}

    result: Dict[str, bytes] = {}
    for code, cfg in SHOPEE_COUNTRY_CONFIG.items():
        r = rates.get(code, cfg["rate_default"])
        result[code] = export_shopee_csv(products, country_code=code, rate=r)
    return result


def export_shopee_zip(
    products: list,
    country_codes: Optional[List[str]] = None,
    rates: Optional[Dict[str, float]] = None,
) -> bytes:
    """
    指定国分の Shopee CSV を ZIP にまとめて返す。

    Args:
        products: Product リスト
        country_codes: 出力する国コードリスト（None = 全4国）
        rates: {country_code: rate}（None = デフォルト）

    Returns:
        ZIP バイト列（Streamlit の download_button に渡せる）
    """
    if country_codes is None:
        country_codes = list(SHOPEE_COUNTRY_CONFIG.keys())
    if rates is None:
        rates = {}

    date_str = datetime.now().strftime("%Y%m%d")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for code in country_codes:
            if code not in SHOPEE_COUNTRY_CONFIG:
                continue
            cfg = SHOPEE_COUNTRY_CONFIG[code]
            r   = rates.get(code, cfg["rate_default"])
            csv_bytes = export_shopee_csv(products, country_code=code, rate=r)
            fname = f"shopee_{code}_{date_str}.csv"
            zf.writestr(fname, csv_bytes)
    return buf.getvalue()


def get_shopee_category_options() -> Dict[str, int]:
    """フォーム用カテゴリ選択肢を返す"""
    return SHOPEE_CATEGORIES


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from backend.db.database import get_session
    from backend.db.models import Product

    with get_session() as s:
        products = s.query(Product).all()

    print(f"商品数: {len(products)} 件")
    for code in SHOPEE_COUNTRY_CONFIG:
        cfg   = SHOPEE_COUNTRY_CONFIG[code]
        data  = export_shopee_csv(products, country_code=code, rate=cfg["rate_default"])
        fname = f"shopee_{code}_{datetime.now().strftime('%Y%m%d')}.csv"
        Path(fname).write_bytes(data)
        print(f"  {cfg['name']} ({cfg['currency']}): {fname} — {len(data):,} bytes")

    zip_data = export_shopee_zip(products)
    zip_name = f"shopee_all_{datetime.now().strftime('%Y%m%d')}.zip"
    Path(zip_name).write_bytes(zip_data)
    print(f"\nZIP: {zip_name} — {len(zip_data):,} bytes")
