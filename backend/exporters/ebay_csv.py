"""
eBay File Exchange CSV エクスポーター

eBay File Exchange フォーマット（Flat File）
公式仕様: https://developer.ebay.com/devzone/file-exchange/docs/

対応アクション: Add / Revise / End
"""

import csv
import io
from datetime import datetime
from typing import List, Optional

# ── eBay カテゴリ ID マッピング ─────────────────────────────────────
EBAY_CATEGORIES: dict[str, str] = {
    "Electronics > Audio (Headphones)":      "112529",
    "Electronics > Audio (Speakers)":        "14990",
    "Electronics > Cameras":                 "31388",
    "Electronics > Computer Components":     "175673",
    "Electronics > Mobile Phones":           "9355",
    "Electronics > Tablets":                 "171485",
    "Electronics > TV & Video":              "32852",
    "Electronics > Video Games":             "139973",
    "Electronics > Wearables":               "178893",
    "Electronics > Other":                   "293",
    "Clothing > Men":                        "1059",
    "Clothing > Women":                      "15724",
    "Clothing > Kids":                       "171146",
    "Shoes":                                 "63889",
    "Jewelry & Watches > Watches":           "31387",
    "Jewelry & Watches > Jewelry":           "281",
    "Toys & Hobbies > Action Figures":       "246",
    "Toys & Hobbies > Board Games":          "180345",
    "Toys & Hobbies > LEGO":                 "19006",
    "Sporting Goods > Fitness":              "15273",
    "Sporting Goods > Cycling":              "7294",
    "Sporting Goods > Golf":                 "1513",
    "Home & Garden > Furniture":             "3197",
    "Home & Garden > Kitchen":               "20625",
    "Home & Garden > Bedding":               "20444",
    "Health & Beauty > Skincare":            "11838",
    "Health & Beauty > Vitamins":            "67",
    "Books > Fiction":                       "29792",
    "Books > Non-Fiction":                   "29792",
    "Collectibles > Japanese":               "10055",
    "Everything Else":                       "99",
}

# product_category（内部値）→ eBay CategoryID のデフォルトマッピング
CATEGORY_DEFAULT_MAP: dict[str, str] = {
    "electronics": "293",
    "clothing":    "11450",
    "accessories": "14223",
    "toys":        "220",
    "food":        "14308",
    "cosmetics":   "26395",
    "health":      "26395",
    "sports":      "888",
    "home":        "11700",
    "books":       "267",
    "auto":        "6000",
    "other":       "99",
}

# ConditionID マッピング
CONDITION_MAP: dict[str, int] = {
    "New":              1000,
    "New (Open Box)":   1500,
    "Manufacturer Refurbished": 2000,
    "Seller Refurbished": 2500,
    "Like New":         3000,
    "Very Good":        4000,
    "Good":             5000,
    "Acceptable":       6000,
    "For Parts":        7000,
}

# eBay File Exchange のカラム定義
EBAY_COLUMNS = [
    "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
    "Category",
    "Title",
    "ConditionID",
    "Description",
    "PicURL",
    "GalleryType",
    "ListingType",
    "StartPrice",
    "BuyItNowPrice",
    "Duration",
    "Quantity",
    "Location",
    "ShipToLocations",
    "ShippingType",
    "ShippingService-1:Option",
    "ShippingService-1:Cost",
    "IntlShippingService-1:Option",
    "IntlShippingService-1:Cost",
    "IntlShippingService-1:Locations",
    "ReturnsAcceptedOption",
    "RefundOption",
    "ReturnsWithinOption",
    "ShippingCostPaidByOption",
    "CustomLabel",
    "UPC",
    "Brand",
    "MPN",
    # 内部管理用（eBay にはアップロードしない）
    "SKU",
    "Cost Price (JPY)",
    "Recommended USD",
    "Source Site",
    "JAN Code",
]


def _get_main_image(product) -> str:
    """メイン画像 URL を返す"""
    if product.image_urls:
        imgs = product.image_urls if isinstance(product.image_urls, list) else []
        if imgs:
            return imgs[0]
    return product.image_url or ""


def _get_all_pic_urls(product) -> str:
    """全画像 URL を「|」区切りで返す（eBay 形式）"""
    urls = []
    if product.image_urls and isinstance(product.image_urls, list):
        urls = [u for u in product.image_urls if u]
    if product.image_url and product.image_url not in urls:
        urls.insert(0, product.image_url)
    return "|".join(urls[:12])  # eBay は最大12枚


def _resolve_category(product) -> str:
    if product.product_category:
        cat_val = product.product_category.value if hasattr(product.product_category, "value") else str(product.product_category)
        return CATEGORY_DEFAULT_MAP.get(cat_val, "99")
    return "99"


def _build_description(product) -> str:
    """eBay 用 HTML 説明文を生成する"""
    name = product.product_name_en or product.name
    desc = product.product_description_en or product.description or ""
    jan  = f"<p><b>JAN:</b> {product.jan_code}</p>" if product.jan_code else ""
    upc  = f"<p><b>UPC:</b> {product.upc_code}</p>" if product.upc_code else ""
    asin = f"<p><b>ASIN:</b> {product.asin}</p>" if product.asin else ""
    weight = f"<p><b>Weight:</b> {product.weight_g:.0f}g</p>" if product.weight_g else ""

    html = f"""<div style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;">
<h2>{name}</h2>
<p>{desc}</p>
{jan}{upc}{asin}{weight}
<hr/>
<p style="font-size:12px;color:#666;">
Shipped from Japan. Please allow 7-14 business days for international delivery.
</p>
</div>"""
    return html


def product_to_ebay_row(
    product,
    action: str = "Add",
    usd_rate: float = 150.0,
) -> dict:
    """
    Product モデルを eBay File Exchange の1行に変換する。

    Args:
        product: backend.db.models.Product インスタンス
        action: "Add" / "Revise" / "End"
        usd_rate: JPY→USD レート（推奨価格が未設定の場合のフォールバック）
    """
    # 価格
    price_usd = product.calc_selling_price_usd or product.selling_price_usd
    if not price_usd and product.cost_price:
        price_usd = round(product.cost_price * 1.3 / usd_rate, 2)
    price_usd = round(float(price_usd or 0), 2)

    # タイトル（80文字制限）
    title = (product.product_name_en or product.name)[:80]

    # カテゴリ
    cat_id = _resolve_category(product)

    # コンディション
    condition_str = getattr(product, "condition", "New") or "New"
    condition_id = CONDITION_MAP.get(condition_str, 1000)

    # 画像
    pic_urls = _get_all_pic_urls(product)

    # 説明
    description = _build_description(product)

    row = {
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)": action,
        "Category":                    cat_id,
        "Title":                       title,
        "ConditionID":                 condition_id,
        "Description":                 description,
        "PicURL":                      pic_urls,
        "GalleryType":                 "Gallery",
        "ListingType":                 "FixedPriceItem",
        "StartPrice":                  f"{price_usd:.2f}",
        "BuyItNowPrice":               f"{price_usd:.2f}",
        "Duration":                    "GTC",  # Good 'Til Cancelled
        "Quantity":                    int(product.current_stock or 0),
        "Location":                    "Japan",
        "ShipToLocations":             "Worldwide",
        "ShippingType":                "Flat",
        "ShippingService-1:Option":    "FlatRateFrFlatRateShipping",
        "ShippingService-1:Cost":      "0.00",
        "IntlShippingService-1:Option":"StandardIntl",
        "IntlShippingService-1:Cost":  "0.00",
        "IntlShippingService-1:Locations": "Worldwide",
        "ReturnsAcceptedOption":       "ReturnsAccepted",
        "RefundOption":                "MoneyBack",
        "ReturnsWithinOption":         "Days_30",
        "ShippingCostPaidByOption":    "Buyer",
        "CustomLabel":                 product.sku,
        "UPC":                         product.upc_code or "Does Not Apply",
        "Brand":                       "",
        "MPN":                         "",
        # 内部管理用
        "SKU":                         product.sku,
        "Cost Price (JPY)":            int(product.cost_price or 0),
        "Recommended USD":             f"{price_usd:.2f}",
        "Source Site":                 str(product.source_site.value if hasattr(product.source_site, "value") else product.source_site),
        "JAN Code":                    product.jan_code or "",
    }
    return row


def export_ebay_csv(
    products: list,
    action: str = "Add",
    usd_rate: float = 150.0,
) -> bytes:
    """
    商品リストを eBay File Exchange CSV に変換してバイト列を返す。

    Returns:
        UTF-8 BOM付き CSV バイト列
    """
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=EBAY_COLUMNS,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()

    for product in products:
        row = product_to_ebay_row(product, action=action, usd_rate=usd_rate)
        writer.writerow(row)

    return ("\ufeff" + output.getvalue()).encode("utf-8")


def get_ebay_category_options() -> dict[str, str]:
    """フォーム用カテゴリ選択肢を返す"""
    return EBAY_CATEGORIES


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from backend.db.database import get_session
    from backend.db.models import Product

    with get_session() as s:
        products = s.query(Product).all()

    csv_bytes = export_ebay_csv(products)
    filename = f"ebay_products_{datetime.now().strftime('%Y%m%d')}.csv"
    Path(filename).write_bytes(csv_bytes)
    print(f"出力: {filename}（{len(products)}件, {len(csv_bytes):,} bytes）")
    lines = csv_bytes.decode("utf-8-sig").split("\r\n")
    for i, line in enumerate(lines[:3]):
        print(f"  行{i}: {line[:120]}...")
