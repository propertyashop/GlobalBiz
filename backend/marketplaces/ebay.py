"""
eBay Trading API 連携モジュール

認証方式: Auth'n'Auth Token（EBAY_USER_TOKEN）
APIバージョン: 1193
エンドポイント: https://api.ebay.com/ws/api.dll

主要コール:
  GetUser           → 接続テスト・ユーザー情報取得
  AddItem           → 新規出品
  ReviseItem        → 価格・在庫更新
  EndItem           → 出品停止
  GetMyeBaySelling  → 出品中商品一覧
  GetItem           → 個別商品情報取得

公式ドキュメント:
  https://developer.ebay.com/DevZone/XML/docs/Reference/eBay/index.html
"""

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ── 認証情報 ──────────────────────────────────────────────────────
APP_ID     = os.getenv("EBAY_APP_ID", "")
DEV_ID     = os.getenv("EBAY_DEV_ID", "")
CERT_ID    = os.getenv("EBAY_CERT_ID", "")
USER_TOKEN = os.getenv("EBAY_USER_TOKEN", "")
SITE_ID    = os.getenv("EBAY_SITE_ID", "0")   # 0=US, 3=UK, 77=DE, 15=AU
SANDBOX    = os.getenv("EBAY_SANDBOX", "false").lower() == "true"

TRADING_ENDPOINT = (
    "https://api.sandbox.ebay.com/ws/api.dll"
    if SANDBOX
    else "https://api.ebay.com/ws/api.dll"
)
API_VERSION = "1193"
NS = "urn:ebay:apis:eBLBaseComponents"

# ── カテゴリマッピング（内部カテゴリ → eBay CategoryID） ───────────
CATEGORY_MAP: Dict[str, str] = {
    "electronics":  "293",     # Consumer Electronics
    "clothing":     "11450",   # Clothing, Shoes & Accessories
    "accessories":  "14223",   # Jewelry & Watches
    "toys":         "220",     # Toys & Hobbies
    "food":         "14308",   # Specialty Food
    "cosmetics":    "26395",   # Health & Beauty
    "health":       "26395",   # Health & Beauty
    "sports":       "888",     # Sporting Goods
    "home":         "11700",   # Home & Garden
    "books":        "267",     # Books
    "auto":         "6000",    # eBay Motors > Parts
    "other":        "99",      # Everything Else
}

# ConditionID マッピング
CONDITION_MAP: Dict[str, str] = {
    "New":              "1000",
    "New (Open Box)":   "1500",
    "Like New":         "3000",
    "Very Good":        "4000",
    "Good":             "5000",
    "Acceptable":       "6000",
    "For Parts":        "7000",
}


# ── データクラス ──────────────────────────────────────────────────
@dataclass
class ListingResult:
    success: bool
    listing_id: Optional[str] = None
    url: Optional[str] = None
    error: Optional[str] = None
    fees: Optional[float] = None   # 出品手数料 (USD)
    raw_xml: Optional[str] = None


@dataclass
class UpdateResult:
    success: bool
    error: Optional[str] = None


@dataclass
class ActiveListing:
    item_id: str
    title: str
    price_usd: float
    quantity: int
    quantity_sold: int
    url: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None


# ── XML ヘルパー ──────────────────────────────────────────────────
def _esc(s: Any) -> str:
    """XML特殊文字をエスケープ"""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _creds() -> str:
    return f"""<RequesterCredentials>
    <eBayAuthToken>{USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>"""


def _find(root: ET.Element, path: str) -> Optional[str]:
    """名前空間付きでテキストを取得"""
    parts = path.split("/")
    ns_parts = "/".join(f"{{{NS}}}{p}" for p in parts)
    el = root.find(ns_parts)
    return el.text if el is not None else None


def _findall(root: ET.Element, path: str) -> List[ET.Element]:
    parts = path.split("/")
    ns_parts = "/".join(f"{{{NS}}}{p}" for p in parts)
    return root.findall(ns_parts)


def _get_errors(root: ET.Element) -> str:
    msgs = []
    for err in root.findall(f"{{{NS}}}Errors"):
        code = err.findtext(f"{{{NS}}}ErrorCode") or ""
        msg  = err.findtext(f"{{{NS}}}LongMessage") or err.findtext(f"{{{NS}}}ShortMessage") or ""
        msgs.append(f"[{code}] {msg}")
    return " / ".join(msgs) if msgs else "Unknown error"


class EbayClient:
    """eBay Trading API クライアント（Auth'n'Auth Token方式）"""

    def __init__(self):
        self.app_id     = APP_ID
        self.dev_id     = DEV_ID
        self.cert_id    = CERT_ID
        self.user_token = USER_TOKEN
        self.site_id    = SITE_ID
        self.endpoint   = TRADING_ENDPOINT

    def _headers(self, call_name: str) -> Dict[str, str]:
        return {
            "X-EBAY-API-COMPATIBILITY-LEVEL": API_VERSION,
            "X-EBAY-API-CALL-NAME":           call_name,
            "X-EBAY-API-SITEID":              self.site_id,
            "X-EBAY-API-APP-NAME":            self.app_id,
            "X-EBAY-API-DEV-NAME":            self.dev_id,
            "X-EBAY-API-CERT-NAME":           self.cert_id,
            "Content-Type":                   "text/xml;charset=utf-8",
        }

    def _call(self, call_name: str, xml_body: str) -> ET.Element:
        """Trading API を呼び出して ElementTree を返す"""
        resp = requests.post(
            self.endpoint,
            data=xml_body.encode("utf-8"),
            headers=self._headers(call_name),
            timeout=30,
        )
        resp.raise_for_status()
        return ET.fromstring(resp.content)

    def _ack_ok(self, root: ET.Element) -> bool:
        ack = _find(root, "Ack")
        return ack in ("Success", "Warning")

    # ── 接続テスト ────────────────────────────────────────────────
    def test_connection(self) -> Tuple[bool, str]:
        """GetUser で接続確認・ユーザーIDを取得"""
        if not self.is_configured():
            missing = [k for k, v in [
                ("EBAY_APP_ID", self.app_id),
                ("EBAY_DEV_ID", self.dev_id),
                ("EBAY_CERT_ID", self.cert_id),
                ("EBAY_USER_TOKEN", self.user_token),
            ] if not v]
            return False, f"未設定の環境変数: {', '.join(missing)}"

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetUserRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
</GetUserRequest>"""

        try:
            root = self._call("GetUser", xml)
        except Exception as e:
            return False, f"通信エラー: {e}"

        if self._ack_ok(root):
            user_id    = _find(root, "User/UserID") or ""
            reg_date   = (_find(root, "User/RegistrationDate") or "")[:10]
            feedback   = _find(root, "User/FeedbackScore") or ""
            env_label  = "🧪 サンドボックス" if SANDBOX else "🟢 本番環境"
            return True, (
                f"✅ eBay API 接続OK\n"
                f"ユーザーID: {user_id}\n"
                f"フィードバックスコア: {feedback}\n"
                f"登録日: {reg_date}\n"
                f"環境: {env_label}"
            )
        return False, f"認証エラー: {_get_errors(root)}"

    # ── 出品 (AddItem) ────────────────────────────────────────────
    def create_listing(self, product: Any, price_usd: float) -> ListingResult:
        """
        商品を eBay に Fixed Price で出品する。

        Args:
            product: backend.db.models.Product インスタンス
            price_usd: 販売価格（USD）
        """
        if not self.is_configured():
            return ListingResult(success=False, error="APIキーが未設定です")

        title = _esc((product.product_name_en or product.name)[:80])
        desc  = self._build_description(product)
        cat_val = (
            product.product_category.value
            if product.product_category and hasattr(product.product_category, "value")
            else str(product.product_category or "other")
        )
        category_id  = CATEGORY_MAP.get(cat_val, "99")
        condition_str = getattr(product, "condition", "New") or "New"
        condition_id  = CONDITION_MAP.get(condition_str, "1000")
        stock = max(int(product.current_stock or 0), 1)  # 0だと出品できない
        pic_urls = self._build_pic_urls(product)
        weight_kg = (product.weight_g or 500) / 1000

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <Item>
    <Title>{title}</Title>
    <Description><![CDATA[{desc}]]></Description>
    <PrimaryCategory>
      <CategoryID>{category_id}</CategoryID>
    </PrimaryCategory>
    <StartPrice currencyID="USD">{price_usd:.2f}</StartPrice>
    <ConditionID>{condition_id}</ConditionID>
    <Country>JP</Country>
    <Currency>USD</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Quantity>{stock}</Quantity>
    <SKU>{_esc(product.sku)}</SKU>
{pic_urls}
    <ShippingDetails>
      <ShippingType>Flat</ShippingType>
      <ShippingServiceOptions>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>USPSMedia</ShippingService>
        <ShippingServiceCost currencyID="USD">0.00</ShippingServiceCost>
        <FreeShipping>true</FreeShipping>
      </ShippingServiceOptions>
      <InternationalShippingServiceOption>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>StandardIntl</ShippingService>
        <ShippingServiceCost currencyID="USD">15.00</ShippingServiceCost>
        <ShipToLocation>Worldwide</ShipToLocation>
      </InternationalShippingServiceOption>
    </ShippingDetails>
    <ReturnPolicy>
      <ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>
      <RefundOption>MoneyBack</RefundOption>
      <ReturnsWithinOption>Days_30</ReturnsWithinOption>
      <ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>
      <Description>Contact us within 30 days for return.</Description>
    </ReturnPolicy>
    <ItemSpecifics>
      <NameValueList>
        <Name>Brand</Name>
        <Value>Unbranded</Value>
      </NameValueList>
      <NameValueList>
        <Name>Country/Region of Manufacture</Name>
        <Value>Japan</Value>
      </NameValueList>
    </ItemSpecifics>
    <Location>Japan</Location>
    <PostalCode>100-0001</PostalCode>
  </Item>
</AddItemRequest>"""

        try:
            root = self._call("AddItem", xml)
        except Exception as e:
            return ListingResult(success=False, error=f"通信エラー: {e}")

        if self._ack_ok(root):
            item_id = _find(root, "ItemID") or ""
            fee_el  = root.find(f".//{{{NS}}}Fee")
            fee_val = float(fee_el.text) if fee_el is not None and fee_el.text else None
            url = f"https://www.ebay.com/itm/{item_id}"
            return ListingResult(
                success=True,
                listing_id=item_id,
                url=url,
                fees=fee_val,
            )
        return ListingResult(success=False, error=_get_errors(root))

    # ── 価格・在庫更新 (ReviseItem) ───────────────────────────────
    def update_price(self, item_id: str, price_usd: float) -> UpdateResult:
        """出品中商品の価格を更新する"""
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <Item>
    <ItemID>{_esc(item_id)}</ItemID>
    <StartPrice currencyID="USD">{price_usd:.2f}</StartPrice>
  </Item>
</ReviseItemRequest>"""

        try:
            root = self._call("ReviseItem", xml)
        except Exception as e:
            return UpdateResult(success=False, error=f"通信エラー: {e}")

        if self._ack_ok(root):
            return UpdateResult(success=True)
        return UpdateResult(success=False, error=_get_errors(root))

    def update_stock(self, item_id: str, quantity: int) -> UpdateResult:
        """出品中商品の在庫数を更新する"""
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <Item>
    <ItemID>{_esc(item_id)}</ItemID>
    <Quantity>{quantity}</Quantity>
  </Item>
</ReviseItemRequest>"""

        try:
            root = self._call("ReviseItem", xml)
        except Exception as e:
            return UpdateResult(success=False, error=f"通信エラー: {e}")

        if self._ack_ok(root):
            return UpdateResult(success=True)
        return UpdateResult(success=False, error=_get_errors(root))

    def update_listing(
        self,
        item_id: str,
        price_usd: Optional[float] = None,
        stock: Optional[int] = None,
    ) -> UpdateResult:
        """価格・在庫を一括更新する"""
        if price_usd is None and stock is None:
            return UpdateResult(success=True)

        price_xml = f'<StartPrice currencyID="USD">{price_usd:.2f}</StartPrice>' if price_usd is not None else ""
        stock_xml = f"<Quantity>{stock}</Quantity>" if stock is not None else ""

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <Item>
    <ItemID>{_esc(item_id)}</ItemID>
    {price_xml}
    {stock_xml}
  </Item>
</ReviseItemRequest>"""

        try:
            root = self._call("ReviseItem", xml)
        except Exception as e:
            return UpdateResult(success=False, error=f"通信エラー: {e}")

        if self._ack_ok(root):
            return UpdateResult(success=True)
        return UpdateResult(success=False, error=_get_errors(root))

    # ── 出品停止 (EndItem) ────────────────────────────────────────
    def end_listing(self, item_id: str, reason: str = "NotAvailable") -> UpdateResult:
        """
        出品を停止する。

        reason: LostOrBroken / NotAvailable / OtherListingError / SellToHighBidder
        """
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<EndItemRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <ItemID>{_esc(item_id)}</ItemID>
  <EndingReason>{reason}</EndingReason>
</EndItemRequest>"""

        try:
            root = self._call("EndItem", xml)
        except Exception as e:
            return UpdateResult(success=False, error=f"通信エラー: {e}")

        if self._ack_ok(root):
            return UpdateResult(success=True)
        return UpdateResult(success=False, error=_get_errors(root))

    # ── 出品中商品一覧 (GetMyeBaySelling) ────────────────────────
    def get_active_listings(self, page: int = 1, per_page: int = 100) -> Tuple[List[ActiveListing], str]:
        """
        出品中商品の一覧を取得する。

        Returns:
            (listings, error_msg) — エラー時は listings=[], error_msg にメッセージ
        """
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{per_page}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
    <Sort>TimeLeft</Sort>
  </ActiveList>
</GetMyeBaySellingRequest>"""

        try:
            root = self._call("GetMyeBaySelling", xml)
        except Exception as e:
            return [], f"通信エラー: {e}"

        if not self._ack_ok(root):
            return [], _get_errors(root)

        listings: List[ActiveListing] = []
        for item in root.findall(f".//{{{NS}}}ActiveList/{{{NS}}}ItemArray/{{{NS}}}Item"):
            item_id = item.findtext(f"{{{NS}}}ItemID") or ""
            title   = item.findtext(f"{{{NS}}}Title") or ""

            # 現在価格
            price_el = item.find(f".//{{{NS}}}CurrentPrice")
            price = float(price_el.text) if price_el is not None and price_el.text else 0.0

            qty       = int(item.findtext(f"{{{NS}}}Quantity") or 0)
            qty_sold  = int(item.findtext(f"{{{NS}}}QuantitySold") or 0)
            start_t   = item.findtext(f"{{{NS}}}ListingDetails/{{{NS}}}StartTime") or ""
            end_t     = item.findtext(f"{{{NS}}}ListingDetails/{{{NS}}}EndTime") or ""
            url       = item.findtext(f"{{{NS}}}ListingDetails/{{{NS}}}ViewItemURL") or f"https://www.ebay.com/itm/{item_id}"

            listings.append(ActiveListing(
                item_id=item_id,
                title=title,
                price_usd=price,
                quantity=qty,
                quantity_sold=qty_sold,
                url=url,
                start_time=start_t[:10] if start_t else None,
                end_time=end_t[:10] if end_t else None,
            ))

        return listings, ""

    # ── 個別商品情報取得 (GetItem) ────────────────────────────────
    def get_item(self, item_id: str) -> Tuple[Optional[Dict], str]:
        """出品中の個別商品情報を取得する"""
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="{NS}">
  {_creds()}
  <ErrorLanguage>en_US</ErrorLanguage>
  <ItemID>{_esc(item_id)}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>"""

        try:
            root = self._call("GetItem", xml)
        except Exception as e:
            return None, f"通信エラー: {e}"

        if not self._ack_ok(root):
            return None, _get_errors(root)

        item = root.find(f"{{{NS}}}Item")
        if item is None:
            return None, "商品情報が取得できませんでした"

        price_el = item.find(f".//{{{NS}}}StartPrice")
        return {
            "item_id":   item.findtext(f"{{{NS}}}ItemID") or "",
            "title":     item.findtext(f"{{{NS}}}Title") or "",
            "price_usd": float(price_el.text) if price_el is not None and price_el.text else 0.0,
            "quantity":  int(item.findtext(f"{{{NS}}}Quantity") or 0),
            "status":    item.findtext(f"{{{NS}}}SellingStatus/{{{NS}}}ListingStatus") or "",
            "url":       item.findtext(f"{{{NS}}}ListingDetails/{{{NS}}}ViewItemURL") or "",
        }, ""

    # ── ヘルパー ──────────────────────────────────────────────────
    def _build_description(self, product: Any) -> str:
        """eBay 用 HTML 説明文を生成"""
        name = product.product_name_en or product.name
        desc = product.product_description_en or product.description or ""
        specs: List[str] = []
        if product.weight_g:
            specs.append(f"<li>Weight: {product.weight_g:.0f}g</li>")
        if product.jan_code:
            specs.append(f"<li>JAN: {product.jan_code}</li>")
        if product.upc_code:
            specs.append(f"<li>UPC: {product.upc_code}</li>")
        if product.asin:
            specs.append(f"<li>ASIN: {product.asin}</li>")
        spec_html = f"<ul>{''.join(specs)}</ul>" if specs else ""

        return f"""<div style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;color:#333;">
<h2 style="border-bottom:2px solid #e53935;padding-bottom:8px;">{_esc(name)}</h2>
<p style="line-height:1.7;">{_esc(desc)}</p>
{spec_html}
<hr style="margin:20px 0;border-color:#eee;"/>
<p style="background:#fff9c4;padding:12px;border-radius:6px;">
  <strong>📦 Shipping from Japan</strong><br/>
  Estimated delivery: 7–14 business days via EMS or DHL.<br/>
  All items are carefully inspected before shipping.
</p>
<p style="font-size:11px;color:#999;margin-top:16px;">
  Sold by GlobalBiz Japan. Please contact us with any questions.
</p>
</div>"""

    def _build_pic_urls(self, product: Any) -> str:
        """PictureDetails XML を生成"""
        urls: List[str] = []
        if product.image_urls and isinstance(product.image_urls, list):
            urls = [u for u in product.image_urls if u]
        if product.image_url and product.image_url not in urls:
            urls.insert(0, product.image_url)
        if not urls:
            return ""
        pics = "".join(f"    <PictureURL>{_esc(u)}</PictureURL>\n" for u in urls[:12])
        return f"    <PictureDetails>\n{pics}    </PictureDetails>"

    # ── 注文・追跡番号 ───────────────────────────────────────────────

    def get_orders(
        self,
        days_back: int = 30,
        page: int = 1,
        per_page: int = 50,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        GetOrders で注文一覧を取得する。

        Returns:
            ([{order_id, buyer_name, buyer_email, shipping_*, item_id,
               item_title, sku, quantity, sale_price, currency, paid_time}],
             error_msg or None)
        """
        from datetime import timedelta
        create_time_from = (
            datetime.utcnow() - timedelta(days=days_back)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetOrdersRequest xmlns="{NS}">
  {_creds()}
  <CreateTimeFrom>{create_time_from}</CreateTimeFrom>
  <CreateTimeTo>{datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")}</CreateTimeTo>
  <OrderRole>Seller</OrderRole>
  <OrderStatus>All</OrderStatus>
  <Pagination>
    <EntriesPerPage>{per_page}</EntriesPerPage>
    <PageNumber>{page}</PageNumber>
  </Pagination>
  <DetailLevel>ReturnAll</DetailLevel>
</GetOrdersRequest>"""

        try:
            root = self._call("GetOrders", xml)
        except Exception as e:
            return [], str(e)

        if not self._ack_ok(root):
            return [], _get_errors(root)

        orders: List[Dict[str, Any]] = []
        for order_el in root.findall(f".//{{{NS}}}Order"):
            order_id = _find(order_el, f"{{{NS}}}OrderID") or ""

            # 購入者情報
            buyer = order_el.find(f".//{{{NS}}}Buyer")
            buyer_id    = _find(buyer, f"{{{NS}}}UserID") if buyer is not None else ""
            buyer_email = _find(buyer, f"{{{NS}}}Email")  if buyer is not None else ""

            # 配送先
            addr = order_el.find(f".//{{{NS}}}ShippingAddress")
            shipping: Dict[str, str] = {}
            if addr is not None:
                shipping = {
                    "name":    _find(addr, f"{{{NS}}}Name")       or "",
                    "address": _find(addr, f"{{{NS}}}Street1")    or "",
                    "city":    _find(addr, f"{{{NS}}}CityName")   or "",
                    "state":   _find(addr, f"{{{NS}}}StateOrProvince") or "",
                    "postal":  _find(addr, f"{{{NS}}}PostalCode") or "",
                    "country": _find(addr, f"{{{NS}}}Country")    or "",
                }

            # 商品情報（最初のトランザクション）
            trans_el = order_el.find(f".//{{{NS}}}Transaction")
            item_id = sku = title = ""
            qty = 1
            price = 0.0
            currency = "USD"
            trans_id = ""
            if trans_el is not None:
                item_el  = trans_el.find(f"{{{NS}}}Item")
                item_id  = _find(item_el, f"{{{NS}}}ItemID")    or "" if item_el is not None else ""
                title    = _find(item_el, f"{{{NS}}}Title")     or "" if item_el is not None else ""
                sku      = _find(item_el, f"{{{NS}}}SKU")       or "" if item_el is not None else ""
                qty      = int(_find(trans_el, f"{{{NS}}}QuantityPurchased") or 1)
                trans_id = _find(trans_el, f"{{{NS}}}TransactionID") or ""
                tp_el    = trans_el.find(f".//{{{NS}}}TransactionPrice")
                if tp_el is not None:
                    price    = float(tp_el.text or 0)
                    currency = tp_el.get("currencyID", "USD")

            paid_time    = _find(order_el, f"{{{NS}}}PaidTime")    or ""
            order_status = _find(order_el, f"{{{NS}}}OrderStatus") or ""
            tracking_num = _find(order_el, f".//{{{NS}}}ShipmentTrackingNumber") or ""

            orders.append({
                "order_id":    order_id,
                "trans_id":    trans_id,
                "item_id":     item_id,
                "item_title":  title,
                "sku":         sku,
                "quantity":    qty,
                "sale_price":  price,
                "currency":    currency,
                "buyer_name":  buyer_id,
                "buyer_email": buyer_email or "",
                "shipping":    shipping,
                "paid_time":   paid_time,
                "status":      order_status,
                "tracking":    tracking_num,
                "platform":    "ebay",
            })

        return orders, None

    def complete_sale(
        self,
        item_id: str,
        trans_id: str,
        tracking_number: str,
        carrier: str = "FedEx",
    ) -> UpdateResult:
        """
        CompleteSale: 発送済みマーク + 追跡番号をeBayに登録する。

        Args:
            item_id: eBay Item ID
            trans_id: Transaction ID（注文ごとに異なる）
            tracking_number: 追跡番号
            carrier: 配送業者名（FedEx / EMS / DHL / UPS など）
        """
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<CompleteSaleRequest xmlns="{NS}">
  {_creds()}
  <ItemID>{_esc(item_id)}</ItemID>
  <TransactionID>{_esc(trans_id)}</TransactionID>
  <Paid>true</Paid>
  <Shipped>true</Shipped>
  <Shipment>
    <ShipmentTrackingDetails>
      <ShippingCarrierUsed>{_esc(carrier)}</ShippingCarrierUsed>
      <ShipmentTrackingNumber>{_esc(tracking_number)}</ShipmentTrackingNumber>
    </ShipmentTrackingDetails>
  </Shipment>
</CompleteSaleRequest>"""

        try:
            root = self._call("CompleteSale", xml)
        except Exception as e:
            return UpdateResult(success=False, error=str(e))

        if self._ack_ok(root):
            return UpdateResult(success=True)
        return UpdateResult(success=False, error=_get_errors(root))

    def is_configured(self) -> bool:
        return bool(self.app_id and self.dev_id and self.cert_id and self.user_token)


# ── シングルトン ──────────────────────────────────────────────────
_client: Optional[EbayClient] = None


def get_ebay_client() -> EbayClient:
    global _client
    if _client is None:
        _client = EbayClient()
    return _client
