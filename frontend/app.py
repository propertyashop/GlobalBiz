"""GlobalBiz 管理画面 v2"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json
import pandas as pd
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv, set_key

from backend.db.database import init_db, get_session
from backend.db.models import Product, ProductStatus, SourceSite, ProductCategory, SizeClass
from backend.calculators.tariff import (
    calculate_tariff, COUNTRY_NAMES, EBAY_COUNTRIES, SHOPEE_COUNTRIES,
)
from backend.calculators.shipping import (
    calculate_domestic_shipping, calculate_international_shipping,
    DomesticCarrier, IntlCarrier,
)
from backend.exporters.shopee_csv import (
    export_shopee_csv, get_shopee_category_options, SHOPEE_CATEGORIES,
)
from backend.exporters.ebay_csv import (
    export_ebay_csv, get_ebay_category_options,
)

load_dotenv()
init_db()

# ─────────────────────────── ページ設定 ────────────────────────────
st.set_page_config(
    page_title="GlobalBiz",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.6rem; }
.profit-box {
    background:#f0f7ff; border-radius:10px; padding:16px;
    border-left:4px solid #1976D2;
}
.profit-row { display:flex; justify-content:space-between; padding:3px 0; }
.profit-total {
    border-top:2px solid #1976D2; margin-top:8px; padding-top:8px;
    font-weight:bold; font-size:1.1rem;
}
.tag-ebay  { background:#e53935; color:white; border-radius:4px; padding:2px 6px; font-size:0.75rem; }
.tag-shopee{ background:#EE4D2D; color:white; border-radius:4px; padding:2px 6px; font-size:0.75rem; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────── 定数 ────────────────────────────
SOURCE_LABELS = {
    SourceSite.AMAZON:  "🛒 Amazon",
    SourceSite.RAKUTEN: "🔴 楽天",
    SourceSite.YAHOO:   "🟣 Yahoo",
    SourceSite.NETSEA:  "🔵 NETSEA",
    SourceSite.MANUAL:  "✏️ 手動",
}
STATUS_LABELS = {
    ProductStatus.ACTIVE:        "✅ 販売中",
    ProductStatus.OUT_OF_STOCK:  "❌ 在庫切れ",
    ProductStatus.DISCONTINUED:  "🚫 終了",
    ProductStatus.DRAFT:         "📝 下書き",
}
CATEGORY_LABELS = {
    ProductCategory.ELECTRONICS: "📱 家電・電子機器",
    ProductCategory.CLOTHING:    "👗 衣類・アパレル",
    ProductCategory.ACCESSORIES: "💍 アクセサリー",
    ProductCategory.TOYS:        "🎮 おもちゃ・ゲーム",
    ProductCategory.FOOD:        "🍱 食品・飲料",
    ProductCategory.COSMETICS:   "💄 化粧品・美容",
    ProductCategory.HEALTH:      "💊 健康・医療",
    ProductCategory.SPORTS:      "⚽ スポーツ",
    ProductCategory.HOME:        "🏠 家具・インテリア",
    ProductCategory.BOOKS:       "📚 書籍・メディア",
    ProductCategory.AUTO:        "🚗 自動車・バイク",
    ProductCategory.OTHER:       "📦 その他",
}

ALL_COUNTRIES = {**{c: f"🇺🇸 {COUNTRY_NAMES[c]}" for c in EBAY_COUNTRIES if c in COUNTRY_NAMES},
                 **{c: f"🌏 {COUNTRY_NAMES[c]}" for c in SHOPEE_COUNTRIES if c in COUNTRY_NAMES}}
# 重複除去・順序維持
_seen = set()
COUNTRY_OPTIONS: dict[str, str] = {}
for k, v in {**{c: f"🇺🇸 {COUNTRY_NAMES[c]}" for c in EBAY_COUNTRIES},
              **{c: f"🌏 {COUNTRY_NAMES[c]}" for c in SHOPEE_COUNTRIES}}.items():
    if k not in _seen:
        _seen.add(k)
        COUNTRY_OPTIONS[k] = v

ENV_PATH = Path(__file__).parent.parent / ".env"

# ─────────────────────────── ヘルパー ────────────────────────────
def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def save_env(key: str, value: str) -> None:
    """key=value を .env に書き込む（なければ作成）"""
    env_file = str(ENV_PATH)
    if not ENV_PATH.exists():
        ENV_PATH.write_text("")
    set_key(env_file, key, value)
    os.environ[key] = value

def products_to_df(products: list) -> pd.DataFrame:
    rows = []
    for p in products:
        ebay_status = "🟢 出品中" if p.ebay_listing_id else ("⬜ 対象" if p.target_ebay else "—")
        shopee_status = "🟢 出品中" if p.shopee_item_id else ("⬜ 対象" if p.target_shopee else "—")
        rows.append({
            "ID": p.id,
            "SKU": p.sku,
            "商品名": p.name[:35] + ("…" if len(p.name) > 35 else ""),
            "仕入れ元": SOURCE_LABELS.get(p.source_site, str(p.source_site)),
            "仕入れ値": f"¥{p.cost_price:,.0f}",
            "推奨USD": f"${p.calc_selling_price_usd:,.2f}" if p.calc_selling_price_usd else "—",
            "推奨SGD": f"S${p.calc_selling_price_sgd:,.2f}" if p.calc_selling_price_sgd else "—",
            "在庫": p.current_stock,
            "eBay": ebay_status,
            "Shopee": shopee_status,
            "状態": STATUS_LABELS.get(p.status, str(p.status)),
        })
    return pd.DataFrame(rows)


def calc_profit_preview(
    cost_price: float,
    weight_g: float,
    size_l: float,
    size_w: float,
    size_h: float,
    category_val: str,
    target_countries: list[str],
    target_profit_rate: float,
    usd_rate: float,
    sgd_rate: float,
    ebay_fee: float,
    shopee_fee: float,
    payment_fee: float,
    fx_fee: float,
) -> dict:
    """利益計算プレビューを返す"""
    if cost_price <= 0:
        return {}

    # 国内送料
    dom = calculate_domestic_shipping(weight_g or 500, size_l, size_w, size_h)
    domestic_ship = dom.fee_jpy

    # 国際送料（最安のEMSで代表計算）
    intl_ships: dict[str, float] = {}
    for code in target_countries:
        r = calculate_international_shipping(weight_g or 500, code, IntlCarrier.EMS)
        intl_ships[code] = r.fee_jpy

    # 関税
    tariffs: dict[str, float] = {}
    for code in target_countries:
        r = calculate_tariff(cost_price, code, category_val, usd_rate)
        tariffs[code] = r.tariff_amount_jpy

    # 販売先国ごとの推奨価格
    results: dict[str, dict] = {}
    usd_prices = []
    sgd_prices = []

    for code in target_countries:
        intl = intl_ships.get(code, 0)
        tariff = tariffs.get(code, 0)
        total_cost = cost_price + domestic_ship + intl + tariff

        # eBay or Shopee 判定
        is_ebay   = code in EBAY_COUNTRIES
        is_shopee = code in SHOPEE_COUNTRIES
        marketplace_fee = ebay_fee if is_ebay else shopee_fee
        total_fee_rate = marketplace_fee + payment_fee + fx_fee

        # 推奨販売価格 = 総コスト / (1 - 手数料率 - 利益率)
        denominator = 1 - total_fee_rate - target_profit_rate
        if denominator <= 0:
            denominator = 0.1
        price_jpy = total_cost / denominator

        price_usd = price_jpy / usd_rate
        price_sgd = price_jpy / sgd_rate

        profit_jpy = price_jpy * target_profit_rate
        results[code] = {
            "country": COUNTRY_NAMES.get(code, code),
            "cost": cost_price,
            "domestic_ship": domestic_ship,
            "intl_ship": intl,
            "tariff": tariff,
            "total_cost": total_cost,
            "fee_rate": total_fee_rate,
            "price_jpy": price_jpy,
            "price_usd": price_usd,
            "price_sgd": price_sgd,
            "profit_jpy": profit_jpy,
            "is_ebay": is_ebay,
            "marketplace": "eBay" if is_ebay else "Shopee",
        }
        if is_ebay:
            usd_prices.append(price_usd)
        if is_shopee:
            sgd_prices.append(price_sgd)

    return {
        "countries": results,
        "domestic_ship": domestic_ship,
        "avg_price_usd": sum(usd_prices) / len(usd_prices) if usd_prices else None,
        "avg_price_sgd": sum(sgd_prices) / len(sgd_prices) if sgd_prices else None,
    }


# ─────────────────────────── サイドバー ────────────────────────────
with st.sidebar:
    st.title("🌐 GlobalBiz")
    st.caption("越境EC運営ツール")
    st.divider()
    page = st.radio(
        "ナビゲーション",
        ["📦 商品一覧", "➕ 商品登録", "🚀 出品管理", "⚙️ 設定"],
        label_visibility="collapsed",
    )
    st.divider()
    with get_session() as s:
        n = s.query(Product).count()
    st.caption(f"登録商品: {n} 件")


# ══════════════════════════════════════════════════════════════════
#  PAGE: 商品一覧
# ══════════════════════════════════════════════════════════════════
if page == "📦 商品一覧":
    st.title("📦 商品一覧")

    with get_session() as s:
        total       = s.query(Product).count()
        active      = s.query(Product).filter(Product.status == ProductStatus.ACTIVE).count()
        out_stock   = s.query(Product).filter(Product.status == ProductStatus.OUT_OF_STOCK).count()
        listed_ebay = s.query(Product).filter(Product.ebay_listing_id.isnot(None)).count()
        listed_shop = s.query(Product).filter(Product.shopee_item_id.isnot(None)).count()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("総商品数", total)
    c2.metric("販売中", active)
    c3.metric("在庫切れ", out_stock, delta=f"-{out_stock}" if out_stock else None, delta_color="inverse")
    c4.metric("eBay出品中", listed_ebay)
    c5.metric("Shopee出品中", listed_shop)

    st.divider()

    with st.expander("🔍 フィルター", expanded=False):
        fc1, fc2, fc3 = st.columns(3)
        f_status = fc1.selectbox("ステータス", ["すべて","販売中","在庫切れ","下書き"])
        f_source = fc2.selectbox("仕入れ元", ["すべて","Amazon","楽天","Yahoo","NETSEA","手動"])
        f_mkt    = fc3.selectbox("販売先", ["すべて","eBay","Shopee"])

    with get_session() as s:
        q = s.query(Product)
        if f_status == "販売中":    q = q.filter(Product.status == ProductStatus.ACTIVE)
        elif f_status == "在庫切れ": q = q.filter(Product.status == ProductStatus.OUT_OF_STOCK)
        elif f_status == "下書き":  q = q.filter(Product.status == ProductStatus.DRAFT)
        if f_source == "Amazon": q = q.filter(Product.source_site == SourceSite.AMAZON)
        elif f_source == "楽天":  q = q.filter(Product.source_site == SourceSite.RAKUTEN)
        elif f_source == "Yahoo": q = q.filter(Product.source_site == SourceSite.YAHOO)
        elif f_source == "NETSEA":q = q.filter(Product.source_site == SourceSite.NETSEA)
        elif f_source == "手動":  q = q.filter(Product.source_site == SourceSite.MANUAL)
        if f_mkt == "eBay":   q = q.filter(Product.target_ebay == True)
        elif f_mkt == "Shopee":q = q.filter(Product.target_shopee == True)
        products = q.order_by(Product.updated_at.desc()).all()

    if not products:
        st.info("商品がありません。「➕ 商品登録」から追加してください。")
    else:
        df = products_to_df(products)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(products)} 件表示")

        # ─── CSV エクスポート ───
        st.divider()
        st.subheader("📥 CSVエクスポート")
        st.caption("Shopee API の実績条件（30注文/月）を満たすまでは CSV 運用でアップロードできます。")

        _csv_usd = float(get_env("DEFAULT_EXCHANGE_RATE_USD", "150"))
        _csv_sgd = float(get_env("DEFAULT_EXCHANGE_RATE_SGD", "112"))

        # 出力対象の選択
        with st.expander("🎯 出力対象を選ぶ", expanded=True):
            _export_scope = st.radio(
                "対象",
                ["表示中の全商品", "Shopee対象商品のみ", "eBay対象商品のみ"],
                horizontal=True,
                label_visibility="collapsed",
            )
            if _export_scope == "Shopee対象商品のみ":
                _export_products = [p for p in products if p.target_shopee]
            elif _export_scope == "eBay対象商品のみ":
                _export_products = [p for p in products if p.target_ebay]
            else:
                _export_products = products
            st.caption(f"出力対象: {len(_export_products)} 件")

        _dl_col1, _dl_col2 = st.columns(2)

        # Shopee CSV
        with _dl_col1:
            with st.container(border=True):
                st.markdown("#### 🟧 Shopee Mass Upload CSV")
                st.caption("Shopee セラーセンター → 商品 → 一括アップロード で使用")
                _shopee_action = st.selectbox(
                    "アクション",
                    ["新規出品（Add）"],
                    key="shopee_csv_action",
                    label_visibility="collapsed",
                )
                _fname_shopee = f"shopee_products_{datetime.now().strftime('%Y%m%d')}.csv"
                if _export_products:
                    _shopee_bytes = export_shopee_csv(_export_products, sgd_rate=_csv_sgd)
                    st.download_button(
                        label=f"⬇️ Shopee CSV をダウンロード ({len(_export_products)}件)",
                        data=_shopee_bytes,
                        file_name=_fname_shopee,
                        mime="text/csv",
                        use_container_width=True,
                        type="primary",
                    )
                    st.caption(f"ファイル名: {_fname_shopee}  |  {len(_shopee_bytes):,} bytes")
                else:
                    st.warning("出力対象の商品がありません")

        # eBay CSV
        with _dl_col2:
            with st.container(border=True):
                st.markdown("#### 🟦 eBay File Exchange CSV")
                st.caption("eBay セラーハブ → リスト → ファイルでアップロード で使用")
                _ebay_action = st.selectbox(
                    "アクション",
                    ["Add（新規）", "Revise（更新）", "End（終了）"],
                    key="ebay_csv_action",
                    label_visibility="collapsed",
                )
                _action_map = {"Add（新規）": "Add", "Revise（更新）": "Revise", "End（終了）": "End"}
                _fname_ebay = f"ebay_products_{datetime.now().strftime('%Y%m%d')}.csv"
                if _export_products:
                    _ebay_bytes = export_ebay_csv(
                        _export_products,
                        action=_action_map[_ebay_action],
                        usd_rate=_csv_usd,
                    )
                    st.download_button(
                        label=f"⬇️ eBay CSV をダウンロード ({len(_export_products)}件)",
                        data=_ebay_bytes,
                        file_name=_fname_ebay,
                        mime="text/csv",
                        use_container_width=True,
                        type="primary",
                    )
                    st.caption(f"ファイル名: {_fname_ebay}  |  {len(_ebay_bytes):,} bytes")
                else:
                    st.warning("出力対象の商品がありません")

        with st.expander("📖 CSV アップロード手順", expanded=False):
            st.markdown("""
**Shopee Mass Upload の手順:**
1. [セラーセンター](https://seller.shopee.sg/) → **商品** → **一括アップロード**
2. 「テンプレートをダウンロード」で公式テンプレートを確認
3. 上記でダウンロードした CSV をアップロード
4. エラーがある行を修正して再アップロード

**eBay File Exchange の手順:**
1. [eBay セラーハブ](https://www.ebay.com/sellerhub) → **リスト** → **ファイルでアップロード**
2. 「ファイルをアップロード」から CSV を選択
3. エラーレポートを確認・修正

**注意事項:**
- `Category ID` は Shopee/eBay の正しいカテゴリ ID に修正してください
- 画像URLは公開アクセス可能なURLである必要があります
- Shopee CSV は **UTF-8 BOM付き** で出力されます（Excelで開いても文字化けしません）
""")

        st.divider()
        st.subheader("🚀 クイック出品")
        st.caption("商品を選択して出品・更新ができます。詳細な出品管理は「🚀 出品管理」タブへ。")

        product_options = {f"[{p.id}] {p.sku} - {p.name[:30]}": p.id for p in products}
        sel = st.selectbox("商品を選択", list(product_options.keys()))
        sel_id = product_options[sel]

        with get_session() as s:
            sel_product = s.query(Product).filter(Product.id == sel_id).first()
            if sel_product:
                pc1, pc2, pc3 = st.columns(3)
                pc1.info(f"**仕入れ値:** ¥{sel_product.cost_price:,.0f}")
                pc2.info(f"**推奨USD:** {'${:,.2f}'.format(sel_product.calc_selling_price_usd) if sel_product.calc_selling_price_usd else '未計算'}")
                pc3.info(f"**推奨SGD:** {'S${:,.2f}'.format(sel_product.calc_selling_price_sgd) if sel_product.calc_selling_price_sgd else '未計算'}")

        bc1, bc2 = st.columns(2)
        if bc1.button("🟦 eBay に出品", use_container_width=True):
            st.session_state["listing_target"] = (sel_id, "ebay")
            st.session_state["show_listing_modal"] = True
            st.rerun()
        if bc2.button("🟧 Shopee に出品", use_container_width=True):
            st.session_state["listing_target"] = (sel_id, "shopee")
            st.session_state["show_listing_modal"] = True
            st.rerun()

    # ─── 出品確認モーダル ───
    if st.session_state.get("show_listing_modal") and st.session_state.get("listing_target"):
        pid, platform = st.session_state["listing_target"]
        with st.container(border=True):
            st.subheader(f"🚀 {'eBay' if platform == 'ebay' else 'Shopee'} 出品確認")
            with get_session() as s:
                p = s.query(Product).filter(Product.id == pid).first()
            if p:
                st.write(f"**商品:** {p.name}")
                st.write(f"**SKU:** {p.sku}  |  **在庫:** {p.current_stock}")

                if platform == "ebay":
                    price_val = p.calc_selling_price_usd or (p.cost_price / 150 * 1.5)
                    price_input = st.number_input("販売価格 (USD)", value=round(float(price_val), 2), step=0.5)
                else:
                    price_val = p.calc_selling_price_sgd or (p.cost_price / 112 * 1.5)
                    price_input = st.number_input("販売価格 (SGD)", value=round(float(price_val), 2), step=0.5)

                mc1, mc2, mc3 = st.columns(3)
                if mc1.button("✅ 出品する", type="primary"):
                    with st.spinner("出品処理中..."):
                        if platform == "ebay":
                            from backend.marketplaces.ebay import get_ebay_client
                            client = get_ebay_client()
                            if not client.is_configured():
                                st.error("⚠️ eBay APIキーが未設定です。設定画面で入力してください。")
                            else:
                                result = client.create_listing(p, price_input)
                                if result.success:
                                    with get_session() as s2:
                                        prod = s2.query(Product).filter(Product.id == pid).first()
                                        prod.ebay_listing_id = result.listing_id
                                        prod.status = ProductStatus.ACTIVE
                                        s2.commit()
                                    st.success(f"✅ eBay に出品しました！ listing_id: {result.listing_id}")
                                    if result.url:
                                        st.write(f"🔗 [出品ページを開く]({result.url})")
                                else:
                                    st.error(f"出品失敗: {result.error}")
                        else:
                            from backend.marketplaces.shopee import get_shopee_client
                            client = get_shopee_client()
                            if not client.is_configured():
                                st.error("⚠️ Shopee APIキーが未設定です。設定画面で入力してください。")
                            else:
                                result = client.add_item(p, price_input)
                                if result.success:
                                    with get_session() as s2:
                                        prod = s2.query(Product).filter(Product.id == pid).first()
                                        prod.shopee_item_id = str(result.item_id)
                                        prod.status = ProductStatus.ACTIVE
                                        s2.commit()
                                    st.success(f"✅ Shopee に出品しました！ item_id: {result.item_id}")
                                else:
                                    st.error(f"出品失敗: {result.error}")

                if mc2.button("❌ キャンセル"):
                    st.session_state["show_listing_modal"] = False
                    st.session_state["listing_target"] = None
                    st.rerun()


# ══════════════════════════════════════════════════════════════════
#  PAGE: 商品登録
# ══════════════════════════════════════════════════════════════════
elif page == "➕ 商品登録":
    st.title("➕ 商品登録")

    # 為替レート（設定から読む）
    usd_rate     = float(get_env("DEFAULT_EXCHANGE_RATE_USD", "150"))
    sgd_rate     = float(get_env("DEFAULT_EXCHANGE_RATE_SGD", "112"))
    ebay_fee     = float(get_env("EBAY_FEE_RATE", "0.13"))
    shopee_fee   = float(get_env("SHOPEE_FEE_RATE", "0.06"))
    payment_fee  = float(get_env("PAYMENT_FEE_RATE", "0.044"))
    fx_fee       = float(get_env("FX_FEE_RATE", "0.02"))

    left, right = st.columns([3, 2], gap="large")

    # ────── 左カラム: 入力フォーム ──────
    with left:
        with st.form("product_form", clear_on_submit=True):

            # 基本情報
            st.subheader("📋 基本情報")
            c1, c2 = st.columns(2)
            name = c1.text_input("商品名 *", placeholder="例: Apple AirPods Pro")
            sku  = c2.text_input("SKU *", placeholder="例: APPLE-APP-001")

            c3, c4 = st.columns(2)
            source_site_val = c3.selectbox(
                "仕入れ元 *",
                [s.value for s in SourceSite],
                format_func=lambda v: SOURCE_LABELS.get(SourceSite(v), v),
            )
            status_val = c4.selectbox(
                "ステータス",
                [s.value for s in ProductStatus],
                format_func=lambda v: STATUS_LABELS.get(ProductStatus(v), v),
                index=3,
            )

            source_url_val = st.text_input("仕入れ元URL", placeholder="https://...")

            # 仕入れ元別 識別番号
            st.subheader("🔖 商品識別番号")
            id_c1, id_c2 = st.columns(2)

            asin_val = rakuten_val = yahoo_val = netsea_val = ""
            if source_site_val == SourceSite.AMAZON.value:
                asin_val = id_c1.text_input("ASIN *", placeholder="B0BDHWDR12", max_chars=10)
                if asin_val and (len(asin_val) != 10 or not asin_val.startswith("B")):
                    id_c1.caption("⚠️ ASIN は B から始まる10桁")
            elif source_site_val == SourceSite.RAKUTEN.value:
                rakuten_val = id_c1.text_input("楽天商品コード *", placeholder="shop:item-001")
            elif source_site_val == SourceSite.YAHOO.value:
                yahoo_val = id_c1.text_input("Yahoo商品コード *", placeholder="yahoo-item-001")
            elif source_site_val == SourceSite.NETSEA.value:
                netsea_val = id_c1.text_input("NETSEA商品ID *", placeholder="12345678")

            jan_val = id_c2.text_input("JANコード（任意）", placeholder="4901234567890", max_chars=13)
            upc_val = id_c2.text_input("UPCコード（任意）", placeholder="012345678901", max_chars=12)

            # カテゴリ
            st.subheader("🏷️ カテゴリ・詳細")
            cat_c1, cat_c2 = st.columns(2)
            category_val = cat_c1.selectbox(
                "商品カテゴリ *",
                [c.value for c in ProductCategory],
                format_func=lambda v: CATEGORY_LABELS.get(ProductCategory(v), v),
            )
            hs_code_val = cat_c2.text_input("HSコード", placeholder="8518.30")

            # 重量・サイズ
            st.subheader("📐 重量・サイズ")
            w_c1, w_c2, w_c3, w_c4 = st.columns(4)
            weight_g_val = w_c1.number_input("重量 (g)", min_value=0.0, step=10.0, value=0.0)
            size_l_val   = w_c2.number_input("縦 (cm)", min_value=0.0, step=1.0, value=0.0)
            size_w_val   = w_c3.number_input("横 (cm)", min_value=0.0, step=1.0, value=0.0)
            size_h_val   = w_c4.number_input("高さ (cm)", min_value=0.0, step=1.0, value=0.0)

            # 価格・在庫
            st.subheader("💴 価格・在庫")
            p_c1, p_c2, p_c3 = st.columns(3)
            cost_price_val    = p_c1.number_input("仕入れ値（円）*", min_value=0.0, step=100.0)
            current_stock_val = p_c2.number_input("在庫数", min_value=0, step=1)
            profit_rate_val   = p_c3.slider("希望利益率", 10, 50, 25, step=5, format="%d%%") / 100

            # 販売先
            st.subheader("🌍 販売先")
            mp_c1, mp_c2 = st.columns(2)
            target_ebay_val   = mp_c1.checkbox("eBay に出品する")
            target_shopee_val = mp_c2.checkbox("Shopee に出品する")

            country_options_list = list(COUNTRY_OPTIONS.keys())
            country_labels_list  = list(COUNTRY_OPTIONS.values())
            target_countries_val = st.multiselect(
                "販売先国（複数選択可）",
                options=country_options_list,
                format_func=lambda v: COUNTRY_OPTIONS.get(v, v),
                default=["USA"] if target_ebay_val else (["SGP"] if target_shopee_val else []),
                help="eBay対応: US/AU/UK/DE/CA/TW/HK  |  Shopee対応: SGP/MYS/THA/PHL/IDN/VNM/TWN",
            )

            # 出品用・英語情報
            st.subheader("🌏 出品用情報（CSV/API 共通）")
            en_c1, en_c2 = st.columns(2)
            product_name_en_val = en_c1.text_input(
                "英語商品名",
                placeholder="例: Apple AirPods Pro 2nd Gen",
                help="eBay/Shopee の商品タイトルに使用（80文字以内推奨）",
            )
            condition_val = en_c2.selectbox(
                "コンディション",
                ["New", "New (Open Box)", "Like New", "Very Good", "Good", "Acceptable"],
                index=0,
            )
            product_description_en_val = st.text_area(
                "英語商品説明",
                placeholder="Describe the product in English. Used for eBay/Shopee listings.",
                height=100,
                help="eBay の Description・Shopee の Product Description に使用",
            )

            # Shopee カテゴリ
            st.subheader("🟧 Shopee 設定（CSVアップロード用）")
            sh_c1, sh_c2 = st.columns(2)
            _shopee_cat_opts = list(SHOPEE_CATEGORIES.keys())
            shopee_category_name_val = sh_c1.selectbox(
                "Shopee カテゴリ",
                _shopee_cat_opts,
                index=0,
                help="CSVエクスポート時のカテゴリIDに使用されます",
            )
            shopee_category_id_val = SHOPEE_CATEGORIES.get(shopee_category_name_val, 100599)
            sh_c2.metric("カテゴリID", shopee_category_id_val)

            # 画像URL（複数）
            st.subheader("🖼️ 画像（最大9枚）")
            st.caption("eBay/Shopee で表示される画像。公開アクセス可能なURLを入力してください。")
            img_rows = []
            for _i in range(0, 9, 3):
                _img_cols = st.columns(3)
                for _j, _col in enumerate(_img_cols):
                    _idx = _i + _j + 1
                    _url = _col.text_input(
                        f"画像 {_idx}",
                        placeholder="https://...",
                        key=f"img_url_{_idx}",
                        label_visibility="visible",
                    )
                    img_rows.append(_url)
            image_urls_val = [u for u in img_rows if u.strip()]

            # 詳細（任意）
            with st.expander("📝 詳細情報（任意）"):
                desc_val   = st.text_area("日本語商品説明", placeholder="内部管理用。英語説明と別に持てます。")
                notes_val  = st.text_area("内部メモ", placeholder="仕入れ先のメモなど")

            submitted = st.form_submit_button("✅ 登録する", type="primary", use_container_width=True)

    # ────── 右カラム: 利益計算プレビュー ──────
    with right:
        st.subheader("💰 利益計算プレビュー")

        # フォーム外のリアルタイム計算用（session_state で最後の入力値を保持）
        preview_cost     = st.session_state.get("preview_cost", 0.0)
        preview_weight   = st.session_state.get("preview_weight", 500.0)
        preview_sl       = st.session_state.get("preview_sl", 0.0)
        preview_sw       = st.session_state.get("preview_sw", 0.0)
        preview_sh       = st.session_state.get("preview_sh", 0.0)
        preview_cat      = st.session_state.get("preview_cat", "electronics")
        preview_countries= st.session_state.get("preview_countries", ["USA"])
        preview_profit   = st.session_state.get("preview_profit", 0.25)

        st.caption("💡 フォームに入力後「計算プレビュー更新」で反映されます")

        with st.form("preview_form"):
            prev_cost_in     = st.number_input("仕入れ値（円）", value=float(preview_cost), step=100.0, key="prev_cost")
            prev_weight_in   = st.number_input("重量 (g)", value=float(preview_weight), step=10.0, key="prev_w")
            prev_cat_in      = st.selectbox(
                "カテゴリ",
                [c.value for c in ProductCategory],
                format_func=lambda v: CATEGORY_LABELS.get(ProductCategory(v), v),
                key="prev_cat_sel",
            )
            prev_countries_in = st.multiselect(
                "販売先国",
                options=list(COUNTRY_OPTIONS.keys()),
                format_func=lambda v: COUNTRY_OPTIONS.get(v, v),
                default=preview_countries,
                key="prev_ctry",
            )
            prev_profit_in = st.slider("希望利益率", 10, 50, int(preview_profit * 100), step=5, format="%d%%", key="prev_profit_sl") / 100
            calc_btn = st.form_submit_button("🔄 計算プレビュー更新", use_container_width=True)

        if calc_btn:
            st.session_state.update({
                "preview_cost": prev_cost_in,
                "preview_weight": prev_weight_in,
                "preview_cat": prev_cat_in,
                "preview_countries": prev_countries_in,
                "preview_profit": prev_profit_in,
            })
            preview_cost      = prev_cost_in
            preview_weight    = prev_weight_in
            preview_cat       = prev_cat_in
            preview_countries = prev_countries_in
            preview_profit    = prev_profit_in

        if preview_cost > 0 and preview_countries:
            calc = calc_profit_preview(
                preview_cost, preview_weight, preview_sl, preview_sw, preview_sh,
                preview_cat, preview_countries,
                preview_profit, usd_rate, sgd_rate,
                ebay_fee, shopee_fee, payment_fee, fx_fee,
            )

            if calc:
                dom_ship = calc.get("domestic_ship", 0)
                st.markdown(f"**国内送料（推定）:** ¥{dom_ship:,.0f}")

                for code, r in calc.get("countries", {}).items():
                    with st.expander(f"{'🇺🇸' if r['is_ebay'] else '🌏'} {r['country']}（{r['marketplace']}）", expanded=True):
                        rows = [
                            ("仕入れ値",      f"¥{r['cost']:,.0f}"),
                            ("国内送料",      f"¥{r['domestic_ship']:,.0f}"),
                            ("国際送料(EMS)", f"¥{r['intl_ship']:,.0f}"),
                            ("関税",          f"¥{r['tariff']:,.0f}"),
                            ("総コスト",      f"¥{r['total_cost']:,.0f}"),
                            ("手数料率",      f"{r['fee_rate']*100:.1f}%"),
                        ]
                        for label, val in rows:
                            cc1, cc2 = st.columns([2, 1])
                            cc1.write(label)
                            cc2.write(val)
                        st.divider()
                        price_usd = r["price_usd"]
                        price_sgd = r["price_sgd"]
                        profit    = r["profit_jpy"]
                        st.markdown(f"**推奨価格: ${price_usd:,.2f} USD / S${price_sgd:,.2f} SGD**")
                        st.markdown(f"**見込み利益: ¥{profit:,.0f}**")

                if calc.get("avg_price_usd"):
                    st.success(f"✅ eBay 平均推奨価格: **${calc['avg_price_usd']:,.2f} USD**")
                if calc.get("avg_price_sgd"):
                    st.success(f"✅ Shopee 平均推奨価格: **S${calc['avg_price_sgd']:,.2f} SGD**")
        else:
            st.info("仕入れ値と販売先国を入力して「計算プレビュー更新」を押してください。")

            # 手数料設定の表示
            with st.expander("⚙️ 手数料設定（現在値）", expanded=False):
                st.write(f"eBay手数料: {ebay_fee*100:.1f}%")
                st.write(f"Shopee手数料: {shopee_fee*100:.1f}%")
                st.write(f"決済手数料: {payment_fee*100:.1f}%")
                st.write(f"為替手数料: {fx_fee*100:.1f}%")
                st.write(f"USD レート: {usd_rate}")
                st.write(f"SGD レート: {sgd_rate}")
                st.caption("変更は「⚙️ 設定」から")

    # ────── フォーム送信処理 ──────
    if submitted:
        errors = []
        if not name:   errors.append("商品名")
        if not sku:    errors.append("SKU")
        if not errors:
            try:
                # 利益計算で推奨価格を確定
                calc_prices = {}
                if cost_price_val > 0 and target_countries_val:
                    calc_prices = calc_profit_preview(
                        cost_price_val, weight_g_val or 500,
                        size_l_val, size_w_val, size_h_val,
                        category_val, target_countries_val,
                        profit_rate_val, usd_rate, sgd_rate,
                        ebay_fee, shopee_fee, payment_fee, fx_fee,
                    )

                avg_usd = calc_prices.get("avg_price_usd")
                avg_sgd = calc_prices.get("avg_price_sgd")

                # 国内送料
                dom_shipping = calculate_domestic_shipping(
                    weight_g_val or 500, size_l_val, size_w_val, size_h_val
                ).fee_jpy

                # メイン画像URL（後方互換）= 先頭の画像
                _main_img = image_urls_val[0] if image_urls_val else None

                with get_session() as sess:
                    prod = Product(
                        name=name, sku=sku,
                        source_site=SourceSite(source_site_val),
                        source_url=source_url_val or None,
                        asin=asin_val or None,
                        rakuten_item_code=rakuten_val or None,
                        yahoo_item_code=yahoo_val or None,
                        netsea_product_id=netsea_val or None,
                        jan_code=jan_val or None,
                        upc_code=upc_val or None,
                        cost_price=cost_price_val,
                        selling_price_usd=avg_usd,
                        selling_price_sgd=avg_sgd,
                        calc_selling_price_usd=avg_usd,
                        calc_selling_price_sgd=avg_sgd,
                        markup_rate=1.0 + profit_rate_val,
                        target_profit_rate=profit_rate_val,
                        current_stock=int(current_stock_val),
                        target_ebay=target_ebay_val,
                        target_shopee=target_shopee_val,
                        target_countries=target_countries_val or None,
                        product_category=ProductCategory(category_val),
                        hs_code=hs_code_val or None,
                        weight_g=weight_g_val if weight_g_val > 0 else None,
                        size_cm_l=size_l_val if size_l_val > 0 else None,
                        size_cm_w=size_w_val if size_w_val > 0 else None,
                        size_cm_h=size_h_val if size_h_val > 0 else None,
                        domestic_shipping_cost=dom_shipping,
                        description=desc_val or None,
                        image_url=_main_img,
                        image_urls=image_urls_val if image_urls_val else None,
                        notes=notes_val or None,
                        status=ProductStatus(status_val),
                        ebay_fee_rate=ebay_fee,
                        shopee_fee_rate=shopee_fee,
                        payment_fee_rate=payment_fee,
                        # 新フィールド
                        product_name_en=product_name_en_val or None,
                        product_description_en=product_description_en_val or None,
                        shopee_category_id=shopee_category_id_val,
                        condition=condition_val,
                    )
                    sess.add(prod)
                    sess.commit()

                st.success(f"✅ 「{name}」を登録しました！（SKU: {sku}）")
                if avg_usd: st.info(f"💵 eBay 推奨価格: **${avg_usd:,.2f} USD**")
                if avg_sgd: st.info(f"💵 Shopee 推奨価格: **S${avg_sgd:,.2f} SGD**")
                st.balloons()
            except Exception as e:
                if "UNIQUE constraint" in str(e):
                    st.error(f"SKU「{sku}」は既に使用されています。")
                else:
                    st.error(f"登録エラー: {e}")
        else:
            st.error(f"必須項目が未入力です: {', '.join(errors)}")


# ══════════════════════════════════════════════════════════════════
#  PAGE: 出品管理
# ══════════════════════════════════════════════════════════════════
elif page == "🚀 出品管理":
    st.title("🚀 出品管理")

    tab1, tab2 = st.tabs(["🟦 eBay", "🟧 Shopee"])

    # ─── eBay タブ ───
    with tab1:
        st.subheader("eBay 出品状況")

        import importlib
        import backend.marketplaces.ebay as _ebay_mod
        importlib.reload(_ebay_mod)
        _ebay = _ebay_mod.EbayClient()

        # API設定チェック
        if not _ebay.is_configured():
            st.warning("⚠️ eBay APIキーが未設定です。「⚙️ 設定」→「🟦 eBay API」で入力してください。")
        else:
            st.success("✅ eBay API設定済み")

        with get_session() as s:
            ebay_listed   = s.query(Product).filter(Product.ebay_listing_id.isnot(None)).all()
            ebay_pending  = s.query(Product).filter(
                Product.target_ebay == True,
                Product.ebay_listing_id.is_(None),
            ).all()

        _usd_rate = float(get_env("DEFAULT_EXCHANGE_RATE_USD", "150"))
        col_l, col_r = st.columns(2)
        col_l.metric("DB内: 出品済", len(ebay_listed))
        col_r.metric("未出品（eBay対象）", len(ebay_pending))

        # ── eBay から実際の出品リストを取得 ──
        st.divider()
        if st.button("🔄 eBay出品リストを取得", key="fetch_ebay_list"):
            if not _ebay.is_configured():
                st.error("APIキーを設定してください")
            else:
                with st.spinner("eBay APIから出品中リストを取得中..."):
                    live_listings, err = _ebay.get_active_listings()
                if err:
                    st.error(f"取得失敗: {err}")
                else:
                    st.session_state["ebay_live_listings"] = live_listings
                    st.success(f"eBay出品中: {len(live_listings)} 件を取得しました")

        if st.session_state.get("ebay_live_listings"):
            live = st.session_state["ebay_live_listings"]
            st.markdown(f"#### 📋 eBay出品中 ({len(live)}件) — リアルタイム")
            live_rows = []
            for item in live:
                live_rows.append({
                    "Item ID": item.item_id,
                    "タイトル": item.title[:45] + "…" if len(item.title) > 45 else item.title,
                    "価格(USD)": f"${item.price_usd:,.2f}",
                    "在庫": item.quantity,
                    "売済": item.quantity_sold,
                    "出品終了": item.end_time or "GTC",
                    "URL": item.url,
                })
            live_df = pd.DataFrame(live_rows)
            st.dataframe(live_df, use_container_width=True, hide_index=True)

            # 個別操作
            with st.expander("⚙️ 個別操作（価格更新・出品停止）"):
                _op_item_id = st.text_input("操作する Item ID", placeholder="123456789012")
                _op_cols = st.columns(3)
                _new_price = _op_cols[0].number_input("新しい価格 (USD)", min_value=0.01, step=0.5, value=10.0)
                if _op_cols[1].button("💰 価格更新", key="ebay_update_price"):
                    if _op_item_id:
                        with st.spinner("更新中..."):
                            r = _ebay.update_price(_op_item_id, _new_price)
                        if r.success:
                            st.success(f"✅ Item {_op_item_id} の価格を ${_new_price:.2f} に更新しました")
                            # DB更新
                            with get_session() as s:
                                p = s.query(Product).filter(Product.ebay_listing_id == _op_item_id).first()
                                if p:
                                    p.selling_price_usd = _new_price
                                    s.commit()
                        else:
                            st.error(f"更新失敗: {r.error}")
                if _op_cols[2].button("🛑 出品停止", key="ebay_end_item"):
                    if _op_item_id:
                        with st.spinner("停止処理中..."):
                            r = _ebay.end_listing(_op_item_id)
                        if r.success:
                            st.success(f"✅ Item {_op_item_id} の出品を停止しました")
                            with get_session() as s:
                                p = s.query(Product).filter(Product.ebay_listing_id == _op_item_id).first()
                                if p:
                                    p.ebay_listing_id = None
                                    p.status = ProductStatus.DRAFT
                                    s.commit()
                        else:
                            st.error(f"停止失敗: {r.error}")

        # ── DB内の出品済み商品 ──
        if ebay_listed:
            st.divider()
            st.markdown("#### 🗄️ DB内: 出品済み商品")
            rows = []
            for p in ebay_listed:
                rows.append({
                    "SKU": p.sku,
                    "商品名": p.name[:40],
                    "listing_id": p.ebay_listing_id,
                    "推奨USD": f"${p.calc_selling_price_usd:,.2f}" if p.calc_selling_price_usd else "—",
                    "在庫": p.current_stock,
                    "ステータス": STATUS_LABELS.get(p.status, str(p.status)),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # ── 未出品商品 → 出品ボタン ──
        if ebay_pending:
            st.divider()
            st.markdown("#### 📤 未出品（eBay対象）商品")
            for p in ebay_pending:
                with st.container(border=True):
                    ec1, ec2, ec3, ec4 = st.columns([3, 1.5, 1.5, 1])
                    ec1.markdown(f"**{p.name[:38]}**  \n`{p.sku}`")
                    _price_default = round(float(p.calc_selling_price_usd or (p.cost_price / _usd_rate * 1.3)), 2)
                    _price_key = f"pending_price_{p.id}"
                    if _price_key not in st.session_state:
                        st.session_state[_price_key] = _price_default
                    _price_show = ec2.number_input(
                        "価格(USD)", min_value=0.01, step=0.5,
                        value=st.session_state[_price_key],
                        key=_price_key, label_visibility="visible",
                    )
                    ec3.metric("在庫", p.current_stock)
                    if ec4.button("🚀 出品", key=f"ebay_list_{p.id}", type="primary"):
                        if not _ebay.is_configured():
                            st.error("APIキーを設定してください")
                        else:
                            with st.spinner(f"「{p.name[:20]}」をeBayに出品中..."):
                                result = _ebay.create_listing(p, _price_show)
                            if result.success:
                                with get_session() as s:
                                    _p = s.query(Product).filter(Product.id == p.id).first()
                                    _p.ebay_listing_id = result.listing_id
                                    _p.selling_price_usd = _price_show
                                    _p.status = ProductStatus.ACTIVE
                                    s.commit()
                                st.success(f"✅ 出品完了！ Item ID: `{result.listing_id}`")
                                if result.url:
                                    st.markdown(f"🔗 [eBayで確認する]({result.url})")
                                if result.fees is not None:
                                    st.caption(f"出品手数料: ${result.fees:.2f} USD")
                                st.rerun()
                            else:
                                st.error(f"出品失敗: {result.error}")

        # 接続テスト
        st.divider()
        with st.expander("🔧 eBay API 接続テスト"):
            if st.button("接続テスト実行", key="ebay_test_mgmt"):
                with st.spinner("接続テスト中..."):
                    ok, msg = _ebay.test_connection()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    # ─── Shopee タブ ───
    with tab2:
        st.subheader("Shopee 出品状況")

        with get_session() as s:
            shopee_listed  = s.query(Product).filter(Product.shopee_item_id.isnot(None)).all()
            shopee_pending = s.query(Product).filter(
                Product.target_shopee == True,
                Product.shopee_item_id.is_(None),
            ).all()

        col_l, col_r = st.columns(2)
        col_l.metric("出品中", len(shopee_listed))
        col_r.metric("未出品（対象）", len(shopee_pending))

        if shopee_listed:
            st.markdown("#### 出品中の商品")
            rows = []
            for p in shopee_listed:
                rows.append({
                    "SKU": p.sku, "商品名": p.name[:40],
                    "item_id": p.shopee_item_id,
                    "推奨SGD": f"S${p.calc_selling_price_sgd:,.2f}" if p.calc_selling_price_sgd else "—",
                    "在庫": p.current_stock,
                    "ステータス": STATUS_LABELS.get(p.status, str(p.status)),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if shopee_pending:
            st.markdown("#### 未出品（Shopee対象）商品")
            for p in shopee_pending:
                sc1, sc2, sc3 = st.columns([3, 1, 1])
                sc1.write(f"**{p.name[:40]}** ({p.sku})")
                if sc3.button("出品", key=f"shopee_list_{p.id}"):
                    st.session_state["listing_target"] = (p.id, "shopee")
                    st.session_state["show_listing_modal"] = True
                    st.rerun()

        with st.expander("🔧 Shopee API 接続テスト"):
            if st.button("接続テスト実行", key="shopee_test"):
                from backend.marketplaces.shopee import get_shopee_client
                client = get_shopee_client()
                ok, msg = client.test_connection()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    # ─── 出品実行モーダル（出品管理タブ内）───
    if st.session_state.get("show_listing_modal") and st.session_state.get("listing_target"):
        pid, platform = st.session_state["listing_target"]
        st.divider()
        with st.container(border=True):
            st.subheader(f"🚀 {'eBay' if platform == 'ebay' else 'Shopee'} 出品実行")
            with get_session() as s:
                p = s.query(Product).filter(Product.id == pid).first()
            if p:
                st.write(f"**{p.name}** ({p.sku}) / 在庫: {p.current_stock}")
                if platform == "ebay":
                    price_default = p.calc_selling_price_usd or round(float(p.cost_price) / 150 * 1.5, 2)
                    price_in = st.number_input("販売価格 (USD)", value=round(float(price_default), 2), step=0.5, key="modal_price")
                else:
                    price_default = p.calc_selling_price_sgd or round(float(p.cost_price) / 112 * 1.5, 2)
                    price_in = st.number_input("販売価格 (SGD)", value=round(float(price_default), 2), step=0.5, key="modal_price_sgd")

                btn_ok, btn_cancel = st.columns(2)
                if btn_ok.button("✅ 出品する", type="primary", key="modal_ok"):
                    with st.spinner("出品処理中..."):
                        try:
                            if platform == "ebay":
                                from backend.marketplaces.ebay import get_ebay_client
                                client = get_ebay_client()
                                if not client.is_configured():
                                    st.error("eBay APIキーが未設定です（設定画面へ）")
                                else:
                                    result = client.create_listing(p, price_in)
                                    if result.success:
                                        with get_session() as s2:
                                            pp = s2.query(Product).filter(Product.id == pid).first()
                                            pp.ebay_listing_id = result.listing_id
                                            pp.selling_price_usd = price_in
                                            pp.status = ProductStatus.ACTIVE
                                            s2.commit()
                                        st.success(f"✅ eBay 出品完了！ ID: {result.listing_id}")
                                        if result.url:
                                            st.write(f"[出品ページ]({result.url})")
                                        st.session_state["show_listing_modal"] = False
                                    else:
                                        st.error(f"エラー: {result.error}")
                            else:
                                from backend.marketplaces.shopee import get_shopee_client
                                client = get_shopee_client()
                                if not client.is_configured():
                                    st.error("Shopee APIキーが未設定です（設定画面へ）")
                                else:
                                    result = client.add_item(p, price_in)
                                    if result.success:
                                        with get_session() as s2:
                                            pp = s2.query(Product).filter(Product.id == pid).first()
                                            pp.shopee_item_id = str(result.item_id)
                                            pp.selling_price_sgd = price_in
                                            pp.status = ProductStatus.ACTIVE
                                            s2.commit()
                                        st.success(f"✅ Shopee 出品完了！ item_id: {result.item_id}")
                                        st.session_state["show_listing_modal"] = False
                                    else:
                                        st.error(f"エラー: {result.error}")
                        except Exception as ex:
                            st.error(f"例外エラー: {ex}")

                if btn_cancel.button("キャンセル", key="modal_cancel"):
                    st.session_state["show_listing_modal"] = False
                    st.session_state["listing_target"] = None
                    st.rerun()


# ══════════════════════════════════════════════════════════════════
#  PAGE: 設定
# ══════════════════════════════════════════════════════════════════
elif page == "⚙️ 設定":
    st.title("⚙️ 設定")
    st.caption("入力値は .env ファイルに保存されます。")

    tab_ebay, tab_shopee, tab_price, tab_misc = st.tabs(
        ["🟦 eBay API", "🟧 Shopee API", "💴 価格・手数料", "🔧 その他"]
    )

    # ─── eBay API設定 ───
    with tab_ebay:
        st.subheader("eBay Trading API キー")
        st.markdown(
            "取得先: [eBay Developer Portal](https://developer.ebay.com/my/keys)  |  "
            "トークン: Developer Portal → **Get a User Token**"
        )

        # 現在の設定状況を表示
        _ebay_fields = {
            "EBAY_APP_ID": get_env("EBAY_APP_ID"),
            "EBAY_DEV_ID": get_env("EBAY_DEV_ID"),
            "EBAY_CERT_ID": get_env("EBAY_CERT_ID"),
            "EBAY_USER_TOKEN": get_env("EBAY_USER_TOKEN"),
        }
        _all_set = all(_ebay_fields.values())
        if _all_set:
            st.success("✅ 全APIキーが設定済みです")
        else:
            _missing = [k for k, v in _ebay_fields.items() if not v]
            st.warning(f"⚠️ 未設定: {', '.join(_missing)}")

        with st.form("ebay_form"):
            e1 = st.text_input("EBAY_APP_ID",   value=get_env("EBAY_APP_ID"),  type="password")
            e2 = st.text_input("EBAY_DEV_ID",   value=get_env("EBAY_DEV_ID"),  type="password")
            e3 = st.text_input("EBAY_CERT_ID",  value=get_env("EBAY_CERT_ID"), type="password")
            e4 = st.text_area(
                "EBAY_USER_TOKEN（User Token）",
                value=get_env("EBAY_USER_TOKEN"),
                height=100,
                help="Developer Portal の「Get a User Token」で取得。v^1.1#i^1#... から始まる文字列",
            )
            e_site = st.selectbox(
                "EBAY_SITE_ID（販売市場）",
                options=["0 (US)", "3 (UK)", "77 (DE)", "15 (AU)", "193 (CH)"],
                index=["0 (US)", "3 (UK)", "77 (DE)", "15 (AU)", "193 (CH)"].index(
                    f"{get_env('EBAY_SITE_ID','0')} (US)" if get_env("EBAY_SITE_ID","0") == "0"
                    else f"{get_env('EBAY_SITE_ID','0')} (UK)" if get_env("EBAY_SITE_ID","0") == "3"
                    else "0 (US)"
                ) if get_env("EBAY_SITE_ID","0") in ["0","3"] else 0,
            )
            e_sandbox = st.checkbox("🧪 サンドボックスモード", value=get_env("EBAY_SANDBOX","false")=="true")

            if st.form_submit_button("💾 eBay設定を保存", type="primary"):
                site_num = e_site.split(" ")[0]
                for k, v in [
                    ("EBAY_APP_ID", e1), ("EBAY_DEV_ID", e2),
                    ("EBAY_CERT_ID", e3), ("EBAY_USER_TOKEN", e4),
                    ("EBAY_SITE_ID", site_num),
                    ("EBAY_SANDBOX", "true" if e_sandbox else "false"),
                ]:
                    if v:
                        save_env(k, v)
                st.success("✅ eBay設定を保存しました（.env 更新済み）")
                st.rerun()

        # トークン診断
        _cur_token = get_env("EBAY_USER_TOKEN")
        if _cur_token:
            _tlen = len(_cur_token)
            if _tlen < 200:
                st.error(
                    f"⚠️ **トークンが短すぎます（現在 {_tlen} 文字 / 正常: 350〜500文字）**\n\n"
                    "チャットへの貼り付け時に途中で切断された可能性があります。\n"
                    "下記の手順でトークンを再取得・再入力してください。"
                )
                with st.expander("📋 eBay User Token の再取得手順", expanded=True):
                    st.markdown("""
1. [eBay Developer Portal](https://developer.ebay.com/my/auth/?env=production&index=0) を開く
2. 上部メニュー **Hi, [name] → User Tokens** をクリック
3. **Generate a User Token** ボタンをクリック
4. eBayアカウントでログイン・許可
5. 表示された **Auth Token** を全文コピー
   - `v^1.1#i^1#r^1#...`  で始まる **350〜500文字** の文字列
6. 上の入力欄「EBAY_USER_TOKEN」にペーストして **💾 保存**
7. 接続テストで確認
""")
            elif _tlen < 300:
                st.warning(f"⚠️ トークン長 {_tlen} 文字（やや短め。接続テストで確認してください）")
            else:
                st.info(f"✅ トークン長: {_tlen} 文字（正常範囲）")

        st.divider()
        st.markdown("**🔌 接続テスト（GetUser）**")
        st.caption("eBay Trading API の GetUser を呼び出してアカウント情報を確認します")
        if st.button("eBay API 接続テスト実行", type="primary", key="ebay_conn_test"):
            with st.spinner("eBay APIに接続中..."):
                import importlib
                import backend.marketplaces.ebay as ebay_mod
                importlib.reload(ebay_mod)
                client = ebay_mod.EbayClient()
                ok, msg = client.test_connection()
            if ok:
                st.success(msg)
            else:
                st.error(msg)
                if "931" in msg:
                    st.warning(
                        "**エラー931: トークン認証失敗**\n\n"
                        "考えられる原因:\n"
                        "1. トークンが短すぎる/途中で切れている → 上で確認\n"
                        "2. トークンの有効期限切れ（18ヶ月） → 再取得が必要\n"
                        "3. APP_ID/CERT_ID がトークンと一致しない"
                    )

    # ─── Shopee API設定 ───
    with tab_shopee:
        st.subheader("Shopee Open Platform API")
        st.caption("Shopee Partner Portal で取得: https://open.shopee.com")

        with st.form("shopee_form"):
            s1 = st.text_input("SHOPEE_PARTNER_ID",    value=get_env("SHOPEE_PARTNER_ID"))
            s2 = st.text_input("SHOPEE_PARTNER_KEY",   value=get_env("SHOPEE_PARTNER_KEY"), type="password")
            s3 = st.text_input("SHOPEE_SHOP_ID",       value=get_env("SHOPEE_SHOP_ID"))
            s4 = st.text_area("SHOPEE_ACCESS_TOKEN",   value=get_env("SHOPEE_ACCESS_TOKEN"), height=80)
            s_sandbox = st.checkbox("サンドボックスモード", value=get_env("SHOPEE_SANDBOX","false")=="true")

            if st.form_submit_button("💾 Shopee設定を保存", type="primary"):
                for k, v in [
                    ("SHOPEE_PARTNER_ID", s1), ("SHOPEE_PARTNER_KEY", s2),
                    ("SHOPEE_SHOP_ID", s3), ("SHOPEE_ACCESS_TOKEN", s4),
                    ("SHOPEE_SANDBOX", "true" if s_sandbox else "false"),
                ]:
                    if v: save_env(k, v)
                st.success("✅ Shopee設定を保存しました（.env 更新済み）")
                st.rerun()

        st.divider()
        if st.button("🔌 Shopee接続テスト"):
            from backend.marketplaces.shopee import ShopeeClient
            import importlib, backend.marketplaces.shopee as shopee_mod
            importlib.reload(shopee_mod)
            client = shopee_mod.ShopeeClient()
            ok, msg = client.test_connection()
            st.success(msg) if ok else st.error(msg)

    # ─── 価格・手数料設定 ───
    with tab_price:
        st.subheader("価格・為替・手数料設定")

        with st.form("price_form"):
            pc1, pc2 = st.columns(2)
            usd = pc1.number_input("USD レート（円）", value=float(get_env("DEFAULT_EXCHANGE_RATE_USD","150")), step=1.0)
            sgd = pc2.number_input("SGD レート（円）", value=float(get_env("DEFAULT_EXCHANGE_RATE_SGD","112")), step=1.0)

            st.markdown("**手数料率**")
            fc1, fc2, fc3, fc4 = st.columns(4)
            ebay_f  = fc1.number_input("eBay手数料 (%)",    value=float(get_env("EBAY_FEE_RATE","0.13"))*100, step=0.5, min_value=0.0, max_value=30.0) / 100
            shop_f  = fc2.number_input("Shopee手数料 (%)",  value=float(get_env("SHOPEE_FEE_RATE","0.06"))*100, step=0.5, min_value=0.0, max_value=30.0) / 100
            pay_f   = fc3.number_input("決済手数料 (%)",     value=float(get_env("PAYMENT_FEE_RATE","0.044"))*100, step=0.1, min_value=0.0, max_value=10.0) / 100
            fx_f    = fc4.number_input("為替手数料 (%)",     value=float(get_env("FX_FEE_RATE","0.02"))*100, step=0.1, min_value=0.0, max_value=5.0) / 100

            if st.form_submit_button("💾 価格設定を保存", type="primary"):
                for k, v in [
                    ("DEFAULT_EXCHANGE_RATE_USD", str(usd)),
                    ("DEFAULT_EXCHANGE_RATE_SGD", str(sgd)),
                    ("EBAY_FEE_RATE", str(ebay_f)),
                    ("SHOPEE_FEE_RATE", str(shop_f)),
                    ("PAYMENT_FEE_RATE", str(pay_f)),
                    ("FX_FEE_RATE", str(fx_f)),
                ]:
                    save_env(k, v)
                st.success("✅ 価格設定を保存しました")
                st.rerun()

    # ─── その他 ───
    with tab_misc:
        st.subheader("仕入れAPI設定")
        with st.form("misc_form"):
            mc1, mc2 = st.columns(2)
            rak_id  = mc1.text_input("楽天 APP_ID",       value=get_env("RAKUTEN_APP_ID"))
            yah_id  = mc2.text_input("Yahoo CLIENT_ID",   value=get_env("YAHOO_CLIENT_ID"))
            amz_id  = mc1.text_input("Amazon CLIENT_ID",  value=get_env("AMAZON_CLIENT_ID"))
            net_em  = mc2.text_input("NETSEA EMAIL",       value=get_env("NETSEA_EMAIL"))

            if st.form_submit_button("💾 保存"):
                for k, v in [
                    ("RAKUTEN_APP_ID", rak_id), ("YAHOO_CLIENT_ID", yah_id),
                    ("AMAZON_CLIENT_ID", amz_id), ("NETSEA_EMAIL", net_em),
                ]:
                    if v: save_env(k, v)
                st.success("✅ 保存しました")

        st.divider()
        st.subheader("DB情報")
        with get_session() as s:
            total = s.query(Product).count()
        st.write(f"登録商品数: {total} 件")
        st.write(f"DB パス: {Path(__file__).parent.parent / 'globalbiz.db'}")
