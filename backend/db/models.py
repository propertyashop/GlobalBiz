from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text, JSON
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class SourceSite(str, PyEnum):
    AMAZON = "amazon"
    RAKUTEN = "rakuten"
    YAHOO = "yahoo"
    NETSEA = "netsea"
    MANUAL = "manual"


class ProductStatus(str, PyEnum):
    ACTIVE = "active"
    OUT_OF_STOCK = "out_of_stock"
    DISCONTINUED = "discontinued"
    DRAFT = "draft"


class ProductCategory(str, PyEnum):
    ELECTRONICS = "electronics"       # 家電・電子機器
    CLOTHING = "clothing"             # 衣類・アパレル
    ACCESSORIES = "accessories"       # アクセサリー・装飾品
    TOYS = "toys"                     # おもちゃ・ゲーム
    FOOD = "food"                     # 食品・飲料
    COSMETICS = "cosmetics"           # 化粧品・美容
    HEALTH = "health"                 # 健康・医療
    SPORTS = "sports"                 # スポーツ・アウトドア
    HOME = "home"                     # 家具・インテリア
    BOOKS = "books"                   # 書籍・メディア
    AUTO = "auto"                     # 自動車・バイク
    OTHER = "other"                   # その他


class SizeClass(str, PyEnum):
    SMALL = "small"       # 60サイズ以下
    MEDIUM = "medium"     # 80サイズ
    LARGE = "large"       # 100サイズ
    XL = "xl"             # 120サイズ以上


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    sku = Column(String(100), unique=True, nullable=False)

    # ===== 仕入れ元情報 =====
    source_site = Column(Enum(SourceSite), nullable=False, default=SourceSite.MANUAL, index=True)
    source_url = Column(Text, nullable=True)
    source_item_id = Column(String(200), nullable=True)  # 旧汎用フィールド（後方互換）

    # ===== 商品識別番号 =====
    asin = Column(String(20), nullable=True, index=True)          # Amazon ASIN (B0BDHWDR12)
    rakuten_item_code = Column(String(200), nullable=True)         # 楽天商品コード
    yahoo_item_code = Column(String(200), nullable=True)           # Yahoo商品コード
    netsea_product_id = Column(String(100), nullable=True)         # NETSEA商品ID
    jan_code = Column(String(13), nullable=True, index=True)       # JANコード 13桁
    upc_code = Column(String(12), nullable=True, index=True)       # UPCコード 12桁

    # ===== 価格情報 =====
    cost_price = Column(Float, nullable=False, default=0.0)        # 仕入れ値（円）
    selling_price_jpy = Column(Float, nullable=True)               # 販売価格（円換算）
    selling_price_usd = Column(Float, nullable=True)               # eBay用（USD）
    selling_price_sgd = Column(Float, nullable=True)               # Shopee用（SGD）
    markup_rate = Column(Float, nullable=False, default=1.3)       # 掛け率

    # ===== 利益計算 =====
    domestic_shipping_cost = Column(Float, nullable=True)          # 国内送料（円）
    intl_shipping_cost = Column(Float, nullable=True)              # 国際送料（円）
    tariff_amount = Column(Float, nullable=True)                   # 関税額（円）
    ebay_fee_rate = Column(Float, nullable=False, default=0.13)    # eBay手数料率
    shopee_fee_rate = Column(Float, nullable=False, default=0.06)  # Shopee手数料率
    payment_fee_rate = Column(Float, nullable=False, default=0.044) # 決済手数料率
    target_profit_rate = Column(Float, nullable=True)              # 希望利益率
    calc_selling_price_usd = Column(Float, nullable=True)          # 計算済み推奨価格(USD)
    calc_selling_price_sgd = Column(Float, nullable=True)          # 計算済み推奨価格(SGD)
    calc_selling_price_twd = Column(Float, nullable=True)          # 計算済み推奨価格(TWD 台湾ドル)
    calc_selling_price_myr = Column(Float, nullable=True)          # 計算済み推奨価格(MYR マレーシアリンギット)
    calc_selling_price_php = Column(Float, nullable=True)          # 計算済み推奨価格(PHP フィリピンペソ)

    # ===== 在庫情報 =====
    current_stock = Column(Integer, nullable=False, default=0)
    min_stock_alert = Column(Integer, nullable=False, default=1)
    last_checked_at = Column(DateTime, nullable=True)

    # ===== 販売先 =====
    target_ebay = Column(Boolean, nullable=False, default=False)
    target_shopee = Column(Boolean, nullable=False, default=False)
    target_countries = Column(JSON, nullable=True)   # ["USA","SGP","MYS"] など
    ebay_listing_id = Column(String(100), nullable=True)
    shopee_item_id = Column(String(100), nullable=True)

    # ===== 多言語・出品用フィールド =====
    product_name_en = Column(String(500), nullable=True)           # 英語商品名（eBay/Shopee掲載用）
    product_description_en = Column(Text, nullable=True)           # 英語商品説明
    shopee_category_id = Column(Integer, nullable=True)            # Shopee カテゴリID
    image_urls = Column(JSON, nullable=True)                       # 画像URLリスト（最大9枚）
    condition = Column(String(20), nullable=False, default="New")  # New / Used

    # ===== 商品詳細 =====
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)                        # メイン画像URL（後方互換）
    category = Column(String(200), nullable=True)                  # 旧テキスト（後方互換）
    product_category = Column(
        Enum(ProductCategory), nullable=True, default=ProductCategory.OTHER
    )
    weight_g = Column(Float, nullable=True)
    size_class = Column(Enum(SizeClass), nullable=True)
    size_cm_l = Column(Float, nullable=True)   # 縦(cm)
    size_cm_w = Column(Float, nullable=True)   # 横(cm)
    size_cm_h = Column(Float, nullable=True)   # 高(cm)
    hs_code = Column(String(20), nullable=True)

    # ===== メタ =====
    status = Column(Enum(ProductStatus), nullable=False, default=ProductStatus.DRAFT, index=True)
    tags = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Product id={self.id} sku={self.sku} name={self.name[:30]}>"


# ══════════════════════════════════════════════════════════════════
#  注文関連 Enum
# ══════════════════════════════════════════════════════════════════

class OrderStatus(str, PyEnum):
    PENDING   = "pending"    # 処理中（未発送）
    ORDERED   = "ordered"    # 仕入れ発注済み
    SHIPPED   = "shipped"    # 発送済み
    DELIVERED = "delivered"  # 配達完了
    CANCELLED = "cancelled"  # キャンセル


class PurchaseStatus(str, PyEnum):
    NOT_ORDERED = "not_ordered"  # 未発注
    ORDERED     = "ordered"      # 発注済み
    RECEIVED    = "received"     # 入荷済み


class LogisticsProvider(str, PyEnum):
    FEDEX    = "fedex"
    ELOGICOM = "elogicom"
    SEAPASS  = "seapass"
    EMS      = "ems"       # 日本郵便 EMS（手動）
    MANUAL   = "manual"    # 自己発送


# ══════════════════════════════════════════════════════════════════
#  Order モデル
# ══════════════════════════════════════════════════════════════════

class Order(Base):
    __tablename__ = "orders"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    order_id   = Column(String(200), nullable=False, unique=True, index=True)  # eBay/Shopee注文ID
    platform   = Column(String(20),  nullable=False)                           # "ebay" / "shopee"

    # ── 購入者・配送先 ──
    buyer_name      = Column(String(200), nullable=True)
    buyer_email     = Column(String(200), nullable=True)
    shipping_name   = Column(String(200), nullable=True)
    shipping_address= Column(Text,        nullable=True)
    shipping_city   = Column(String(100), nullable=True)
    shipping_state  = Column(String(100), nullable=True)
    shipping_postal = Column(String(20),  nullable=True)
    shipping_country= Column(String(10),  nullable=True)  # 2文字 ISO / 3文字

    # ── 商品情報（スナップショット）──
    product_id   = Column(Integer, ForeignKey("products.id"), nullable=True)
    product_name = Column(String(500), nullable=True)   # 注文時点の商品名
    sku          = Column(String(100), nullable=True)
    quantity     = Column(Integer, nullable=False, default=1)

    # ── 価格 ──
    sale_price      = Column(Float, nullable=True)   # 販売価格（現地通貨）
    sale_currency   = Column(String(10), nullable=True)  # "USD" / "SGD" など
    sale_price_jpy  = Column(Float, nullable=True)   # 円換算
    purchase_price  = Column(Float, nullable=True)   # 仕入れ価格（円）
    shipping_fee_jpy= Column(Float, nullable=True)   # 国内送料
    profit_jpy      = Column(Float, nullable=True)   # 利益（円）

    # ── 仕入れ ──
    purchase_status = Column(
        Enum(PurchaseStatus), nullable=False, default=PurchaseStatus.NOT_ORDERED
    )
    purchase_url    = Column(Text, nullable=True)    # Amazon/楽天など仕入れURL
    purchase_note   = Column(Text, nullable=True)

    # ── 物流 ──
    logistics_provider = Column(Enum(LogisticsProvider), nullable=True)
    tracking_number    = Column(String(200), nullable=True, index=True)
    carrier_name       = Column(String(100), nullable=True)  # "FedEx" / "EMS" など
    label_url          = Column(Text, nullable=True)         # 送り状 PDF URL

    # ── ステータス ──
    status       = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING)
    ordered_at   = Column(DateTime, nullable=True)   # eBay/Shopee 注文日時
    shipped_at   = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    # ── メタ ──
    notes      = Column(Text,     nullable=True)
    raw_data   = Column(JSON,     nullable=True)   # 元CSVデータ（参照用）
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # ── リレーション ──
    product = relationship("Product", foreign_keys=[product_id])

    def __repr__(self) -> str:
        return f"<Order id={self.id} order_id={self.order_id} status={self.status}>"
