"""
Shopee Mass Upload CSV エクスポーター

公式テンプレート仕様:
https://seller.shopee.sg/edu/article/8370

出力形式: UTF-8 BOM付き（Shopeeの要件）
"""

import csv
import io
from datetime import datetime
from typing import List, Optional

# ── Shopee カテゴリ辞書（主要カテゴリ ID） ──────────────────────────
# https://open.shopee.com/documents  → Category API で取得可能
# ここでは SGP/MYS の主要カテゴリを手動で定義
SHOPEE_CATEGORIES: dict[str, int] = {
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

# product_category（内部値）→ Shopee カテゴリ ID のデフォルトマッピング
CATEGORY_DEFAULT_MAP: dict[str, int] = {
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

# Shopee Mass Upload のカラム定義（順序固定）
SHOPEE_COLUMNS = [
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
    "Recommended Price (SGD)",
]


def _get_image_urls(product) -> list:
    """商品から画像URLリストを取得する（最大9枚）"""
    urls = []
    # image_urls フィールド（JSON リスト）
    if product.image_urls:
        if isinstance(product.image_urls, list):
            urls = [u for u in product.image_urls if u]
        elif isinstance(product.image_urls, str):
            import json
            try:
                urls = json.loads(product.image_urls) or []
            except Exception:
                urls = [product.image_urls]
    # image_url（後方互換）が未追加なら先頭に追加
    if product.image_url and product.image_url not in urls:
        urls.insert(0, product.image_url)
    return urls[:9]


def _resolve_shopee_category_id(product) -> int:
    """商品から Shopee カテゴリ ID を解決する"""
    if product.shopee_category_id:
        return int(product.shopee_category_id)
    if product.product_category:
        cat_val = product.product_category.value if hasattr(product.product_category, "value") else str(product.product_category)
        return CATEGORY_DEFAULT_MAP.get(cat_val, 100599)
    return 100599  # Others


def product_to_shopee_row(product, sgd_rate: float = 112.0) -> dict:
    """
    Product モデルを Shopee Mass Upload の1行に変換する。

    Args:
        product: backend.db.models.Product インスタンス
        sgd_rate: JPY→SGD レート（推奨価格が未設定の場合のフォールバック）
    """
    # 商品名（英語優先）
    name_en = product.product_name_en or product.name

    # 説明（英語優先）
    desc_en = product.product_description_en or product.description or name_en

    # 価格
    price_sgd = product.calc_selling_price_sgd or product.selling_price_sgd
    if not price_sgd and product.cost_price:
        price_sgd = round(product.cost_price * 1.3 / sgd_rate, 2)
    price_sgd = round(float(price_sgd or 0), 2)

    # 在庫
    stock = int(product.current_stock or 0)

    # 重量
    weight_kg = round((product.weight_g or 500) / 1000, 3)

    # サイズ
    size_l = product.size_cm_l or 20
    size_w = product.size_cm_w or 15
    size_h = product.size_cm_h or 10

    # 画像
    images = _get_image_urls(product)
    image_dict = {f"Image {i+1}": (images[i] if i < len(images) else "") for i in range(9)}

    # カテゴリ ID
    cat_id = _resolve_shopee_category_id(product)

    # コンディション
    condition = getattr(product, "condition", "New") or "New"

    row = {
        "Product Name":              name_en[:120],
        "Category ID":               cat_id,
        "Variation Name 1":          "",
        "Variation Value 1":         "",
        "Variation Name 2":          "",
        "Variation Value 2":         "",
        "Parent SKU":                product.sku,
        "SKU Reference":             product.sku,
        "Price":                     f"{price_sgd:.2f}",
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
        "Source Site":               str(product.source_site.value if hasattr(product.source_site, "value") else product.source_site),
        "JAN Code":                  product.jan_code or "",
        "UPC Code":                  product.upc_code or "",
        "Cost Price (JPY)":          int(product.cost_price or 0),
        "Recommended Price (SGD)":   f"{price_sgd:.2f}",
        **image_dict,
    }
    return row


def export_shopee_csv(
    products: list,
    sgd_rate: float = 112.0,
) -> bytes:
    """
    商品リストを Shopee Mass Upload CSV に変換してバイト列を返す。

    Returns:
        UTF-8 BOM付き CSV バイト列（Streamlit の download_button に渡せる）
    """
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=SHOPEE_COLUMNS,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()

    for product in products:
        row = product_to_shopee_row(product, sgd_rate)
        writer.writerow(row)

    # UTF-8 BOM 付きで返す（Excel/Shopeeの文字化け防止）
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def get_shopee_category_options() -> dict[str, int]:
    """フォーム用カテゴリ選択肢を返す"""
    return SHOPEE_CATEGORIES


if __name__ == "__main__":
    # 動作テスト（DBから商品を取得して出力）
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from backend.db.database import get_session
    from backend.db.models import Product

    with get_session() as s:
        products = s.query(Product).all()

    csv_bytes = export_shopee_csv(products)
    filename = f"shopee_products_{datetime.now().strftime('%Y%m%d')}.csv"
    Path(filename).write_bytes(csv_bytes)
    print(f"出力: {filename}（{len(products)}件, {len(csv_bytes):,} bytes）")
    # 先頭2行を確認
    lines = csv_bytes.decode("utf-8-sig").split("\r\n")
    for i, line in enumerate(lines[:3]):
        print(f"  行{i}: {line[:100]}...")
