"""GlobalBiz 管理画面 v5
追加機能: ページネーション / 為替レートキャッシュ / 利益計算キャッシュ / SQLiteインデックス
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import time
import json
import base64
import importlib
from typing import Optional
import pandas as pd
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv, set_key

from backend.db.database import init_db, get_session
from backend.db.models import Product, ProductStatus, SourceSite, ProductCategory, SizeClass
from backend.calculators.tariff import (
    calculate_tariff, COUNTRY_NAMES, EBAY_COUNTRIES, SHOPEE_COUNTRIES,
)
from backend.calculators import tariff_v2 as _tariff_v2
from backend.calculators.shipping import (
    calculate_domestic_shipping, calculate_international_shipping,
    DomesticCarrier, IntlCarrier,
)
from backend.exporters.shopee_csv import (
    export_shopee_csv, export_shopee_zip,
    get_shopee_category_options, SHOPEE_CATEGORIES,
    SHOPEE_COUNTRY_CONFIG,
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
.warn-red { color:#d32f2f; font-weight:bold; }
.tag-ebay  { background:#e53935; color:white; border-radius:4px; padding:2px 6px; font-size:0.75rem; }
.tag-shopee{ background:#EE4D2D; color:white; border-radius:4px; padding:2px 6px; font-size:0.75rem; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════
#  パスワード認証
# ══════════════════════════════════════════════════════════════════

def _check_auth() -> None:
    """未認証ならパスワード入力画面を表示し、st.stop() でアプリ本体の描画を止める。"""
    if st.session_state.get("authenticated"):
        return

    # ── ログイン画面 ─────────────────────────────────────────
    col_l, col_c, col_r = st.columns([1, 1, 1])
    with col_c:
        st.markdown("## 🔐 GlobalBiz")
        st.caption("越境EC運営ツール — アクセスにはパスワードが必要です")
        st.divider()

        with st.form("login_form", clear_on_submit=True):
            pw = st.text_input(
                "パスワード",
                type="password",
                placeholder="パスワードを入力してください",
                label_visibility="collapsed",
            )
            login_btn = st.form_submit_button("🔑 ログイン", use_container_width=True, type="primary")

        if login_btn:
            try:
                correct = st.secrets["auth"]["password"]
            except Exception:
                st.error(
                    "⚠️ `.streamlit/secrets.toml` が設定されていません。\n\n"
                    "`secrets.toml.example` を参考に作成してください。"
                )
                st.stop()

            if pw == correct:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("❌ パスワードが違います。再度お試しください。")

    st.stop()


_check_auth()


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
    
     ProductCategory.COSMETICS:   "💄 化粧品・美容",
     ProductCategory.HEALTH:      "💊 健康・医療",
     ProductCategory.CLOTHING:    "👗 衣類・アパレル",
    ProductCategory.ACCESSORIES: "アクセサリー",
     ProductCategory.HOME:        "🏠 ホーム・家具・インテリア",
     ProductCategory.TOYS:        "🧸 おもちゃ・ゲーム",
     ProductCategory.ELECTRONICS: "📱 家電・電子機器",
     ProductCategory.SPORTS:      "⚽ スポーツ・アウトドア",
     ProductCategory.BOOKS:       "📚 本・メディア",
     ProductCategory.TOOLS:       "🔧 工具・DIY・刃物",
     ProductCategory.HOBBY:       "🎨 ホビー・コレクション",
     ProductCategory.PETS:        "🐾 ペット用品",
     ProductCategory.AUTO:        "🚗 自動車・バイク用品・自転車",
     ProductCategory.FOOD:        "🍱 食品・飲料",
     ProductCategory.OTHER:       "📦 その他",

}

# カテゴリ → 代表的な HS コード
HS_CODE_DEFAULTS: dict = {
    "electronics": "8518.30",
    "clothing":    "6109.10",
    "accessories": "7117.19",
    "toys":        "9503.00",
    "food":        "2106.90",
    "cosmetics":   "3304.99",
    "health":      "3004.90",
    "sports":      "9506.99",
    "home":        "9403.20",
    "books":       "4901.99",
    "tools":       "8467.29",
    "hobby":       "9505.90",
    "pets":        "9508.90",
    "auto":        "8708.99",
    "other":       "",
}

# 全国オプション（重複除去）
_seen: set = set()
COUNTRY_OPTIONS: dict = {}
for _c in EBAY_COUNTRIES + SHOPEE_COUNTRIES:
    if _c not in _seen and _c in COUNTRY_NAMES:
        _seen.add(_c)
        COUNTRY_OPTIONS[_c] = f"{'🇺🇸' if _c in EBAY_COUNTRIES else '🌏'} {COUNTRY_NAMES[_c]}"

ENV_PATH = Path(__file__).parent.parent / ".env"

# ─── キャッシュ ────────────────────────────────────────────────────
# 為替レートキャッシュ（1時間有効）
_RATE_CACHE: dict = {"data": None, "ts": 0.0}
# 利益計算キャッシュ  key=(product.id, updated_at_str) → result dict
_PROFIT_CACHE: dict = {}

# ページネーション
PAGE_SIZE = 50

# ── No Image プレースホルダー（base64 SVG）──
_NO_IMG_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60">'
    '<rect width="60" height="60" fill="#EEEEEE" rx="6"/>'
    '<text x="50%" y="44%" text-anchor="middle" dominant-baseline="middle"'
    ' font-size="22" font-family="sans-serif">📷</text>'
    '<text x="50%" y="76%" text-anchor="middle" dominant-baseline="middle"'
    ' font-size="9" font-family="sans-serif" fill="#AAAAAA">No Image</text>'
    "</svg>"
)
NO_IMAGE_PLACEHOLDER: str = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(_NO_IMG_SVG.encode()).decode()
)

# ── カテゴリ別利益率の env キー ──
CATEGORY_PROFIT_KEYS = {
    "electronics": "PROFIT_RATE_ELECTRONICS",
    "clothing":    "PROFIT_RATE_CLOTHING",
    "accessories": "PROFIT_RATE_ACCESSORIES",
    "toys":        "PROFIT_RATE_TOYS",
    "food":        "PROFIT_RATE_FOOD",
    "cosmetics":   "PROFIT_RATE_COSMETICS",
    "health":      "PROFIT_RATE_HEALTH",
    "sports":      "PROFIT_RATE_SPORTS",
    "home":        "PROFIT_RATE_HOME",
    "books":       "PROFIT_RATE_BOOKS",
    "tools":       "PROFIT_RATE_TOOLS",
    "hobby":       "PROFIT_RATE_HOBBY",
    "pets":        "PROFIT_RATE_PETS",
    "auto":        "PROFIT_RATE_AUTO",
    "other":       "PROFIT_RATE_OTHER",
}

# ─────────────────────────── ヘルパー ────────────────────────────
def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def save_env(key: str, value: str) -> None:
    env_file = str(ENV_PATH)
    if not ENV_PATH.exists():
        ENV_PATH.write_text("")
    set_key(env_file, key, value)
    os.environ[key] = value
    # 為替レートに関する設定が変わったらキャッシュを無効化
    if "EXCHANGE_RATE" in key or "FEE_RATE" in key or "PROFIT_RATE" in key:
        global _RATE_CACHE, _PROFIT_CACHE
        _RATE_CACHE.update({"data": None, "ts": 0.0})
        _PROFIT_CACHE.clear()

def get_profit_rate(category_val: str, override: float = 0.0) -> float:
    """
    利益率を優先順位に従って返す。
    優先: 商品個別 override > カテゴリ設定 > デフォルト設定
    """
    if override > 0:
        return override
    env_key = CATEGORY_PROFIT_KEYS.get(category_val, "")
    if env_key:
        val = get_env(env_key)
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    return float(get_env("DEFAULT_PROFIT_RATE", "0.25"))

def get_platform_margin(platform: str) -> float:
    """プラットフォーム別追加マージン"""
    if platform == "ebay":
        return float(get_env("PROFIT_MARGIN_EBAY", "0.03"))
    return float(get_env("PROFIT_MARGIN_SHOPEE", "0.00"))

def get_all_rates() -> dict:
    """全通貨レートを返す（1時間キャッシュ）"""
    global _RATE_CACHE
    now = time.time()
    if _RATE_CACHE["data"] is not None and now - _RATE_CACHE["ts"] < 3600:
        return _RATE_CACHE["data"]
    data = {
        "USD": float(get_env("DEFAULT_EXCHANGE_RATE_USD", "150")),
        "SGD": float(get_env("DEFAULT_EXCHANGE_RATE_SGD", "112")),
        "TWD": float(get_env("DEFAULT_EXCHANGE_RATE_TWD", "4.5")),
        "MYR": float(get_env("DEFAULT_EXCHANGE_RATE_MYR", "33.0")),
        "PHP": float(get_env("DEFAULT_EXCHANGE_RATE_PHP", "2.7")),
    }
    _RATE_CACHE.update({"data": data, "ts": now})
    return data

def calc_profit_for_product(product) -> dict:
    """
    商品の利益率・利益額を計算して返す（商品データ変更時のみ再計算）。
    戻り値: {profit_rate, profit_jpy, price_usd, price_sgd, price_twd, price_myr, price_php}
    """
    # ── キャッシュチェック（product.id + updated_at が同じなら再計算しない）──
    global _PROFIT_CACHE
    if product.id is not None:
        cache_key = (product.id, str(product.updated_at))
        if cache_key in _PROFIT_CACHE:
            return _PROFIT_CACHE[cache_key]

    cost = float(product.cost_price or 0)
    if cost <= 0:
        return {}
    rates = get_all_rates()
    profit_rate = float(product.target_profit_rate or get_profit_rate(
        product.product_category.value if product.product_category else "other"
    ))
    ebay_fee  = float(get_env("EBAY_FEE_RATE", "0.13"))
    shop_fee  = float(get_env("SHOPEE_FEE_RATE", "0.06"))
    pay_fee   = float(get_env("PAYMENT_FEE_RATE", "0.044"))
    fx_fee    = float(get_env("FX_FEE_RATE", "0.02"))
    ebay_total_fee  = ebay_fee  + pay_fee + fx_fee
    shop_total_fee  = shop_fee  + pay_fee + fx_fee

    def price_from_cost(total_cost: float, fee_rate: float, pr: float) -> float:
        denom = 1 - fee_rate - pr
        return total_cost / denom if denom > 0 else total_cost * 2

    dom_ship = float(product.domestic_shipping_cost or 0)
    total_cost = cost + dom_ship

    price_jpy_ebay  = price_from_cost(total_cost, ebay_total_fee, profit_rate)
    price_jpy_shopee = price_from_cost(total_cost, shop_total_fee, profit_rate)

    # USD (eBay)
    price_usd = product.calc_selling_price_usd or round(price_jpy_ebay / rates["USD"], 2)
    # SGD (Shopee SGP)
    price_sgd = product.calc_selling_price_sgd or round(price_jpy_shopee / rates["SGD"], 2)
    # TWD (Shopee TWN)
    price_twd = product.calc_selling_price_twd or round(price_jpy_shopee / rates["TWD"], 0)
    # MYR (Shopee MYS)
    price_myr = product.calc_selling_price_myr or round(price_jpy_shopee / rates["MYR"], 2)
    # PHP (Shopee PHL)
    price_php = product.calc_selling_price_php or round(price_jpy_shopee / rates["PHP"], 0)

    # 利益額（USD価格を円換算して計算）
    revenue_jpy = price_usd * rates["USD"]
    total_fees_jpy = revenue_jpy * ebay_total_fee
    profit_jpy = revenue_jpy - total_cost - total_fees_jpy
    actual_rate = profit_jpy / revenue_jpy if revenue_jpy > 0 else 0

    result = {
        "profit_rate":  actual_rate,
        "profit_jpy":   profit_jpy,
        "price_usd":    price_usd,
        "price_sgd":    price_sgd,
        "price_twd":    price_twd,
        "price_myr":    price_myr,
        "price_php":    price_php,
    }
    # キャッシュに保存
    if product.id is not None:
        _PROFIT_CACHE[(product.id, str(product.updated_at))] = result
        # キャッシュが肥大しないよう 500 件を上限に古いものを削除
        if len(_PROFIT_CACHE) > 500:
            oldest_key = next(iter(_PROFIT_CACHE))
            del _PROFIT_CACHE[oldest_key]
    return result

def get_product_main_image(product) -> Optional[str]:
    """商品のメイン画像URLを返す（image_urls[0] → image_url の順で優先）"""
    imgs = product.image_urls
    if imgs:
        if isinstance(imgs, list) and imgs:
            return imgs[0]
        if isinstance(imgs, str):
            try:
                parsed = json.loads(imgs)
                if parsed:
                    return parsed[0]
            except Exception:
                return imgs
    return product.image_url or None


def get_product_all_images(product) -> list:
    """商品の全画像URLリストを返す"""
    imgs = product.image_urls
    if imgs:
        if isinstance(imgs, list):
            return [u for u in imgs if u]
        if isinstance(imgs, str):
            try:
                parsed = json.loads(imgs)
                return [u for u in parsed if u]
            except Exception:
                return [imgs] if imgs else []
    main = product.image_url
    return [main] if main else []


def products_to_dfs(products: list):
    """
    商品リストを2つの DataFrame に変換して返す。
      df_main   — 常時表示（サムネイル・商品名・仕入れ・利益率・USD・ステータス）
      df_detail — 折りたたみ表示（SGD/TWD/MYR/PHP・利益額・在庫・eBay/Shopee）
    """
    main_rows = []
    detail_rows = []
    for p in products:
        ebay_st   = "🟢 出品中" if p.ebay_listing_id else ("⬜ 対象" if p.target_ebay else "—")
        shopee_st = "🟢 出品中" if p.shopee_item_id else ("⬜ 対象" if p.target_shopee else "—")
        pf = calc_profit_for_product(p)
        profit_rate = pf.get("profit_rate", 0)
        profit_jpy  = pf.get("profit_jpy", 0)

        # 利益率表示 with 警告アイコン
        rate_str = f"{profit_rate*100:.1f}%"
        if profit_rate < 0.05 and pf:
            rate_str = f"🔴 {profit_rate*100:.1f}%"
        elif profit_rate < 0.10 and pf:
            rate_str = f"🟡 {profit_rate*100:.1f}%"

        # 画像 URL（なければ No Image プレースホルダー）
        main_img = get_product_main_image(p) or NO_IMAGE_PLACEHOLDER

        main_rows.append({
            "画像":     main_img,
            "商品名":   p.name[:35] + ("…" if len(p.name) > 35 else ""),
            "仕入れ元": SOURCE_LABELS.get(p.source_site, str(p.source_site)),
            "仕入れ値": f"¥{p.cost_price:,.0f}",
            "利益率":   rate_str if pf else "—",
            "推奨USD":  f"${pf['price_usd']:,.2f}" if pf.get("price_usd") else "—",
            "状態":     STATUS_LABELS.get(p.status, str(p.status)),
            "_id":      p.id,          # ソート・紐付け用（表示しない）
        })
        detail_rows.append({
            "商品名":   p.name[:25] + ("…" if len(p.name) > 25 else ""),
            "SKU":      p.sku,
            "利益額":   f"¥{profit_jpy:,.0f}" if pf else "—",
            "推奨SGD":  f"S${pf['price_sgd']:,.2f}" if pf.get("price_sgd") else "—",
            "推奨TWD":  f"NT${pf['price_twd']:,.0f}" if pf.get("price_twd") else "—",
            "推奨MYR":  f"RM{pf['price_myr']:,.2f}"  if pf.get("price_myr") else "—",
            "推奨PHP":  f"₱{pf['price_php']:,.0f}"  if pf.get("price_php") else "—",
            "在庫":     p.current_stock,
            "eBay":     ebay_st,
            "Shopee":   shopee_st,
        })
    return pd.DataFrame(main_rows), pd.DataFrame(detail_rows)


# 後方互換エイリアス
def products_to_df(products: list) -> pd.DataFrame:
    df_main, _ = products_to_dfs(products)
    return df_main


def calc_profit_preview(
    cost_price: float,
    weight_g: float,
    size_l: float, size_w: float, size_h: float,
    category_val: str,
    target_countries: list,
    target_profit_rate: float,
    usd_rate: float, sgd_rate: float,
    ebay_fee: float, shopee_fee: float,
    payment_fee: float, fx_fee: float,
    twd_rate: float = 4.7,
    myr_rate: float = 33.0,
    php_rate: float = 2.7,
    hs_code: str = "",
    duty_bearer: str = "DDP",
) -> dict:
    if cost_price <= 0:
        return {}

    dom = calculate_domestic_shipping(weight_g or 500, size_l, size_w, size_h)
    domestic_ship = dom.fee_jpy

    results: dict = {}
    usd_prices, sgd_prices, twd_prices, myr_prices, php_prices = [], [], [], [], []

    for code in target_countries:
        intl_r = calculate_international_shipping(weight_g or 500, code, IntlCarrier.EMS)
        intl = intl_r.fee_jpy

        # ── 関税・輸入税の計算 ──────────────────────────────────────
        # tariff_v2 対応国（US/SG/TW/MY/PH）は新モジュールで計算
        duty_jpy = 0.0
        vat_jpy = 0.0
        duty_rate = 0.0
        vat_rate = 0.0
        vat_name = ""
        is_under_de_minimis = False
        de_minimis_info = ""

        if _tariff_v2.is_supported_country(code):
            try:
                info = _tariff_v2.get_country_info(code)
                cif_jpy = cost_price + intl
                fx = _tariff_v2.get_exchange_rate(info["currency"])
                cif_local = cif_jpy / fx if fx else 0.0
                de_min = info.get("de_minimis_local")

                if de_min is not None and cif_local <= de_min:
                    is_under_de_minimis = True
                    de_minimis_info = (
                        f"{info['currency']} {de_min:,.0f} 以下のため免税"
                    )
                else:
                    duty_rate = _tariff_v2.resolve_duty_rate(code, hs_code or None)
                    duty_jpy = cif_jpy * duty_rate
                    vat_rate = info.get("vat_rate", 0.0)
                    vat_name = info.get("vat_name", "")
                    vat_jpy = (cif_jpy + duty_jpy) * vat_rate
            except Exception:
                # フォールバック: 旧モジュール
                t = calculate_tariff(cost_price, code, category_val, usd_rate)
                duty_jpy = t.tariff_amount_jpy
        else:
            # tariff_v2 未対応国: 旧モジュールで関税のみ
            t = calculate_tariff(cost_price, code, category_val, usd_rate)
            duty_jpy = t.tariff_amount_jpy

        # ── 価格計算 ────────────────────────────────────────────────
        is_ebay   = code in EBAY_COUNTRIES
        is_shopee = code in SHOPEE_COUNTRIES
        mkt_fee   = ebay_fee if is_ebay else shopee_fee
        total_fee = mkt_fee + payment_fee + fx_fee

        # DDP: セラーが関税+VATを負担 → 原価に含めて価格算出
        # DDU: バイヤーが負担 → 原価に含めず価格算出
        if duty_bearer == "DDP":
            base_cost = cost_price + domestic_ship + intl + duty_jpy + vat_jpy
            buyer_burden = 0.0
        else:
            base_cost = cost_price + domestic_ship + intl
            buyer_burden = duty_jpy + vat_jpy

        denom = 1 - total_fee - target_profit_rate
        if denom <= 0:
            denom = 0.1
        price_jpy = base_cost / denom

        price_usd = price_jpy / usd_rate
        price_sgd = price_jpy / sgd_rate
        price_twd = price_jpy / twd_rate
        price_myr = price_jpy / myr_rate
        price_php = price_jpy / php_rate
        profit_jpy = price_jpy * target_profit_rate
        fee_jpy = price_jpy * total_fee

        results[code] = {
            "country":             COUNTRY_NAMES.get(code, code),
            "cost":                cost_price,
            "domestic_ship":       domestic_ship,
            "intl_ship":           intl,
            # 後方互換キー
            "tariff":              duty_jpy,
            # 新キー（詳細内訳）
            "duty_jpy":            duty_jpy,
            "duty_rate":           duty_rate,
            "vat_jpy":             vat_jpy,
            "vat_rate":            vat_rate,
            "vat_name":            vat_name,
            "is_under_de_minimis": is_under_de_minimis,
            "de_minimis_info":     de_minimis_info,
            "duty_bearer":         duty_bearer,
            "buyer_burden_jpy":    buyer_burden,
            "total_cost":          base_cost,
            "fee_rate":            total_fee,
            "fee_jpy":             fee_jpy,
            "price_jpy":           price_jpy,
            "price_usd":           price_usd,
            "price_sgd":           price_sgd,
            "price_twd":           price_twd,
            "price_myr":           price_myr,
            "price_php":           price_php,
            "profit_jpy":          profit_jpy,
            "profit_rate":         target_profit_rate,
            "is_ebay":             is_ebay,
            "marketplace":         "eBay" if is_ebay else "Shopee",
        }
        if is_ebay:
            usd_prices.append(price_usd)
        if is_shopee:
            sgd_prices.append(price_sgd)
            if code == "TWN": twd_prices.append(price_twd)
            elif code == "MYS": myr_prices.append(price_myr)
            elif code == "PHL": php_prices.append(price_php)

    return {
        "countries":     results,
        "domestic_ship": domestic_ship,
        "duty_bearer":   duty_bearer,
        "avg_price_usd": sum(usd_prices) / len(usd_prices) if usd_prices else None,
        "avg_price_sgd": sum(sgd_prices) / len(sgd_prices) if sgd_prices else None,
        "avg_price_twd": sum(twd_prices) / len(twd_prices) if twd_prices else None,
        "avg_price_myr": sum(myr_prices) / len(myr_prices) if myr_prices else None,
        "avg_price_php": sum(php_prices) / len(php_prices) if php_prices else None,
    }


# ─────────────────────────── サイドバー ────────────────────────────
with st.sidebar:
    st.title("🌐 GlobalBiz")
    st.caption("越境EC運営ツール v3")
    st.divider()
    page = st.radio(
        "ナビゲーション",
        ["📦 商品一覧", "➕ 商品登録", "✏️ 商品編集", "📥 インポート",
         "🚀 出品管理", "📊 監視・スケジューラ", "⚙️ 設定"],
        label_visibility="collapsed",
    )
    st.divider()
    with get_session() as s:
        n = s.query(Product).count()
    st.caption(f"登録商品: {n} 件")
    st.divider()
    if st.button("🚪 ログアウト", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()


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

    # フィルターが変わったらページをリセット
    filter_sig = f"{f_status}|{f_source}|{f_mkt}"
    if st.session_state.get("_list_filter_sig") != filter_sig:
        st.session_state["product_page"] = 0
        st.session_state["_list_filter_sig"] = filter_sig

    if "product_page" not in st.session_state:
        st.session_state["product_page"] = 0

    with get_session() as s:
        q = s.query(Product)
        if f_status == "販売中":     q = q.filter(Product.status == ProductStatus.ACTIVE)
        elif f_status == "在庫切れ": q = q.filter(Product.status == ProductStatus.OUT_OF_STOCK)
        elif f_status == "下書き":   q = q.filter(Product.status == ProductStatus.DRAFT)
        if f_source == "Amazon":  q = q.filter(Product.source_site == SourceSite.AMAZON)
        elif f_source == "楽天":  q = q.filter(Product.source_site == SourceSite.RAKUTEN)
        elif f_source == "Yahoo": q = q.filter(Product.source_site == SourceSite.YAHOO)
        elif f_source == "NETSEA":q = q.filter(Product.source_site == SourceSite.NETSEA)
        elif f_source == "手動":  q = q.filter(Product.source_site == SourceSite.MANUAL)
        if f_mkt == "eBay":    q = q.filter(Product.target_ebay == True)
        elif f_mkt == "Shopee":q = q.filter(Product.target_shopee == True)

        total_filtered = q.count()
        page_num = st.session_state.get("product_page", 0)
        total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
        page_num = max(0, min(page_num, total_pages - 1))
        st.session_state["product_page"] = page_num

        products = (
            q.order_by(Product.updated_at.desc())
             .offset(page_num * PAGE_SIZE)
             .limit(PAGE_SIZE)
             .all()
        )

    if not products and total_filtered == 0:
        st.info("商品がありません。「➕ 商品登録」から追加してください。")
    else:
        df_main, df_detail = products_to_dfs(products)

        # ── メインテーブル（常時表示・最適化カラム）──
        st.dataframe(
            df_main.drop(columns=["_id"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "画像": st.column_config.ImageColumn(
                    "📷",
                    help="商品画像（クリックで拡大）",
                    width="small",
                ),
                "商品名":   st.column_config.TextColumn("商品名",   width="large"),
                "仕入れ元": st.column_config.TextColumn("仕入れ元", width="small"),
                "仕入れ値": st.column_config.TextColumn("仕入れ値", width="small"),
                "利益率":   st.column_config.TextColumn("利益率",   width="small"),
                "推奨USD":  st.column_config.TextColumn("推奨USD",  width="small"),
                "状態":     st.column_config.TextColumn("状態",     width="small"),
            },
            row_height=64,
        )

        # ── 詳細カラム（折りたたみ）──
        with st.expander("📊 詳細カラムを表示（SGD/TWD/MYR/PHP・利益額・在庫・出品状況）"):
            st.dataframe(
                df_detail,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "商品名":  st.column_config.TextColumn("商品名",  width="medium"),
                    "SKU":     st.column_config.TextColumn("SKU",     width="small"),
                    "利益額":  st.column_config.TextColumn("利益額",  width="small"),
                    "推奨SGD": st.column_config.TextColumn("推奨SGD", width="small"),
                    "推奨TWD": st.column_config.TextColumn("推奨TWD", width="small"),
                    "推奨MYR": st.column_config.TextColumn("推奨MYR", width="small"),
                    "推奨PHP": st.column_config.TextColumn("推奨PHP", width="small"),
                    "在庫":    st.column_config.NumberColumn("在庫",  width="small"),
                    "eBay":    st.column_config.TextColumn("eBay",    width="small"),
                    "Shopee":  st.column_config.TextColumn("Shopee",  width="small"),
                },
                row_height=40,
            )

        # ── 画像拡大ビューア（選択商品のギャラリー）──
        with st.expander("🔍 画像を拡大表示", expanded=False):
            _view_options = {f"[{p.id}] {p.sku} — {p.name[:30]}": p for p in products}
            _view_sel = st.selectbox(
                "商品を選択",
                list(_view_options.keys()),
                key="list_img_viewer_sel",
                label_visibility="collapsed",
            )
            _vp = _view_options.get(_view_sel)
            if _vp:
                _vp_imgs = get_product_all_images(_vp)
                if _vp_imgs:
                    # メイン画像
                    _vi_main_col, _vi_sub_col = st.columns([2, 3])
                    with _vi_main_col:
                        st.caption("📌 メイン画像（1枚目）")
                        try:
                            st.image(_vp_imgs[0], use_container_width=True)
                        except Exception:
                            st.info("画像を表示できません")
                    with _vi_sub_col:
                        if len(_vp_imgs) > 1:
                            st.caption(f"サブ画像（全 {len(_vp_imgs)} 枚）")
                            _sub_grid = st.columns(min(len(_vp_imgs) - 1, 4))
                            for _si, _surl in enumerate(_vp_imgs[1:8], 1):
                                with _sub_grid[(_si - 1) % 4]:
                                    try:
                                        st.image(_surl, width=120)
                                        _dom = "🇺🇸" if "amazon.com/" in _surl and ".co.jp" not in _surl else "🇯🇵" if "amazon" in _surl else "🌐"
                                        st.caption(f"{_dom} {_si+1}")
                                    except Exception:
                                        st.caption(f"画像{_si+1}")
                else:
                    st.info("この商品には画像が登録されていません")

        # ── ページネーションコントロール ──
        pn_left, pn_center, pn_right = st.columns([1, 4, 1])
        with pn_left:
            if st.button("◀ 前へ", disabled=(page_num == 0), use_container_width=True):
                st.session_state["product_page"] -= 1
                st.rerun()
        with pn_center:
            start = page_num * PAGE_SIZE + 1
            end   = min((page_num + 1) * PAGE_SIZE, total_filtered)
            st.caption(
                f"ページ **{page_num + 1}** / {total_pages}  |  "
                f"{start}〜{end} 件表示（全 {total_filtered} 件）  |  "
                "🔴=利益率5%未満  🟡=10%未満"
            )
        with pn_right:
            if st.button("次へ ▶", disabled=(page_num >= total_pages - 1), use_container_width=True):
                st.session_state["product_page"] += 1
                st.rerun()

        # ─── 赤字警告（現在のページ内のみ）───
        low_profit = [p for p in products
                      if (pf := calc_profit_for_product(p)) and pf.get("profit_rate", 1) < 0.05]
        if low_profit:
            with st.expander(f"⚠️ 利益率5%未満の商品（このページ: {len(low_profit)}件）", expanded=True):
                for p in low_profit:
                    pf = calc_profit_for_product(p)
                    st.warning(f"**{p.name[:40]}** ({p.sku})  利益率: {pf['profit_rate']*100:.1f}%  "
                               f"利益: ¥{pf['profit_jpy']:,.0f}")

        # ─── CSV エクスポート ───
        st.divider()
        st.subheader("📥 CSVエクスポート")
        rates = get_all_rates()

        with st.expander("🎯 出力対象を選ぶ", expanded=True):
            _scope = st.radio(
                "対象",
                ["表示中の全商品", "Shopee対象商品のみ", "eBay対象商品のみ"],
                horizontal=True, label_visibility="collapsed",
            )
            if _scope == "Shopee対象商品のみ":
                _export_prods = [p for p in products if p.target_shopee]
            elif _scope == "eBay対象商品のみ":
                _export_prods = [p for p in products if p.target_ebay]
            else:
                _export_prods = products
            st.caption(f"出力対象: {len(_export_prods)} 件")

        csv_c1, csv_c2 = st.columns(2)

        # Shopee 多国 CSV
        with csv_c1:
            with st.container(border=True):
                st.markdown("#### 🟧 Shopee 多国展開 CSV")
                _shopee_countries = st.multiselect(
                    "出力する国",
                    options=list(SHOPEE_COUNTRY_CONFIG.keys()),
                    default=list(SHOPEE_COUNTRY_CONFIG.keys()),
                    format_func=lambda c: f"{SHOPEE_COUNTRY_CONFIG[c]['name']} ({SHOPEE_COUNTRY_CONFIG[c]['currency']})",
                )
                if _export_prods and _shopee_countries:
                    if len(_shopee_countries) == 1:
                        # 1国 → 単体 CSV
                        _sc = _shopee_countries[0]
                        _scfg = SHOPEE_COUNTRY_CONFIG[_sc]
                        _rate = rates.get(_scfg["currency"], _scfg["rate_default"])
                        _csv_bytes = export_shopee_csv(_export_prods, country_code=_sc, rate=_rate)
                        _fname = f"shopee_{_sc}_{datetime.now().strftime('%Y%m%d')}.csv"
                        st.download_button(
                            f"⬇️ Shopee CSV ({_scfg['name']}) をダウンロード",
                            data=_csv_bytes, file_name=_fname, mime="text/csv",
                            use_container_width=True, type="primary",
                        )
                    else:
                        # 複数国 → ZIP
                        _rates_map = {
                            c: rates.get(SHOPEE_COUNTRY_CONFIG[c]["currency"],
                                         SHOPEE_COUNTRY_CONFIG[c]["rate_default"])
                            for c in _shopee_countries
                        }
                        _zip_bytes = export_shopee_zip(_export_prods,
                                                       country_codes=_shopee_countries,
                                                       rates=_rates_map)
                        _zip_name = f"shopee_multi_{datetime.now().strftime('%Y%m%d')}.zip"
                        st.download_button(
                            f"⬇️ Shopee CSV ({len(_shopee_countries)}国) ZIP をダウンロード",
                            data=_zip_bytes, file_name=_zip_name, mime="application/zip",
                            use_container_width=True, type="primary",
                        )
                        st.caption(f"含まれるファイル: " +
                                   ", ".join(f"shopee_{c}*.csv" for c in _shopee_countries))
                else:
                    st.warning("出力対象の商品または国を選択してください")

        # eBay CSV
        with csv_c2:
            with st.container(border=True):
                st.markdown("#### 🟦 eBay File Exchange CSV")
                _ebay_action = st.selectbox(
                    "アクション",
                    ["Add（新規）", "Revise（更新）", "End（終了）"],
                    key="ebay_csv_action", label_visibility="collapsed",
                )
                _action_map = {"Add（新規）": "Add", "Revise（更新）": "Revise", "End（終了）": "End"}
                _fname_ebay = f"ebay_products_{datetime.now().strftime('%Y%m%d')}.csv"
                if _export_prods:
                    _ebay_bytes = export_ebay_csv(
                        _export_prods,
                        action=_action_map[_ebay_action],
                        usd_rate=rates["USD"],
                    )
                    st.download_button(
                        f"⬇️ eBay CSV をダウンロード ({len(_export_prods)}件)",
                        data=_ebay_bytes, file_name=_fname_ebay, mime="text/csv",
                        use_container_width=True, type="primary",
                    )
                    st.caption(f"{_fname_ebay}  |  {len(_ebay_bytes):,} bytes")
                else:
                    st.warning("出力対象の商品がありません")

        # クイック出品
        st.divider()
        st.subheader("🚀 クイック出品")

        if not products:
            st.info("商品がありません。「➕ 商品登録」から商品を追加してください。")
        else:
            product_options = {f"[{p.id}] {p.sku} — {p.name[:35]}": p.id for p in products}
            sel = st.selectbox("商品を選択", list(product_options.keys()),
                               key="quick_list_sel")
            sel_id = product_options[sel]

            with get_session() as s:
                sel_p = s.query(Product).filter(Product.id == sel_id).first()

            if sel_p:
                pf = calc_profit_for_product(sel_p)
                _qs_img = get_product_main_image(sel_p)

                # ── 画像（左）+ 商品情報グリッド（右）──
                _qs_left, _qs_right = st.columns([1, 3], gap="medium")

                with _qs_left:
                    if _qs_img:
                        try:
                            st.image(_qs_img, width=120)
                        except Exception:
                            st.markdown(
                                "<div style='width:120px;height:120px;background:#EEEEEE;"
                                "display:flex;align-items:center;justify-content:center;"
                                "border-radius:10px;font-size:2rem'>📷</div>",
                                unsafe_allow_html=True,
                            )
                    else:
                        st.markdown(
                            "<div style='width:120px;height:120px;background:#EEEEEE;"
                            "display:flex;flex-direction:column;align-items:center;"
                            "justify-content:center;border-radius:10px;gap:4px'>"
                            "<span style='font-size:2rem'>📷</span>"
                            "<span style='font-size:0.7rem;color:#AAA'>No Image</span>"
                            "</div>",
                            unsafe_allow_html=True,
                        )

                with _qs_right:
                    st.markdown(f"**{sel_p.name[:50]}**")
                    # 2列×2行グリッド
                    _mg1, _mg2 = st.columns(2)
                    _mg1.metric(
                        "💴 仕入れ値",
                        f"¥{sel_p.cost_price:,.0f}",
                    )
                    _mg2.metric(
                        "📈 利益率",
                        f"{pf['profit_rate']*100:.1f}%" if pf.get("profit_rate") is not None else "—",
                    )
                    _mg3, _mg4 = st.columns(2)
                    _mg3.metric(
                        "🟦 推奨USD",
                        f"${pf['price_usd']:,.2f}" if pf.get("price_usd") else "未計算",
                    )
                    _mg4.metric(
                        "🟧 推奨SGD",
                        f"S${pf['price_sgd']:,.2f}" if pf.get("price_sgd") else "未計算",
                    )

                    # 出品ボタン
                    _bb1, _bb2 = st.columns(2)
                    if _bb1.button(
                        "🟦 eBay に出品する",
                        use_container_width=True,
                        type="primary",
                        key="qs_ebay_btn",
                    ):
                        st.session_state["listing_target"] = (sel_id, "ebay")
                        st.session_state["show_listing_modal"] = True
                        st.rerun()
                    if _bb2.button(
                        "🟧 Shopee に出品する",
                        use_container_width=True,
                        type="secondary",
                        key="qs_shopee_btn",
                    ):
                        st.session_state["listing_target"] = (sel_id, "shopee")
                        st.session_state["show_listing_modal"] = True
                        st.rerun()

    # 出品モーダル
    if st.session_state.get("show_listing_modal") and st.session_state.get("listing_target"):
        pid, platform = st.session_state["listing_target"]
        with st.container(border=True):
            st.subheader(f"🚀 {'eBay' if platform == 'ebay' else 'Shopee'} 出品確認")
            with get_session() as s:
                p = s.query(Product).filter(Product.id == pid).first()
            if p:
                # 商品情報 + 画像プレビュー
                _modal_img = get_product_main_image(p)
                _modal_img_col, _modal_info_col = st.columns([1, 3])
                with _modal_img_col:
                    if _modal_img:
                        try:
                            st.image(_modal_img, use_container_width=True)
                            st.caption(
                                f"{'この画像でeBayに出品します' if platform == 'ebay' else 'この画像でShopeeに出品します'}"
                            )
                        except Exception:
                            st.caption("画像を表示できません")
                    else:
                        st.info("📷 画像未登録")
                with _modal_info_col:
                    st.write(f"**商品:** {p.name}")
                    _all_modal_imgs = get_product_all_images(p)
                    if len(_all_modal_imgs) > 1:
                        st.caption(f"📷 登録画像: {len(_all_modal_imgs)} 枚（全て出品に使用されます）")
                    pf = calc_profit_for_product(p)
                    if platform == "ebay":
                        price_val = pf.get("price_usd") or round(float(p.cost_price) / 150 * 1.5, 2)
                        price_input = st.number_input("販売価格 (USD)", value=round(float(price_val), 2), step=0.5)
                    else:
                        price_val = pf.get("price_sgd") or round(float(p.cost_price) / 112 * 1.5, 2)
                        price_input = st.number_input("販売価格 (SGD)", value=round(float(price_val), 2), step=0.5)

                mc1, mc2 = st.columns(2)
                if mc1.button("✅ 出品する", type="primary"):
                    with st.spinner("出品処理中..."):
                        if platform == "ebay":
                            import backend.marketplaces.ebay as _em
                            importlib.reload(_em)
                            client = _em.EbayClient()
                            if not client.is_configured():
                                st.error("eBay APIキーが未設定です")
                            else:
                                result = client.create_listing(p, price_input)
                                if result.success:
                                    with get_session() as s2:
                                        pp = s2.query(Product).filter(Product.id == pid).first()
                                        pp.ebay_listing_id = result.listing_id
                                        pp.status = ProductStatus.ACTIVE
                                        s2.commit()
                                    st.success(f"✅ eBay 出品完了！ ID: {result.listing_id}")
                                    st.session_state["show_listing_modal"] = False
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

    rates    = get_all_rates()
    usd_rate = rates["USD"]
    sgd_rate = rates["SGD"]
    twd_rate = rates["TWD"]
    myr_rate = rates["MYR"]
    php_rate = rates["PHP"]
    ebay_fee    = float(get_env("EBAY_FEE_RATE", "0.13"))
    shopee_fee  = float(get_env("SHOPEE_FEE_RATE", "0.06"))
    payment_fee = float(get_env("PAYMENT_FEE_RATE", "0.044"))
    fx_fee      = float(get_env("FX_FEE_RATE", "0.02"))

    left, right = st.columns([3, 2], gap="large")

    # ────── 左カラム ──────
    with left:

        # ── ASIN 自動取得パネル（フォーム外）──
        with st.expander("🔍 ASIN から商品情報を自動取得", expanded=False):
            _af_c1, _af_c2, _af_c3 = st.columns([3, 1, 1])
            _asin_input = _af_c1.text_input(
                "ASIN コード", placeholder="B0BDHWDR12",
                key="asin_fetch_input", label_visibility="visible",
            )
            # 画像取得リージョン選択
            _img_region = _af_c2.radio(
                "画像取得元",
                options=["🇯🇵JP（日本語）", "🇺🇸US（英語）", "🌐 両方"],
                index=0,
                key="asin_img_region",
                help="US画像は英語パッケージ。JP画像は日本語パッケージ。両方取得して選べます。",
            )
            _region_map = {
                "🇯🇵JP（日本語）": "jp",
                "🇺🇸US（英語）": "us",
                "🌐 両方": "both",
            }
            _selected_region = _region_map.get(_img_region, "both")

            if _af_c3.button("📥 ASINから取得", type="primary", use_container_width=True):
                if not _asin_input:
                    st.warning("ASIN を入力してください")
                else:
                    _spinner_msg = {
                        "us":   "Amazon.com（US）から取得中...",
                        "jp":   "Amazon.co.jp（JP）から取得中...",
                        "both": "Amazon.co.jp + Amazon.com から並列取得中...",
                    }.get(_selected_region, "取得中...")
                    with st.spinner(f"ASIN {_asin_input.strip().upper()} — {_spinner_msg}"):
                        try:
                            from backend.scrapers.amazon import fetch_product_by_asin
                            _result = fetch_product_by_asin(
                                _asin_input.strip().upper(),
                                region=_selected_region,
                            )
                        except Exception as _e:
                            _result = {"error": str(_e)}

                    if _result.get("error") and not _result.get("name"):
                        st.error(f"❌ {_result['error']}")
                    else:
                        st.session_state["asin_prefill"] = _result
                        _name_disp = _result.get("name", "") or _result.get("name_en", "")
                        st.success(f"✅ 取得完了: {_name_disp[:50]}")
                        if _result.get("price"):
                            st.info(f"💴 Amazon JP 価格: ¥{_result['price']:,.0f}")
                        if _result.get("name_en") and _result["name_en"] != _result.get("name",""):
                            st.info(f"🇺🇸 英語名: {_result['name_en'][:60]}")
                        if _result.get("error_us"):
                            st.warning(f"⚠️ US画像取得: {_result['error_us'][:60]}")

                        # 画像プレビュー（JP と US 並べて表示）
                        _imgs_jp = _result.get("images_jp", [])
                        _imgs_us = _result.get("images_us", [])
                        if _imgs_us and _imgs_jp:
                            _pv_c1, _pv_c2 = st.columns(2)
                            with _pv_c1:
                                st.caption(f"🇯🇵 JP画像 ({len(_imgs_jp)}枚)")
                                _jp_cols = st.columns(min(len(_imgs_jp), 4))
                                for _ii, _iu in enumerate(_imgs_jp[:4]):
                                    try: _jp_cols[_ii].image(_iu, width=75)
                                    except Exception: pass
                            with _pv_c2:
                                st.caption(f"🇺🇸 US画像 ({len(_imgs_us)}枚)")
                                _us_cols = st.columns(min(len(_imgs_us), 4))
                                for _ii, _iu in enumerate(_imgs_us[:4]):
                                    try: _us_cols[_ii].image(_iu, width=75)
                                    except Exception: pass
                        elif _result.get("images"):
                            _pv_cols = st.columns(min(len(_result["images"]), 4))
                            for _ii, _iu in enumerate(_result["images"][:4]):
                                try: _pv_cols[_ii].image(_iu, width=80)
                                except Exception: pass

                        st.caption(
                            f"カテゴリ: {_result.get('category','—')} / 在庫: {_result.get('availability','—')}"
                            + (f" / JAN: {_result['jan_code']}" if _result.get("jan_code") else "")
                            + (f" / 重量: {_result['weight_g']:.0f}g" if _result.get("weight_g") else "")
                        )
                        st.caption("⬇️ 下のパネルに自動入力されます（登録ボタンで確定）")

            # プリフィルデータの確認表示
            if st.session_state.get("asin_prefill"):
                pf_data = st.session_state["asin_prefill"]
                _n = pf_data.get("name", "") or pf_data.get("name_en", "")
                _imgs_jp_n = len(pf_data.get("images_jp", []))
                _imgs_us_n = len(pf_data.get("images_us", []))
                _img_info = (
                    f"JP:{_imgs_jp_n}枚 / US:{_imgs_us_n}枚"
                    if (_imgs_jp_n or _imgs_us_n)
                    else f"画像 {len(pf_data.get('images',[]))} 枚"
                )
                st.info(f"📋 取得済み: **{_n[:60]}**  |  {_img_info}")
                if st.button("🗑️ 取得データをクリア", key="clear_prefill"):
            st.session_state["reg_name_jp"] = _n
                    for _k in ["asin_prefill", "reg_images"]:
                        st.session_state.pop(_k, None)
                    st.rerun()

        # プリフィルデータを読み込む
        _pf = st.session_state.get("asin_prefill") or {}

        # ASIN 取得後、画像を自動でセッションに取り込む（US優先）
        if _pf and "reg_images" not in st.session_state:
            _auto_imgs = _pf.get("images_us") or _pf.get("images_jp") or _pf.get("images") or []
            if _auto_imgs:
                st.session_state["reg_images"] = list(_auto_imgs)

        # ══════════════════════════════════════════════════════
        #  🌐 翻訳・AI コンテンツ生成パネル（フォーム外）
        # ══════════════════════════════════════════════════════
        with st.expander("🌐 翻訳・AI コンテンツ生成", expanded=bool(_pf)):

            _trans_name_jp = st.text_input(
                "商品名（日本語）",
                value=_pf.get("name", st.session_state.get("reg_name_jp", "")),
                key="trans_name_jp_input",
                help="ASINから取得した商品名、または手動入力してください",
            )
            _tn_c1, _tn_c2, _tn_c3 = st.columns([2, 1, 1])
            if _tn_c2.button("🌐 英語に変換", key="btn_trans_name_en", use_container_width=True):
                if _trans_name_jp:
                    with st.spinner("翻訳中..."):
                        from backend.translators.translate import translate_to_english
                        _en, _m = translate_to_english(_trans_name_jp)
                    if _en:
                        st.session_state["reg_name_en"] = _en
                        st.success(f"✅ 英語変換完了 ({_m}): {_en[:60]}")
                        st.rerun()
                    else:
                        st.error("翻訳に失敗しました")
                else:
                    st.warning("商品名（日本語）を入力してください")
            if _tn_c3.button("🀄 繁体字中国語", key="btn_trans_name_zh", use_container_width=True):
                if _trans_name_jp:
                    with st.spinner("翻訳中..."):
                        from backend.translators.translate import translate_to_traditional_chinese
                        _zh, _m = translate_to_traditional_chinese(_trans_name_jp)
                    if _zh:
                        st.session_state["reg_name_zh"] = _zh
                        st.success(f"✅ 繁体字変換完了 ({_m}): {_zh[:60]}")
                        st.rerun()
                    else:
                        st.error("翻訳に失敗しました")
                else:
                    st.warning("商品名（日本語）を入力してください")
            _tn_c1.caption(
                f"英語: {st.session_state.get('reg_name_en', '（未翻訳）')[:60]}" +
                (f" | 繁体字: {st.session_state.get('reg_name_zh', '')[:30]}"
                 if st.session_state.get("reg_name_zh") else "")
            )

            st.divider()
            _trans_desc_jp = st.text_area(
                "商品説明（日本語）",
                value=st.session_state.get("reg_desc_jp", ""),
                height=80,
                key="trans_desc_jp_input",
            )
            _td_c1, _td_c2, _td_c3 = st.columns([2, 1, 1])
            if _td_c2.button("🌐 英語に変換", key="btn_trans_desc_en", use_container_width=True):
                if _trans_desc_jp:
                    with st.spinner("翻訳中..."):
                        from backend.translators.translate import translate_to_english
                        _den, _m = translate_to_english(_trans_desc_jp)
                    if _den:
                        st.session_state["reg_desc_en"] = _den
                        st.session_state["reg_desc_jp"] = _trans_desc_jp
                        st.success(f"✅ 説明を英語翻訳完了 ({_m})")
                        st.rerun()
                    else:
                        st.error("翻訳に失敗しました")
                else:
                    st.warning("商品説明（日本語）を入力してください")
            if _td_c3.button("🀄 繁体字中国語", key="btn_trans_desc_zh", use_container_width=True):
                if _trans_desc_jp:
                    with st.spinner("翻訳中..."):
                        from backend.translators.translate import translate_to_traditional_chinese
                        _dzh, _m = translate_to_traditional_chinese(_trans_desc_jp)
                    if _dzh:
                        st.session_state["reg_desc_zh"] = _dzh
                        st.session_state["reg_desc_jp"] = _trans_desc_jp
                        st.success(f"✅ 説明を繁体字翻訳完了 ({_m})")
                        st.rerun()
                    else:
                        st.error("翻訳に失敗しました")
                else:
                    st.warning("商品説明（日本語）を入力してください")
            if st.session_state.get("reg_desc_en"):
                _td_c1.caption(f"英語訳: {st.session_state['reg_desc_en'][:80]}…")
            if st.session_state.get("reg_desc_zh"):
                st.caption(f"繁体字訳: {st.session_state['reg_desc_zh'][:80]}…")

            st.divider()
            st.markdown("**✨ eBay/Shopee 用英語コンテンツ自動生成**")
            _ai_cat = st.session_state.get("reg_category_select",
                                           _pf.get("category", "other"))
            _ai_configured = bool(get_env("ANTHROPIC_API_KEY"))

            # APIキーの有無を表示
            if _ai_configured:
                st.caption("🤖 Claude AI モード（高品質生成）")
            else:
                st.caption("📋 テンプレートモード（APIキーなしでも利用可）  "
                           "— Anthropic APIキーを設定画面で入力するとAI生成に切り替わります")

            _gen_btn_label = (
                "✨ Claude AIで英語コンテンツを生成" if _ai_configured
                else "📋 テンプレートで英語コンテンツを生成"
            )
            if st.button(
                _gen_btn_label,
                key="btn_ai_generate",
                type="primary",
                disabled=not bool(_trans_name_jp),
                use_container_width=True,
            ):
                _spinner_msg = (
                    "Claude AI で生成中...（10〜20秒）" if _ai_configured
                    else "テンプレート＋翻訳で生成中..."
                )
                with st.spinner(_spinner_msg):
                    from backend.ai.content_gen import generate_listing_content
                    _ai_result = generate_listing_content(
                        product_name_ja=_trans_name_jp,
                        description_ja=_trans_desc_jp,
                        category=_ai_cat,
                        product_name_en=st.session_state.get("reg_name_en", ""),
                    )
                if _ai_result.get("error") and not _ai_result.get("ebay_title"):
                    st.error(f"❌ {_ai_result['error']}")
                else:
                    st.session_state["ai_content"] = _ai_result
                    _src = _ai_result.get("source", "template")
                    st.success(
                        f"✅ 生成完了（{'Claude AI' if _src == 'claude' else 'テンプレート'}）"
                    )
                    st.rerun()

            if st.session_state.get("ai_content"):
                _ai = st.session_state["ai_content"]
                _src_badge = (
                    "🤖 Claude AI生成" if _ai.get("source") == "claude"
                    else "📋 テンプレート生成"
                )

                # ── タイトル ──
                with st.container(border=True):
                    st.caption(_src_badge)
                    _tc1, _tc2 = st.columns([4, 1])
                    _tc1.markdown(
                        f"**🟦 eBay タイトル（{len(_ai.get('ebay_title',''))}文字）**"
                    )
                    _tc1.code(_ai.get("ebay_title", ""), language=None)
                    _tc2.markdown("<br>", unsafe_allow_html=True)
                    if _tc2.button("📋", key="copy_ebay_title", help="eBayタイトルを英語名欄に適用"):
                        st.session_state["reg_name_en"] = _ai.get("ebay_title", "")
                        st.success("eBayタイトルを英語名欄に適用しました")
                        st.rerun()

                    _sc1, _sc2 = st.columns([4, 1])
                    _sc1.markdown(
                        f"**🟧 Shopee タイトル（{len(_ai.get('shopee_title',''))}文字）**"
                    )
                    _sc1.code(_ai.get("shopee_title", ""), language=None)

                # ── 特徴リスト ──
                if _ai.get("features"):
                    with st.container(border=True):
                        st.markdown("**✅ 特徴リスト（eBay Item Specifics用）**")
                        for _i, _feat in enumerate(_ai["features"], 1):
                            st.markdown(f"{_i}. {_feat}")

                # ── 説明文プレビュー（タブ切り替え）──
                if _ai.get("description_html") or _ai.get("description_plain"):
                    st.markdown("**📄 説明文プレビュー**")
                    _prev_tab1, _prev_tab2 = st.tabs(
                        ["🟦 eBay（HTML）", "🟧 Shopee（テキスト）"]
                    )
                    with _prev_tab1:
                        if _ai.get("description_html"):
                            st.markdown(
                                _ai["description_html"],
                                unsafe_allow_html=True,
                            )
                            st.markdown("---")
                            st.caption("▼ HTMLソース（コピーして eBay に貼り付け）")
                            st.text_area(
                                "eBay HTML説明文",
                                value=_ai["description_html"],
                                height=150,
                                key="preview_ebay_html",
                                label_visibility="collapsed",
                            )
                            if st.button("⬇️ eBay説明文をフォームに適用",
                                         key="apply_ebay_desc", use_container_width=True):
                                st.session_state["reg_desc_en"] = _ai["description_html"]
                                st.success("✅ eBay説明文を適用しました")
                                st.rerun()
                    with _prev_tab2:
                        if _ai.get("description_plain"):
                            st.markdown(
                                "```\n" + _ai["description_plain"] + "\n```"
                            )
                            if st.button("⬇️ Shopee説明文をフォームに適用",
                                         key="apply_shopee_desc", use_container_width=True):
                                st.session_state["reg_desc_en"] = _ai["description_plain"]
                                st.success("✅ Shopee説明文を適用しました")
                                st.rerun()

                # ── 一括適用 / クリア ──
                _ai_btn_c1, _ai_btn_c2 = st.columns(2)
                if _ai_btn_c1.button(
                    "⬇️ タイトル＋説明文を一括適用",
                    key="apply_ai_en", use_container_width=True, type="primary",
                ):
                    st.session_state["reg_name_en"] = _ai.get("ebay_title", "")
                    st.session_state["reg_desc_en"] = _ai.get("description_plain", "")
                    st.success("✅ 英語タイトル・説明文をフォームに適用しました")
                    st.rerun()
                if _ai_btn_c2.button(
                    "🗑️ AI結果をクリア",
                    key="clear_ai", use_container_width=True,
                ):
                    st.session_state["ai_content"] = None
                    st.rerun()

        # ══════════════════════════════════════════════════════
        #  🖼️ 画像管理パネル（フォーム外）
        # ══════════════════════════════════════════════════════
        _imgs_jp  = _pf.get("images_jp", []) or []
        _imgs_us  = _pf.get("images_us", []) or []
        _asin_imgs = _pf.get("images", []) or []   # 後方互換（both 未対応時）
        _has_dual  = bool(_imgs_jp) or bool(_imgs_us)

        _confirmed_count = len(st.session_state.get("reg_images", _asin_imgs))
        with st.expander(
            f"🖼️ 画像管理（{_confirmed_count} 枚選択中）",
            expanded=_has_dual or bool(_asin_imgs),
        ):
            # ─ JP/US タブ切り替え ─
            if _has_dual:
                st.caption(
                    f"🇯🇵 JP画像 {len(_imgs_jp)}枚  /  🇺🇸 US画像 {len(_imgs_us)}枚  "
                    "— チェックした画像を選択。1枚目がメイン画像。"
                )
                _itab_us, _itab_jp, _itab_all = st.tabs(
                    [f"🇺🇸 US画像 ({len(_imgs_us)}枚)",
                     f"🇯🇵 JP画像 ({len(_imgs_jp)}枚)",
                     "📋 選択済みまとめ"]
                )

                def _render_img_tab(tab, imgs: list, key_prefix: str) -> list:
                    sel = []
                    if not imgs:
                        tab.info("画像が取得されていません")
                        return sel
                    _tab_cols = tab.columns(min(len(imgs), 3))
                    for _ii, _url in enumerate(imgs):
                        with _tab_cols[_ii % 3]:
                            try:
                                st.image(_url, use_container_width=True)
                            except Exception:
                                st.caption(f"画像{_ii+1}")
                            if st.checkbox(
                                f"{'📌 メイン' if _ii == 0 else f'{_ii+1}枚目'}",
                                value=True,
                                key=f"{key_prefix}_{_ii}",
                            ):
                                sel.append(_url)
                    return sel

                _sel_us = _render_img_tab(_itab_us, _imgs_us, "chk_us")
                _sel_jp = _render_img_tab(_itab_jp, _imgs_jp, "chk_jp")
                # US優先でマージ（重複除去）
                _selected_dual = _sel_us + [u for u in _sel_jp if u not in _sel_us]

                with _itab_all:
                    if _selected_dual:
                        st.caption(f"現在の選択: {len(_selected_dual)} 枚")
                        _all_cols = st.columns(min(len(_selected_dual), 3))
                        for _ai2, _au in enumerate(_selected_dual):
                            with _all_cols[_ai2 % 3]:
                                try:
                                    st.image(_au, use_container_width=True)
                                    _dom = "🇺🇸" if _au in _imgs_us else "🇯🇵"
                                    st.caption(f"{_dom} {'📌' if _ai2==0 else f'{_ai2+1}枚目'}")
                                except Exception:
                                    st.caption(f"{_ai2+1}枚目")
                    else:
                        st.info("画像が選択されていません")

            elif _asin_imgs:
                st.caption("取得済み画像（チェックした画像のみ使用）")
                _selected_dual = []
                _asin_img_cols = st.columns(min(len(_asin_imgs), 3))
                for _ii, _img_url in enumerate(_asin_imgs):
                    with _asin_img_cols[_ii % 3]:
                        try:
                            st.image(_img_url, use_container_width=True)
                        except Exception:
                            st.caption("(プレビュー不可)")
                        if st.checkbox(
                            f"{'📌 メイン' if _ii == 0 else f'{_ii+1}枚目'}",
                            value=True,
                            key=f"img_chk_{_ii}",
                        ):
                            _selected_dual.append(_img_url)
            else:
                _selected_dual = []

            # 手動追加 URL
            _existing_manual = [
                u for u in st.session_state.get("reg_images", [])
                if u not in _imgs_jp and u not in _imgs_us and u not in _asin_imgs
            ]
            _manual_urls_raw = st.text_area(
                "追加画像URL（1行1URL、最大9枚）",
                value="\n".join(_existing_manual),
                height=60,
                key="manual_img_urls",
                placeholder="https://... （手動追加、ASINで取得できない画像用）",
            )
            _manual_urls = [u.strip() for u in _manual_urls_raw.strip().split("\n") if u.strip()]
            _all_imgs_final = (_selected_dual + _manual_urls)[:9]

            # 右側プレビュー + 確定ボタン
            _ic1, _ic2 = st.columns(2)
            if _ic1.button("✅ この画像リストで確定", key="confirm_images", use_container_width=True):
                st.session_state["reg_images"] = _all_imgs_final
                st.success(f"✅ {len(_all_imgs_final)} 枚確定（1枚目がメイン画像）")
            if _ic2.button("🗑️ 選択をリセット", key="reset_images", use_container_width=True):
                st.session_state.pop("reg_images", None)
                st.rerun()

            # 現在確定している画像のプレビュー
            _confirmed_imgs = st.session_state.get("reg_images", [])
            if _confirmed_imgs:
                st.caption(f"✅ 確定済み: {len(_confirmed_imgs)} 枚")
                _conf_cols = st.columns(min(len(_confirmed_imgs), 3))
                for _ci, _cu in enumerate(_confirmed_imgs):
                    with _conf_cols[_ci % 3]:
                        try:
                            st.image(_cu, use_container_width=True)
                            st.caption(f"{'📌 メイン' if _ci == 0 else f'{_ci+1}枚目'}")
                        except Exception:
                            st.caption(f"{_ci+1}枚目")

        # ══════════════════════════════════════════════════════
        #  🏷️ カテゴリ・利益率 （フォーム外 → リアルタイム更新）
        # ══════════════════════════════════════════════════════
        with st.container(border=True):
            _cat_label = "🏷️ カテゴリ・利益率"
            st.markdown(f"**{_cat_label}**")
            _cat_all = [c.value for c in ProductCategory]
            _cat_from_asin = _pf.get("category", "other")
            _cat_persisted = st.session_state.get("reg_category_select", _cat_from_asin)
            # ASINから新しいデータが来たら反映
            if _pf and _cat_from_asin != "other":
                _cat_persisted = _cat_from_asin
            _cat_idx = _cat_all.index(_cat_persisted) if _cat_persisted in _cat_all else 0
            category_val = st.radio(
                "商品カテゴリ *",
                _cat_all,
                index=_cat_idx,
                format_func=lambda v: CATEGORY_LABELS.get(ProductCategory(v), v),
                key="reg_category_select",
                help="カテゴリを変えると利益率・HSコードが自動更新されます",
                horizontal=True,
            )
            _auto_hs = HS_CODE_DEFAULTS.get(category_val, "")
            # 利益率スライダー（カテゴリ変更でリアルタイム更新）
            _cat_rate = get_profit_rate(category_val)
            _rate_col1, _rate_col2 = st.columns([3, 1])
            profit_rate_override = _rate_col1.slider(
                f"個別利益率（{CATEGORY_LABELS.get(ProductCategory(category_val), category_val)} 既定: {_cat_rate*100:.0f}%）",
                0, 60,
                int(_cat_rate * 100), step=5, format="%d%%",
                key="reg_profit_slider",
                help="0% = カテゴリ設定を使用。個別に上書きする場合はここで指定。",
            ) / 100
            _rate_col2.metric("適用利益率", f"{(profit_rate_override or _cat_rate)*100:.0f}%")

        with st.form("product_form", clear_on_submit=True):

            st.subheader("📋 基本情報")
            c1, c2 = st.columns(2)
            name = c1.text_input("商品名 *", value=_pf.get("name", ""), placeholder="例: Apple AirPods Pro")
            sku = c2.text_input("SKU（空欄で自動生成）", placeholder="例: APPLE-APP-001")

            c3, c4 = st.columns(2)
            source_site_val = c3.selectbox(
                "仕入れ元 *",
                [s.value for s in SourceSite],
                format_func=lambda v: SOURCE_LABELS.get(SourceSite(v), v),
                index=0,  # Amazon がデフォルト（ASIN取得後）
            )
            status_val = c4.selectbox(
                "ステータス",
                [s.value for s in ProductStatus],
                format_func=lambda v: STATUS_LABELS.get(ProductStatus(v), v),
                index=3,
            )
            source_url_val = st.text_input("仕入れ元URL", value=_pf.get("url", ""), placeholder="https://...")

            st.subheader("🔖 商品識別番号")
            id_c1, id_c2 = st.columns(2)
            asin_val = rakuten_val = yahoo_val = netsea_val = ""
            if source_site_val == SourceSite.AMAZON.value:
                asin_val = id_c1.text_input("ASIN", value=_pf.get("asin", ""),
                                             placeholder="B0BDHWDR12", max_chars=10)
            elif source_site_val == SourceSite.RAKUTEN.value:
                rakuten_val = id_c1.text_input("楽天商品コード", placeholder="shop:item-001")
            elif source_site_val == SourceSite.YAHOO.value:
                yahoo_val = id_c1.text_input("Yahoo商品コード", placeholder="yahoo-item-001")
            elif source_site_val == SourceSite.NETSEA.value:
                netsea_val = id_c1.text_input("NETSEA商品ID", placeholder="12345678")

            _jan_default = _pf.get("jan_code", "") or ""
            jan_val = id_c2.text_input("JANコード（任意）", value=_jan_default,
                                        placeholder="4901234567890", max_chars=13)
            upc_val = id_c2.text_input("UPCコード（任意）", placeholder="012345678901", max_chars=12)

            st.subheader("📐 重量・サイズ・HSコード")
            w_c1, w_c2, w_c3, w_c4 = st.columns(4)
            _weight_default = _pf.get("weight_g") or 0.0
            weight_g_val = w_c1.number_input("重量 (g)", min_value=0.0, step=10.0,
                                              value=float(_weight_default))
            size_l_val = w_c2.number_input("縦 (cm)", min_value=0.0, step=1.0, value=0.0)
            size_w_val = w_c3.number_input("横 (cm)", min_value=0.0, step=1.0, value=0.0)
            size_h_val = w_c4.number_input("高さ (cm)", min_value=0.0, step=1.0, value=0.0)
            hs_code_val = st.text_input(
                "HSコード（関税分類番号）",
                value=_auto_hs,
                placeholder="カテゴリから自動入力 / 手動で上書き可",
                help="カテゴリに応じて自動補完されます。変更可能。",
            )

            st.subheader("💴 価格・在庫・利益率")
            p_c1, p_c2 = st.columns(2)
            _price_default = _pf.get("price") or 0.0
            cost_price_val    = p_c1.number_input("仕入れ値（円）*", min_value=0.0, step=100.0,
                                                   value=float(_price_default))
            current_stock_val = p_c2.number_input("在庫数", min_value=0, step=1)

            st.subheader("🌍 販売先")
            mp_c1, mp_c2 = st.columns(2)
            target_ebay_val   = mp_c1.checkbox("eBay に出品する")
            target_shopee_val = mp_c2.checkbox("Shopee に出品する")
            target_countries_val = st.multiselect(
                "販売先国",
                options=list(COUNTRY_OPTIONS.keys()),
                format_func=lambda v: COUNTRY_OPTIONS.get(v, v),
                default=["USA"] if target_ebay_val else (["SGP"] if target_shopee_val else []),
            )

            st.subheader("🌏 出品用情報")
            en_c1, en_c2 = st.columns(2)
            # 翻訳パネルの結果を優先して表示
            _default_name_en = (
                st.session_state.get("reg_name_en")
                or st.session_state.get("ai_content", {}) and st.session_state.get("ai_content", {}).get("ebay_title", "")
                or _pf.get("name_en", "")
                or ""
            )
            product_name_en_val = en_c1.text_input(
                "英語商品名",
                value=_default_name_en,
                placeholder="例: Apple AirPods Pro 2nd Gen（翻訳パネルで自動入力）",
            )
            condition_val = en_c2.selectbox(
                "コンディション",
                ["New", "New (Open Box)", "Like New", "Very Good", "Good", "Acceptable"],
            )
            _default_desc_en = (
                st.session_state.get("reg_desc_en")
                or st.session_state.get("ai_content", {}) and st.session_state.get("ai_content", {}).get("description_plain", "")
                or ""
            )
            product_description_en_val = st.text_area(
                "英語商品説明",
                value=_default_desc_en,
                placeholder="Describe the product in English.（翻訳パネルで自動入力）",
                height=80,
            )
            # Shopee TW 用繁体字中国語
            product_name_zh_val = st.text_input(
                "繁体字中国語商品名（Shopee TW用）",
                value=st.session_state.get("reg_name_zh", ""),
                placeholder="台湾Shopee用（翻訳パネルで自動入力）",
            )
            product_desc_zh_val = st.text_area(
                "繁体字中国語商品説明（Shopee TW用）",
                value=st.session_state.get("reg_desc_zh", ""),
                placeholder="台湾Shopee用（翻訳パネルで自動入力）",
                height=60,
            )

            st.subheader("🟧 Shopee 設定")
            sh_c1, sh_c2 = st.columns(2)
            _shopee_cat_opts = list(SHOPEE_CATEGORIES.keys())
            shopee_cat_name = sh_c1.selectbox("Shopee カテゴリ", _shopee_cat_opts, index=0)
            shopee_cat_id   = SHOPEE_CATEGORIES.get(shopee_cat_name, 100599)
            sh_c2.metric("カテゴリID", shopee_cat_id)

            st.subheader("🖼️ 画像（最大9枚）")
            # 画像管理パネルで確定した画像を使用（未確定時は ASIN 画像をそのまま）
            _confirmed_form_imgs = st.session_state.get(
                "reg_images", _pf.get("images", []) or []
            )
            if _confirmed_form_imgs:
                st.caption(f"📌 画像管理パネルで確定済み: {len(_confirmed_form_imgs)} 枚")
                _thumb_cols = st.columns(min(len(_confirmed_form_imgs), 4))
                for _ti, _tu in enumerate(_confirmed_form_imgs[:4]):
                    with _thumb_cols[_ti]:
                        try:
                            st.image(_tu, width=70)
                        except Exception:
                            st.caption(f"画像{_ti+1}")
            else:
                st.caption("「画像管理パネル」で画像を選択してください（フォーム上部）")
            image_urls_val = _confirmed_form_imgs[:9]

            with st.expander("📝 詳細情報（任意）"):
                desc_val  = st.text_area("日本語商品説明", placeholder="内部管理用")
                notes_val = st.text_area("内部メモ")

            submitted = st.form_submit_button("✅ 登録する", type="primary", use_container_width=True)

    # ────── 右カラム: 利益計算プレビュー ──────
    with right:
        st.subheader("💰 利益計算プレビュー")
        st.caption("カテゴリ設定の利益率が自動適用されます")

        preview_cost      = st.session_state.get("preview_cost", 0.0)
        preview_weight    = st.session_state.get("preview_weight", 500.0)
        preview_cat       = st.session_state.get("preview_cat", "electronics")
        preview_countries = st.session_state.get("preview_countries", ["USA", "SGP"])
        preview_profit    = st.session_state.get("preview_profit", 0.25)
        preview_hs        = st.session_state.get("preview_hs", "")
        preview_incoterm  = st.session_state.get("preview_incoterm", "DDP")

        with st.form("preview_form"):
            prev_cost_in = st.number_input("仕入れ値（円）", value=float(preview_cost), step=100.0)
            prev_weight_in = st.number_input("重量 (g)", value=float(preview_weight), step=10.0)
            prev_cat_in = st.radio(
                "カテゴリ",
                [c.value for c in ProductCategory],
                format_func=lambda v: CATEGORY_LABELS.get(ProductCategory(v), v),
                horizontal=True,
            )
            prev_countries_in = st.multiselect(
                "販売先国",
                options=list(COUNTRY_OPTIONS.keys()),
                format_func=lambda v: COUNTRY_OPTIONS.get(v, v),
                default=preview_countries,
            )
            # カテゴリ既定利益率を表示
            _auto_rate = get_profit_rate(prev_cat_in)
            prev_profit_in = st.slider(
                f"利益率（{prev_cat_in}の既定: {_auto_rate*100:.0f}%）",
                5, 60, int(_auto_rate * 100), step=5, format="%d%%",
            ) / 100
            # ── 関税設定 ──────────────────────────
            st.caption("🛃 関税・輸入税設定")
            _hs_default = HS_CODE_DEFAULTS.get(prev_cat_in, "")
            prev_hs_in = st.text_input(
                "HSコード",
                value=preview_hs or _hs_default,
                placeholder="例: 8517.13",
                help="空欄の場合はカテゴリのデフォルト税率を使用",
            )
            prev_incoterm_in = st.radio(
                "関税負担方式",
                ["DDP", "DDU"],
                index=0 if preview_incoterm == "DDP" else 1,
                horizontal=True,
                format_func=lambda v: (
                    "🟢 DDP（セラー負担・販売価格に転嫁）"
                    if v == "DDP"
                    else "🔵 DDU（バイヤー負担・参考表示のみ）"
                ),
                help="DDP: 関税をセラーが払い価格に上乗せ。DDU: バイヤーが通関時に支払い（参考表示）",
            )
            calc_btn = st.form_submit_button("🔄 計算プレビュー更新", use_container_width=True)

        if calc_btn:
            st.session_state.update({
                "preview_cost": prev_cost_in, "preview_weight": prev_weight_in,
                "preview_cat": prev_cat_in, "preview_countries": prev_countries_in,
                "preview_profit": prev_profit_in,
                "preview_hs": prev_hs_in, "preview_incoterm": prev_incoterm_in,
            })
            preview_cost = prev_cost_in; preview_weight = prev_weight_in
            preview_cat = prev_cat_in; preview_countries = prev_countries_in
            preview_profit = prev_profit_in
            preview_hs = prev_hs_in; preview_incoterm = prev_incoterm_in

        if preview_cost > 0 and preview_countries:
            calc = calc_profit_preview(
                preview_cost, preview_weight, 0, 0, 0,
                preview_cat, preview_countries, preview_profit,
                usd_rate, sgd_rate, ebay_fee, shopee_fee, payment_fee, fx_fee,
                twd_rate, myr_rate, php_rate,
                hs_code=preview_hs, duty_bearer=preview_incoterm,
            )
            if calc:
                # ── ヘッダ情報 ──────────────────────────────────
                _bearer = calc.get("duty_bearer", "DDP")
                _bearer_badge = (
                    "🟢 **DDP** — 関税・輸入税はセラー負担（価格に転嫁）"
                    if _bearer == "DDP"
                    else "🔵 **DDU** — 関税・輸入税はバイヤー負担（参考表示）"
                )
                st.caption(_bearer_badge)
                st.markdown(f"**国内送料（推定）:** ¥{calc.get('domestic_ship',0):,.0f}")

                for code, r in calc.get("countries", {}).items():
                    flag = "🇺🇸" if r["is_ebay"] else "🌏"
                    exempt_tag = " ✅免税" if r.get("is_under_de_minimis") else ""
                    with st.expander(
                        f"{flag} {r['country']}（{r['marketplace']}）{exempt_tag}",
                        expanded=True,
                    ):
                        # ── コスト内訳 ─────────────────────────
                        _duty_label = (
                            f"関税 ({r['duty_rate']*100:.1f}%)"
                            if r.get("duty_rate", 0) > 0
                            else "関税"
                        )
                        _vat_label = (
                            f"{r['vat_name'] or 'VAT'} ({r['vat_rate']*100:.1f}%)"
                            if r.get("vat_rate", 0) > 0
                            else f"{r.get('vat_name', 'VAT')}"
                        )
                        _duty_val = (
                            "¥0（免税）"
                            if r.get("is_under_de_minimis")
                            else f"¥{r['duty_jpy']:,.0f}"
                        )
                        _vat_val = (
                            "¥0（免税）"
                            if r.get("is_under_de_minimis")
                            else f"¥{r['vat_jpy']:,.0f}"
                        )
                        breakdown_rows = [
                            ("仕入れ値",           f"¥{r['cost']:,.0f}"),
                            ("国内送料",           f"¥{r['domestic_ship']:,.0f}"),
                            ("国際送料",           f"¥{r['intl_ship']:,.0f}"),
                            (_duty_label,          _duty_val),
                            (_vat_label,           _vat_val),
                            (f"{r['marketplace']}手数料 ({r['fee_rate']*100:.1f}%)",
                                                   f"¥{r.get('fee_jpy', r['price_jpy']*r['fee_rate']):,.0f}"),
                        ]
                        for lbl, val in breakdown_rows:
                            cc1, cc2 = st.columns([2, 1])
                            cc1.caption(lbl)
                            cc2.caption(val)

                        # 免税メモ
                        if r.get("de_minimis_info"):
                            st.caption(f"ℹ️ {r['de_minimis_info']}")
                        # DDU バイヤー負担
                        if _bearer == "DDU" and r.get("buyer_burden_jpy", 0) > 0:
                            st.caption(
                                f"📦 バイヤー負担（関税+{r.get('vat_name','VAT')}）: "
                                f"¥{r['buyer_burden_jpy']:,.0f}（参考）"
                            )

                        st.divider()

                        # ── 推奨価格・利益 ──────────────────────
                        if r["is_ebay"]:
                            st.markdown(f"**推奨販売価格: ${r['price_usd']:,.2f} USD**"
                                        f" （¥{r['price_jpy']:,.0f}）")
                        else:
                            price_lines = [f"S${r['price_sgd']:,.2f} SGD"]
                            if code == "TWN":  price_lines.append(f"NT${r['price_twd']:,.0f} TWD")
                            elif code == "MYS": price_lines.append(f"RM{r['price_myr']:,.2f} MYR")
                            elif code == "PHL": price_lines.append(f"₱{r['price_php']:,.0f} PHP")
                            st.markdown(
                                "**推奨販売価格: " + " / ".join(price_lines) + "**"
                                + f" （¥{r['price_jpy']:,.0f}）"
                            )

                        _profit_color = "🟢" if r["profit_jpy"] >= 0 else "🔴"
                        st.markdown(
                            f"{_profit_color} **純利益: ¥{r['profit_jpy']:,.0f}"
                            f" （利益率 {r['profit_rate']*100:.0f}%）**"
                        )

                # ── サマリ ──────────────────────────────────────
                st.divider()
                if calc.get("avg_price_usd"):
                    st.success(f"✅ eBay 推奨: **${calc['avg_price_usd']:,.2f} USD**")
                if calc.get("avg_price_sgd"):
                    st.success(f"✅ Shopee SGP: **S${calc['avg_price_sgd']:,.2f}**")
                if calc.get("avg_price_twd"):
                    st.success(f"✅ Shopee TWN: **NT${calc['avg_price_twd']:,.0f}**")
                if calc.get("avg_price_myr"):
                    st.success(f"✅ Shopee MYS: **RM{calc['avg_price_myr']:,.2f}**")
                if calc.get("avg_price_php"):
                    st.success(f"✅ Shopee PHL: **₱{calc['avg_price_php']:,.0f}**")
        else:
            st.info("仕入れ値と販売先国を入力して「計算プレビュー更新」を押してください。")

    # ────── フォーム送信 ──────
    if submitted:
        errors = []
        if not name: errors.append("商品名")
        if not sku:  errors.append("SKU")
        if not errors:
            try:
                # 利益率確定（個別 > カテゴリ）
                final_profit = profit_rate_override if profit_rate_override > 0 else get_profit_rate(category_val)
                calc_prices = {}
                if cost_price_val > 0 and target_countries_val:
                    _reg_hs = st.session_state.get("preview_hs", "")
                    _reg_incoterm = st.session_state.get("preview_incoterm", "DDP")
                    calc_prices = calc_profit_preview(
                        cost_price_val, weight_g_val or 500,
                        size_l_val, size_w_val, size_h_val,
                        category_val, target_countries_val,
                        final_profit, usd_rate, sgd_rate,
                        ebay_fee, shopee_fee, payment_fee, fx_fee,
                        twd_rate, myr_rate, php_rate,
                        hs_code=_reg_hs, duty_bearer=_reg_incoterm,
                    )

                avg_usd = calc_prices.get("avg_price_usd")
                avg_sgd = calc_prices.get("avg_price_sgd")
                avg_twd = calc_prices.get("avg_price_twd")
                avg_myr = calc_prices.get("avg_price_myr")
                avg_php = calc_prices.get("avg_price_php")

                dom_ship = calculate_domestic_shipping(
                    weight_g_val or 500, size_l_val, size_w_val, size_h_val
                ).fee_jpy
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
                        calc_selling_price_twd=avg_twd,
                        calc_selling_price_myr=avg_myr,
                        calc_selling_price_php=avg_php,
                        markup_rate=1.0 + final_profit,
                        target_profit_rate=profit_rate_override if profit_rate_override > 0 else None,
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
                        domestic_shipping_cost=dom_ship,
                        description=desc_val or None,
                        image_url=_main_img,
                        image_urls=image_urls_val if image_urls_val else None,
                        notes=notes_val or None,
                        status=ProductStatus(status_val),
                        ebay_fee_rate=ebay_fee,
                        shopee_fee_rate=shopee_fee,
                        payment_fee_rate=payment_fee,
                        product_name_en=product_name_en_val or None,
                        product_description_en=product_description_en_val or None,
                        shopee_category_id=shopee_cat_id,
                        condition=condition_val,
                    )
                    sess.add(prod)
                    sess.commit()

                st.success(f"✅ 「{name}」を登録しました！（SKU: {sku}）")
                if avg_usd: st.info(f"💵 eBay 推奨: **${avg_usd:,.2f} USD**")
                if avg_sgd: st.info(f"💵 Shopee SGP: **S${avg_sgd:,.2f}**")
                if avg_twd: st.info(f"💵 Shopee TWN: **NT${avg_twd:,.0f}**")
                if avg_myr: st.info(f"💵 Shopee MYS: **RM{avg_myr:,.2f}**")
                if avg_php: st.info(f"💵 Shopee PHL: **₱{avg_php:,.0f}**")
                # セッション状態をクリア
                for _k in ["asin_prefill", "reg_images", "reg_name_en", "reg_name_zh",
                           "reg_desc_en", "reg_desc_zh", "reg_desc_jp", "ai_content"]:
                    st.session_state.pop(_k, None)
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

    tab1, tab2, tab3 = st.tabs(["🟦 eBay", "🟧 Shopee（SGP/MYS/PHL）", "🟥 Shopee TW（台湾）"])

    with tab1:
        st.subheader("eBay 出品状況")

        import backend.marketplaces.ebay as _ebay_mod
        importlib.reload(_ebay_mod)
        _ebay = _ebay_mod.EbayClient()

        if not _ebay.is_configured():
            st.warning("⚠️ eBay APIキーが未設定です。「⚙️ 設定」→「🟦 eBay API」で入力してください。")
        else:
            st.success("✅ eBay API設定済み")

        with get_session() as s:
            ebay_listed  = s.query(Product).filter(Product.ebay_listing_id.isnot(None)).all()
            ebay_pending = s.query(Product).filter(
                Product.target_ebay == True,
                Product.ebay_listing_id.is_(None),
            ).all()

        _usd_rate = float(get_env("DEFAULT_EXCHANGE_RATE_USD", "150"))
        col_l, col_r = st.columns(2)
        col_l.metric("DB内: 出品済", len(ebay_listed))
        col_r.metric("未出品（eBay対象）", len(ebay_pending))

        st.divider()
        if st.button("🔄 eBay出品リストを取得", key="fetch_ebay_list"):
            if not _ebay.is_configured():
                st.error("APIキーを設定してください")
            else:
                with st.spinner("eBay APIから取得中..."):
                    live_listings, err = _ebay.get_active_listings()
                if err:
                    st.error(f"取得失敗: {err}")
                else:
                    st.session_state["ebay_live_listings"] = live_listings
                    st.success(f"eBay出品中: {len(live_listings)} 件")

        if st.session_state.get("ebay_live_listings"):
            live = st.session_state["ebay_live_listings"]
            st.markdown(f"#### 📋 eBay出品中 ({len(live)}件)")
            live_df = pd.DataFrame([{
                "Item ID":  item.item_id,
                "タイトル": item.title[:45] + "…" if len(item.title) > 45 else item.title,
                "価格(USD)": f"${item.price_usd:,.2f}",
                "在庫":     item.quantity,
                "売済":     item.quantity_sold,
                "終了":     item.end_time or "GTC",
            } for item in live])
            st.dataframe(live_df, use_container_width=True, hide_index=True)

            with st.expander("⚙️ 個別操作"):
                _op_id = st.text_input("操作する Item ID", placeholder="123456789012")
                _op_c = st.columns(3)
                _new_price = _op_c[0].number_input("新価格 (USD)", min_value=0.01, step=0.5, value=10.0)
                if _op_c[1].button("💰 価格更新", key="ebay_upd_price"):
                    if _op_id:
                        r = _ebay.update_price(_op_id, _new_price)
                        st.success(f"✅ 価格更新 ${_new_price:.2f}") if r.success else st.error(r.error)
                if _op_c[2].button("🛑 出品停止", key="ebay_end"):
                    if _op_id:
                        r = _ebay.end_listing(_op_id)
                        if r.success:
                            st.success("✅ 停止しました")
                            with get_session() as s:
                                p = s.query(Product).filter(Product.ebay_listing_id == _op_id).first()
                                if p: p.ebay_listing_id = None; p.status = ProductStatus.DRAFT; s.commit()
                        else:
                            st.error(r.error)

        if ebay_listed:
            st.divider()
            st.markdown("#### 🗄️ DB内: 出品済み商品")
            st.dataframe(pd.DataFrame([{
                "SKU": p.sku, "商品名": p.name[:40],
                "listing_id": p.ebay_listing_id,
                "推奨USD": f"${p.calc_selling_price_usd:,.2f}" if p.calc_selling_price_usd else "—",
                "在庫": p.current_stock,
            } for p in ebay_listed]), use_container_width=True, hide_index=True)

        if ebay_pending:
            st.divider()
            st.markdown("#### 📤 未出品（eBay対象）商品")
            for p in ebay_pending:
                with st.container(border=True):
                    ec1, ec2, ec3, ec4 = st.columns([3, 1.5, 1.5, 1])
                    ec1.markdown(f"**{p.name[:38]}**  \n`{p.sku}`")
                    pf = calc_profit_for_product(p)
                    _price_default = round(float(pf.get("price_usd") or p.cost_price / _usd_rate * 1.3), 2)
                    _price_key = f"pending_price_{p.id}"
                    if _price_key not in st.session_state:
                        st.session_state[_price_key] = _price_default
                    _price_show = ec2.number_input("価格(USD)", min_value=0.01, step=0.5,
                                                    value=st.session_state[_price_key], key=_price_key)
                    ec3.metric("在庫", p.current_stock)
                    if ec4.button("🚀 出品", key=f"ebay_list_{p.id}", type="primary"):
                        if not _ebay.is_configured():
                            st.error("APIキーを設定してください")
                        else:
                            with st.spinner(f"「{p.name[:20]}」出品中..."):
                                result = _ebay.create_listing(p, _price_show)
                            if result.success:
                                with get_session() as s:
                                    _p = s.query(Product).filter(Product.id == p.id).first()
                                    _p.ebay_listing_id = result.listing_id
                                    _p.selling_price_usd = _price_show
                                    _p.status = ProductStatus.ACTIVE
                                    s.commit()
                                st.success(f"✅ 出品完了！ ID: `{result.listing_id}`")
                                st.rerun()
                            else:
                                st.error(f"出品失敗: {result.error}")

        st.divider()
        with st.expander("🔧 eBay API 接続テスト"):
            if st.button("接続テスト実行", key="ebay_test_mgmt"):
                with st.spinner("接続中..."):
                    ok, msg = _ebay.test_connection()
                st.success(msg) if ok else st.error(msg)

    with tab2:
        st.subheader("Shopee 出品状況")
        with get_session() as s:
            shopee_listed  = s.query(Product).filter(Product.shopee_item_id.isnot(None)).all()
            shopee_pending = s.query(Product).filter(
                Product.target_shopee == True, Product.shopee_item_id.is_(None)).all()

        col_l, col_r = st.columns(2)
        col_l.metric("出品中", len(shopee_listed))
        col_r.metric("未出品（対象）", len(shopee_pending))

        if shopee_listed:
            st.markdown("#### 出品中")
            st.dataframe(pd.DataFrame([{
                "SKU": p.sku, "商品名": p.name[:40],
                "item_id": p.shopee_item_id,
                "推奨SGD": f"S${p.calc_selling_price_sgd:,.2f}" if p.calc_selling_price_sgd else "—",
                "在庫": p.current_stock,
            } for p in shopee_listed]), use_container_width=True, hide_index=True)

        if shopee_pending:
            st.markdown("#### 未出品（Shopee対象）")
            for p in shopee_pending:
                sc1, sc2, sc3 = st.columns([3, 1, 1])
                sc1.write(f"**{p.name[:40]}** ({p.sku})")
                if sc3.button("出品", key=f"shopee_list_{p.id}"):
                    st.session_state["listing_target"] = (p.id, "shopee")
                    st.session_state["show_listing_modal"] = True
                    st.rerun()

        with st.expander("🔧 Shopee 接続テスト"):
            if st.button("接続テスト", key="shopee_test"):
                import backend.marketplaces.shopee as shopee_mod
                importlib.reload(shopee_mod)
                ok, msg = shopee_mod.ShopeeClient().test_connection()
                st.success(msg) if ok else st.error(msg)

    # ─── Shopee TW タブ ───────────────────────────────────────────
    with tab3:
        st.subheader("🟥 Shopee TW（台湾）出品管理")
        st.caption("shopee.tw | 通貨: NT$（TWD） | 台湾Shopeeの出品・CSV管理")

        rates_tw = get_all_rates()
        twd_rate = rates_tw["TWD"]

        # 対象商品
        with get_session() as s:
            tw_products = s.query(Product).filter(
                Product.target_shopee == True
            ).order_by(Product.updated_at.desc()).all()

        tw_listed   = [p for p in tw_products if p.shopee_item_id]
        tw_pending  = [p for p in tw_products if not p.shopee_item_id]

        tw_c1, tw_c2, tw_c3 = st.columns(3)
        tw_c1.metric("Shopee TW 対象商品", len(tw_products))
        tw_c2.metric("出品済み（Shopee）", len(tw_listed))
        tw_c3.metric("未出品", len(tw_pending))
        st.info(f"💱 TWD/JPY レート: 1 TWD = ¥{twd_rate} | 設定画面で変更可能")

        st.divider()

        # TWD 推奨価格テーブル
        if tw_products:
            st.markdown("#### 📊 台湾向け推奨価格")
            tw_rows = []
            for p in tw_products:
                pf = calc_profit_for_product(p)
                price_twd = p.calc_selling_price_twd or (pf.get("price_twd") if pf else None)
                tw_rows.append({
                    "SKU":      p.sku,
                    "商品名":   p.name[:35] + ("…" if len(p.name) > 35 else ""),
                    "仕入れ値": f"¥{p.cost_price:,.0f}",
                    "推奨TWD":  f"NT${price_twd:,.0f}" if price_twd else "—",
                    "利益率":   f"{pf.get('profit_rate', 0)*100:.1f}%" if pf else "—",
                    "在庫":     p.current_stock,
                    "英語名":   p.product_name_en[:30] if p.product_name_en else "—",
                    "繁体字":   (st.session_state.get(f"tw_name_{p.id}") or "")[:20] or "—",
                })
            st.dataframe(
                pd.DataFrame(tw_rows), use_container_width=True, hide_index=True
            )

        st.divider()
        # CSV エクスポート
        st.markdown("#### 📥 Shopee TW 一括出品 CSV")
        tw_export_c1, tw_export_c2 = st.columns(2)
        _tw_rate_input = tw_export_c1.number_input(
            "TWD/JPY レート", value=twd_rate, step=0.1, format="%.2f"
        )
        if tw_export_c2.button("⬇️ shopee_TWN_YYYYMMDD.csv をダウンロード",
                               use_container_width=True, type="primary"):
            from backend.exporters.shopee_csv import export_shopee_csv
            if tw_products:
                _tw_csv = export_shopee_csv(tw_products, country_code="TWN", rate=_tw_rate_input)
                st.download_button(
                    "📥 CSV ダウンロード",
                    data=_tw_csv,
                    file_name=f"shopee_TWN_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.warning("Shopee対象商品がありません")

        st.divider()
        # 台湾向け関税情報
        st.markdown("#### 🛃 台湾向け関税・送料目安")
        with st.container(border=True):
            st.markdown("""
| 商品カテゴリ | 関税率 | 備考 |
|---|---|---|
| 一般消費財 | 5% | 申告価格ベース |
| 電子機器 | 0% | WTO協定品目 |
| 衣類・繊維 | 10〜12% | カテゴリによる |
| 食品 | 3〜10% | 品目による |
| 化粧品 | 2〜5% | 品目による |
""")
            st.caption("実際の関税率は税関申告時に異なる場合があります。詳細は台湾財政部関税局でご確認ください。")

        tw_ship_c1, tw_ship_c2 = st.columns(2)
        tw_ship_c1.info(
            "**EMS（日本郵便）**  \n"
            "500g: 約 ¥2,200  \n"
            "1kg: 約 ¥3,100  \n"
            "2kg: 約 ¥4,600  \n"
            "所要日数: 3〜7日"
        )
        tw_ship_c2.info(
            "**FedEx / DHL**  \n"
            "500g: 約 ¥3,500〜  \n"
            "1kg: 約 ¥4,500〜  \n"
            "2kg: 約 ¥6,000〜  \n"
            "所要日数: 1〜3日"
        )

    # 出品モーダル
    if st.session_state.get("show_listing_modal") and st.session_state.get("listing_target"):
        pid, platform = st.session_state["listing_target"]
        st.divider()
        with st.container(border=True):
            st.subheader(f"🚀 {'eBay' if platform == 'ebay' else 'Shopee'} 出品実行")
            with get_session() as s:
                p = s.query(Product).filter(Product.id == pid).first()
            if p:
                pf = calc_profit_for_product(p)
                st.write(f"**{p.name}** ({p.sku}) / 在庫: {p.current_stock}")
                if platform == "ebay":
                    price_default = pf.get("price_usd") or round(float(p.cost_price) / 150 * 1.5, 2)
                    price_in = st.number_input("販売価格 (USD)", value=round(float(price_default), 2),
                                               step=0.5, key="modal_price")
                else:
                    price_default = pf.get("price_sgd") or round(float(p.cost_price) / 112 * 1.5, 2)
                    price_in = st.number_input("販売価格 (SGD)", value=round(float(price_default), 2),
                                               step=0.5, key="modal_price_sgd")

                btn_ok, btn_cancel = st.columns(2)
                if btn_ok.button("✅ 出品する", type="primary", key="modal_ok"):
                    with st.spinner("出品処理中..."):
                        try:
                            if platform == "ebay":
                                import backend.marketplaces.ebay as _em
                                importlib.reload(_em)
                                client = _em.EbayClient()
                                if not client.is_configured():
                                    st.error("eBay APIキーが未設定です")
                                else:
                                    result = client.create_listing(p, price_in)
                                    if result.success:
                                        with get_session() as s2:
                                            pp = s2.query(Product).filter(Product.id == pid).first()
                                            pp.ebay_listing_id = result.listing_id
                                            pp.selling_price_usd = price_in
                                            pp.status = ProductStatus.ACTIVE
                                            s2.commit()
                                        st.success(f"✅ 出品完了！ ID: {result.listing_id}")
                                        st.session_state["show_listing_modal"] = False
                                    else:
                                        st.error(f"エラー: {result.error}")
                            else:
                                import backend.marketplaces.shopee as sm
                                importlib.reload(sm)
                                client = sm.ShopeeClient()
                                if not client.is_configured():
                                    st.error("Shopee APIキーが未設定です")
                                else:
                                    result = client.add_item(p, price_in)
                                    if result.success:
                                        with get_session() as s2:
                                            pp = s2.query(Product).filter(Product.id == pid).first()
                                            pp.shopee_item_id = str(result.item_id)
                                            pp.selling_price_sgd = price_in
                                            pp.status = ProductStatus.ACTIVE
                                            s2.commit()
                                        st.success(f"✅ 出品完了！")
                                        st.session_state["show_listing_modal"] = False
                                    else:
                                        st.error(f"エラー: {result.error}")
                        except Exception as ex:
                            st.error(f"例外: {ex}")

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

    tab_ebay, tab_shopee, tab_profit, tab_price, tab_logistics, tab_ai, tab_misc = st.tabs([
        "🟦 eBay API", "🟧 Shopee API", "📊 利益率設定",
        "💴 価格・手数料", "🚚 物流設定", "🌐 翻訳・AI設定", "🔧 その他",
    ])

    # ─── eBay API ───
    with tab_ebay:
        st.subheader("eBay Trading API キー")
        st.markdown("取得先: [eBay Developer Portal](https://developer.ebay.com/my/keys)")

        _all_set = all([get_env(k) for k in ["EBAY_APP_ID","EBAY_DEV_ID","EBAY_CERT_ID","EBAY_USER_TOKEN"]])
        st.success("✅ 全APIキー設定済み") if _all_set else st.warning("⚠️ 未設定キーあり")

        with st.form("ebay_form"):
            e1 = st.text_input("EBAY_APP_ID",  value=get_env("EBAY_APP_ID"),  type="password")
            e2 = st.text_input("EBAY_DEV_ID",  value=get_env("EBAY_DEV_ID"),  type="password")
            e3 = st.text_input("EBAY_CERT_ID", value=get_env("EBAY_CERT_ID"), type="password")
            e4 = st.text_area("EBAY_USER_TOKEN", value=get_env("EBAY_USER_TOKEN"), height=100)
            e_site    = st.selectbox("EBAY_SITE_ID", ["0 (US)","3 (UK)","77 (DE)","15 (AU)"], index=0)
            e_sandbox = st.checkbox("🧪 サンドボックス", value=get_env("EBAY_SANDBOX","false")=="true")
            if st.form_submit_button("💾 eBay設定を保存", type="primary"):
                for k, v in [("EBAY_APP_ID",e1),("EBAY_DEV_ID",e2),("EBAY_CERT_ID",e3),
                              ("EBAY_USER_TOKEN",e4),
                              ("EBAY_SITE_ID", e_site.split(" ")[0]),
                              ("EBAY_SANDBOX","true" if e_sandbox else "false")]:
                    if v: save_env(k, v)
                st.success("✅ 保存しました"); st.rerun()

        _tok = get_env("EBAY_USER_TOKEN")
        if _tok:
            _tlen = len(_tok)
            if _tlen < 200:
                st.error(f"⚠️ トークン {_tlen}文字（正常: 350〜500文字）")
            elif _tlen < 300:
                st.warning(f"⚠️ トークン {_tlen}文字（やや短め）")
            else:
                st.info(f"✅ トークン長: {_tlen}文字（正常）")

        st.divider()
        if st.button("eBay 接続テスト", type="primary", key="ebay_conn_test"):
            import backend.marketplaces.ebay as ebay_mod
            importlib.reload(ebay_mod)
            with st.spinner("接続中..."):
                ok, msg = ebay_mod.EbayClient().test_connection()
            st.success(msg) if ok else st.error(msg)

    # ─── Shopee API ───
    with tab_shopee:
        st.subheader("Shopee Open Platform API")
        st.caption("https://open.shopee.com")
        with st.form("shopee_form"):
            s1 = st.text_input("SHOPEE_PARTNER_ID",  value=get_env("SHOPEE_PARTNER_ID"))
            s2 = st.text_input("SHOPEE_PARTNER_KEY", value=get_env("SHOPEE_PARTNER_KEY"), type="password")
            s3 = st.text_input("SHOPEE_SHOP_ID",     value=get_env("SHOPEE_SHOP_ID"))
            s4 = st.text_area("SHOPEE_ACCESS_TOKEN", value=get_env("SHOPEE_ACCESS_TOKEN"), height=80)
            s_sb = st.checkbox("サンドボックス", value=get_env("SHOPEE_SANDBOX","false")=="true")
            if st.form_submit_button("💾 Shopee設定を保存", type="primary"):
                for k, v in [("SHOPEE_PARTNER_ID",s1),("SHOPEE_PARTNER_KEY",s2),
                              ("SHOPEE_SHOP_ID",s3),("SHOPEE_ACCESS_TOKEN",s4),
                              ("SHOPEE_SANDBOX","true" if s_sb else "false")]:
                    if v: save_env(k, v)
                st.success("✅ 保存しました"); st.rerun()
        st.divider()
        if st.button("🔌 Shopee 接続テスト"):
            import backend.marketplaces.shopee as sm
            importlib.reload(sm)
            ok, msg = sm.ShopeeClient().test_connection()
            st.success(msg) if ok else st.error(msg)

    # ─── 利益率設定 ───
    with tab_profit:
        st.subheader("📊 利益率設定")
        st.caption("優先順位: 商品個別 > カテゴリ設定 > デフォルト")

        with st.form("profit_form"):
            st.markdown("**デフォルト利益率**")
            default_rate = st.slider(
                "全カテゴリ共通デフォルト",
                min_value=5, max_value=60,
                value=int(float(get_env("DEFAULT_PROFIT_RATE","0.25"))*100),
                step=5, format="%d%%",
            ) / 100

            st.markdown("**カテゴリ別利益率**")
            cat_rates: dict = {}
            cols = st.columns(3)
            cat_items = list(CATEGORY_PROFIT_KEYS.items())
            for idx, (cat_val, env_key) in enumerate(cat_items):
                col = cols[idx % 3]
                label = CATEGORY_LABELS.get(ProductCategory(cat_val), cat_val)
                cur_val = int(float(get_env(env_key, str(default_rate))) * 100)
                cat_rates[env_key] = col.slider(
                    label, 5, 60, cur_val, step=5, format="%d%%",
                    key=f"cat_rate_{cat_val}",
                ) / 100

            st.markdown("**販売先別追加マージン**")
            pm_c1, pm_c2 = st.columns(2)
            ebay_margin  = pm_c1.slider("eBay 追加マージン",  0, 15,
                int(float(get_env("PROFIT_MARGIN_EBAY","0.03"))*100), step=1, format="%d%%") / 100
            shopee_margin = pm_c2.slider("Shopee 追加マージン", 0, 15,
                int(float(get_env("PROFIT_MARGIN_SHOPEE","0.00"))*100), step=1, format="%d%%") / 100

            if st.form_submit_button("💾 利益率設定を保存", type="primary"):
                save_env("DEFAULT_PROFIT_RATE", str(default_rate))
                for env_key, rate in cat_rates.items():
                    save_env(env_key, str(rate))
                save_env("PROFIT_MARGIN_EBAY",   str(ebay_margin))
                save_env("PROFIT_MARGIN_SHOPEE", str(shopee_margin))
                st.success("✅ 利益率設定を保存しました")
                st.rerun()

        st.divider()
        st.markdown("**現在の設定サマリ**")
        summary_rows = [{"カテゴリ": CATEGORY_LABELS.get(ProductCategory(cv), cv),
                          "利益率": f"{float(get_env(ek, str(float(get_env('DEFAULT_PROFIT_RATE','0.25')))))*100:.0f}%"}
                         for cv, ek in CATEGORY_PROFIT_KEYS.items()]
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # ─── 価格・手数料 ───
    with tab_price:
        st.subheader("価格・為替・手数料設定")
        with st.form("price_form"):
            st.markdown("**為替レート（JPY換算）**")
            r_c1, r_c2, r_c3 = st.columns(3)
            usd = r_c1.number_input("USD レート", value=float(get_env("DEFAULT_EXCHANGE_RATE_USD","150")), step=1.0)
            sgd = r_c2.number_input("SGD レート", value=float(get_env("DEFAULT_EXCHANGE_RATE_SGD","112")), step=1.0)
            twd = r_c3.number_input("TWD レート（台湾）1TWD=円", value=float(get_env("DEFAULT_EXCHANGE_RATE_TWD","4.5")), step=0.1, help="1 TWD = X 円。台湾Shopee用。デフォルト: 4.5")
            r_c4, r_c5 = st.columns(2)
            myr = r_c4.number_input("MYR レート（マレーシア）", value=float(get_env("DEFAULT_EXCHANGE_RATE_MYR","33.0")), step=0.5)
            php = r_c5.number_input("PHP レート（フィリピン）", value=float(get_env("DEFAULT_EXCHANGE_RATE_PHP","2.7")), step=0.1)

            st.markdown("**手数料率**")
            fc1, fc2, fc3, fc4 = st.columns(4)
            ebay_f  = fc1.number_input("eBay手数料(%)", value=float(get_env("EBAY_FEE_RATE","0.13"))*100, step=0.5, min_value=0.0) / 100
            shop_f  = fc2.number_input("Shopee手数料(%)", value=float(get_env("SHOPEE_FEE_RATE","0.06"))*100, step=0.5, min_value=0.0) / 100
            pay_f   = fc3.number_input("決済手数料(%)", value=float(get_env("PAYMENT_FEE_RATE","0.044"))*100, step=0.1, min_value=0.0) / 100
            fx_f    = fc4.number_input("為替手数料(%)", value=float(get_env("FX_FEE_RATE","0.02"))*100, step=0.1, min_value=0.0) / 100

            if st.form_submit_button("💾 価格設定を保存", type="primary"):
                for k, v in [
                    ("DEFAULT_EXCHANGE_RATE_USD", str(usd)),
                    ("DEFAULT_EXCHANGE_RATE_SGD", str(sgd)),
                    ("DEFAULT_EXCHANGE_RATE_TWD", str(twd)),
                    ("DEFAULT_EXCHANGE_RATE_MYR", str(myr)),
                    ("DEFAULT_EXCHANGE_RATE_PHP", str(php)),
                    ("EBAY_FEE_RATE", str(ebay_f)),
                    ("SHOPEE_FEE_RATE", str(shop_f)),
                    ("PAYMENT_FEE_RATE", str(pay_f)),
                    ("FX_FEE_RATE", str(fx_f)),
                ]:
                    save_env(k, v)
                st.success("✅ 価格設定を保存しました"); st.rerun()

    # ─── 物流設定 ───
    with tab_logistics:
        st.subheader("🚚 物流 API 設定")

        log_tab1, log_tab2, log_tab3 = st.tabs(["FedEx", "イーロジコム", "シーパス"])

        with log_tab1:
            st.markdown("**FedEx Ship API**  \n[FedEx Developer Portal](https://developer.fedex.com/)")
            with st.form("fedex_form"):
                fx1 = st.text_input("FEDEX_CLIENT_ID",     value=get_env("FEDEX_CLIENT_ID"),     type="password")
                fx2 = st.text_input("FEDEX_CLIENT_SECRET", value=get_env("FEDEX_CLIENT_SECRET"), type="password")
                fx3 = st.text_input("FEDEX_ACCOUNT_NUMBER",value=get_env("FEDEX_ACCOUNT_NUMBER"))
                fx_sb = st.checkbox("🧪 FedEx サンドボックス", value=get_env("FEDEX_SANDBOX","false")=="true")
                if st.form_submit_button("💾 FedEx設定を保存", type="primary"):
                    for k, v in [("FEDEX_CLIENT_ID",fx1),("FEDEX_CLIENT_SECRET",fx2),
                                  ("FEDEX_ACCOUNT_NUMBER",fx3),
                                  ("FEDEX_SANDBOX","true" if fx_sb else "false")]:
                        if v: save_env(k, v)
                    st.success("✅ 保存しました"); st.rerun()
            if st.button("🔌 FedEx 接続テスト"):
                from backend.logistics.fedex import FedExClient
                ok, msg = FedExClient().test_connection()
                st.success(msg) if ok else st.error(msg)

            # 送料見積もりテスト
            with st.expander("📦 FedEx 送料見積もりテスト"):
                tc1, tc2 = st.columns(2)
                _fw = tc1.number_input("重量 (kg)", 0.1, 30.0, 1.0, 0.1, key="fedex_weight")
                _fc = tc2.selectbox("仕向け国", list(COUNTRY_OPTIONS.keys()),
                                     format_func=lambda c: COUNTRY_OPTIONS.get(c, c), key="fedex_country")
                if st.button("見積もり計算", key="fedex_rate_test"):
                    from backend.logistics.fedex import FedExClient
                    client = FedExClient()
                    if not client.is_configured():
                        st.warning("FedEx APIキーを設定してください")
                    else:
                        with st.spinner("FedEx API に問い合わせ中..."):
                            rates, err = client.get_international_rate(_fw, _fc)
                        if err:
                            st.error(err)
                        else:
                            for r in rates:
                                st.write(f"**{r.service_name}**: {r.currency} {r.total_charge:,.2f}"
                                         + (f" ({r.transit_days}日)" if r.transit_days else ""))

        with log_tab2:
            st.markdown("**イーロジコム（elogicom）**  \nhttps://www.elogicom.jp/")
            with st.form("elogicom_form"):
                el1 = st.text_input("ELOGICOM_API_KEY",   value=get_env("ELOGICOM_API_KEY"),   type="password")
                el2 = st.text_input("ELOGICOM_SHOP_CODE", value=get_env("ELOGICOM_SHOP_CODE"))
                el_sb = st.checkbox("🧪 イーロジコム サンドボックス", value=get_env("ELOGICOM_SANDBOX","false")=="true")
                if st.form_submit_button("💾 イーロジコム設定を保存", type="primary"):
                    for k, v in [("ELOGICOM_API_KEY",el1),("ELOGICOM_SHOP_CODE",el2),
                                  ("ELOGICOM_SANDBOX","true" if el_sb else "false")]:
                        if v: save_env(k, v)
                    st.success("✅ 保存しました"); st.rerun()
            if st.button("🔌 イーロジコム 接続テスト"):
                from backend.logistics.elogicom import ElogicomClient
                ok, msg = ElogicomClient().test_connection()
                st.success(msg) if ok else st.error(msg)

            # 在庫照会テスト
            with st.expander("📦 在庫照会テスト"):
                _el_sku = st.text_input("SKU", placeholder="APPLE-APP-001", key="el_sku")
                if st.button("在庫照会", key="el_stock_test"):
                    from backend.logistics.elogicom import ElogicomClient
                    client = ElogicomClient()
                    if not client.is_configured():
                        st.warning("イーロジコム APIキーを設定してください")
                    else:
                        item, err = client.get_stock_by_sku(_el_sku)
                        if err:
                            st.error(err)
                        elif item:
                            st.success(f"在庫: {item.available_qty} / 総数: {item.total_qty}")

        with log_tab3:
            st.markdown("**シーパス（Seapass）国際配送**  \nhttps://www.seapass.co.jp/")
            with st.form("seapass_form"):
                sp1 = st.text_input("SEAPASS_API_KEY",       value=get_env("SEAPASS_API_KEY"),       type="password")
                sp2 = st.text_input("SEAPASS_CUSTOMER_CODE", value=get_env("SEAPASS_CUSTOMER_CODE"))
                sp_sb = st.checkbox("🧪 シーパス サンドボックス", value=get_env("SEAPASS_SANDBOX","false")=="true")
                if st.form_submit_button("💾 シーパス設定を保存", type="primary"):
                    for k, v in [("SEAPASS_API_KEY",sp1),("SEAPASS_CUSTOMER_CODE",sp2),
                                  ("SEAPASS_SANDBOX","true" if sp_sb else "false")]:
                        if v: save_env(k, v)
                    st.success("✅ 保存しました"); st.rerun()
            if st.button("🔌 シーパス 接続テスト"):
                from backend.logistics.seapass import SeapassClient
                ok, msg = SeapassClient().test_connection()
                st.success(msg) if ok else st.error(msg)

            # 送料計算テスト
            with st.expander("📦 シーパス 送料計算テスト"):
                sp_c1, sp_c2 = st.columns(2)
                _sp_w = sp_c1.number_input("重量 (kg)", 0.1, 30.0, 0.5, 0.1, key="sp_weight")
                _sp_c = sp_c2.selectbox("仕向け国", list(COUNTRY_OPTIONS.keys()),
                                         format_func=lambda c: COUNTRY_OPTIONS.get(c, c), key="sp_country")
                if st.button("送料計算", key="sp_rate_test"):
                    from backend.logistics.seapass import SeapassClient
                    client = SeapassClient()
                    if not client.is_configured():
                        st.warning("シーパス APIキーを設定してください")
                    else:
                        with st.spinner("シーパス API に問い合わせ中..."):
                            rates, err = client.calculate_rate(_sp_w, _sp_c)
                        if err:
                            st.error(err)
                        else:
                            for r in rates:
                                days = (f"{r.transit_days_min}〜{r.transit_days_max}日"
                                        if r.transit_days_min else "日数不明")
                                st.write(f"**{r.service_name}**: ¥{r.charge_jpy:,.0f} / {days}")

    # ─── 翻訳・AI設定 ───
    with tab_ai:
        st.subheader("🌐 翻訳・AI設定")
        st.caption("商品名・商品説明の自動翻訳とAIコンテンツ生成に使用します")

        with st.form("ai_settings_form"):
            st.markdown("#### DeepL API（高品質翻訳）")
            st.markdown(
                "取得先: [DeepL API](https://www.deepl.com/pro-api) — Free プランは月50万文字まで無料"
            )
            _deepl_key = st.text_input(
                "DEEPL_API_KEY",
                value=get_env("DEEPL_API_KEY"),
                type="password",
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx  (FreeはキーがFxで終わる)",
            )
            st.markdown("#### Anthropic API（AI英語コンテンツ生成）")
            st.markdown(
                "取得先: [Anthropic Console](https://console.anthropic.com/) — "
                "eBay/Shopee用タイトル・説明文を日本語から自動生成"
            )
            _anthropic_key = st.text_input(
                "ANTHROPIC_API_KEY",
                value=get_env("ANTHROPIC_API_KEY"),
                type="password",
                placeholder="sk-ant-api03-...",
            )
            _ai_model = st.selectbox(
                "使用モデル",
                ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
                index=1,
                help="Sonnet がコストと品質のバランスが良い",
            )
            if st.form_submit_button("💾 保存", type="primary"):
                if _deepl_key:    save_env("DEEPL_API_KEY", _deepl_key)
                if _anthropic_key: save_env("ANTHROPIC_API_KEY", _anthropic_key)
                if _ai_model:      save_env("AI_MODEL", _ai_model)
                st.success("✅ 保存しました")

        st.divider()
        st.markdown("**接続テスト**")
        test_c1, test_c2 = st.columns(2)

        if test_c1.button("🌐 DeepL 翻訳テスト", use_container_width=True):
            if not get_env("DEEPL_API_KEY"):
                st.error("DeepL APIキーが設定されていません")
            else:
                with st.spinner("翻訳テスト中..."):
                    from backend.translators.translate import translate_to_english
                    _test, _method = translate_to_english("テスト翻訳です。これはGlobalBizです。")
                if _test:
                    st.success(f"✅ DeepL ({_method}): {_test}")
                else:
                    st.error("翻訳失敗。APIキーを確認してください。")

        if test_c2.button("✨ Claude API テスト", use_container_width=True):
            if not get_env("ANTHROPIC_API_KEY"):
                st.error("Anthropic APIキーが設定されていません")
            else:
                with st.spinner("Claude API テスト中..."):
                    from backend.ai.content_gen import generate_listing_content
                    _test_result = generate_listing_content(
                        "テスト商品 高品質ワイヤレスイヤホン",
                        "日本製の高品質ワイヤレスイヤホン",
                        "electronics",
                    )
                if _test_result.get("error"):
                    st.error(f"❌ {_test_result['error']}")
                else:
                    st.success(f"✅ eBay タイトル: {_test_result.get('ebay_title','')}")

        st.divider()
        st.markdown("**繁体字中国語翻訳テスト（Shopee TW用）**")
        if st.button("🀄 繁体字中国語テスト", use_container_width=True):
            with st.spinner("翻訳テスト中..."):
                from backend.translators.translate import translate_to_traditional_chinese
                _zh_test, _zh_m = translate_to_traditional_chinese("高品質ワイヤレスイヤホン 日本製")
            if _zh_test:
                st.success(f"✅ 繁体字 ({_zh_m}): {_zh_test}")
            else:
                st.warning("翻訳失敗（DeepL/Google翻訳が利用できない可能性があります）")

    # ─── その他 ───
    with tab_misc:
        st.subheader("仕入れAPI設定")
        with st.form("misc_form"):
            mc1, mc2 = st.columns(2)
            rak_id = mc1.text_input("楽天 APP_ID",       value=get_env("RAKUTEN_APP_ID"))
            yah_id = mc2.text_input("Yahoo CLIENT_ID",   value=get_env("YAHOO_CLIENT_ID"))
            amz_id = mc1.text_input("Amazon CLIENT_ID",  value=get_env("AMAZON_CLIENT_ID"))
            net_em = mc2.text_input("NETSEA EMAIL",       value=get_env("NETSEA_EMAIL"))
            if st.form_submit_button("💾 保存"):
                for k, v in [("RAKUTEN_APP_ID",rak_id),("YAHOO_CLIENT_ID",yah_id),
                              ("AMAZON_CLIENT_ID",amz_id),("NETSEA_EMAIL",net_em)]:
                    if v: save_env(k, v)
                st.success("✅ 保存しました")

        st.divider()
        st.subheader("DB情報")
        with get_session() as s:
            total = s.query(Product).count()
        st.write(f"登録商品数: {total} 件")
        st.write(f"DB パス: {Path(__file__).parent.parent / 'globalbiz.db'}")
        st.caption("DB マイグレーション: `python3 scripts/migrate_db.py`")


# ══════════════════════════════════════════════════════════════════
#  PAGE: 商品編集
# ══════════════════════════════════════════════════════════════════
elif page == "✏️ 商品編集":
    st.title("✏️ 商品編集・削除")

    rates = get_all_rates()
    ebay_fee    = float(get_env("EBAY_FEE_RATE", "0.13"))
    shopee_fee  = float(get_env("SHOPEE_FEE_RATE", "0.06"))
    payment_fee = float(get_env("PAYMENT_FEE_RATE", "0.044"))
    fx_fee      = float(get_env("FX_FEE_RATE", "0.02"))

    # ── 商品選択 ──
    with get_session() as s:
        all_products = s.query(Product).order_by(Product.updated_at.desc()).all()

    if not all_products:
        st.info("登録商品がありません。「➕ 商品登録」から追加してください。")
        st.stop()

    product_options = {f"[{p.id}] {p.sku}  {p.name[:40]}": p.id for p in all_products}
    selected_label = st.selectbox("編集する商品を選択", list(product_options.keys()),
                                   key="edit_product_select")
    edit_id = product_options[selected_label]

    with get_session() as s:
        ep = s.query(Product).filter(Product.id == edit_id).first()

    if not ep:
        st.error("商品が見つかりません")
        st.stop()

    st.caption(f"登録日: {ep.created_at}  |  最終更新: {ep.updated_at}")

    # ── 商品画像ギャラリー ──
    _edit_imgs = get_product_all_images(ep)
    if _edit_imgs:
        with st.expander(f"🖼️ 商品画像ギャラリー（{len(_edit_imgs)} 枚）", expanded=True):
            if len(_edit_imgs) == 1:
                try:
                    st.image(_edit_imgs[0], width=300)
                except Exception:
                    st.caption(_edit_imgs[0][:80])
            else:
                _gal_main, _gal_subs = st.columns([2, 3])
                with _gal_main:
                    st.caption("📌 メイン画像")
                    try:
                        st.image(_edit_imgs[0], use_container_width=True)
                    except Exception:
                        st.caption(_edit_imgs[0][:80])
                with _gal_subs:
                    st.caption(f"サブ画像（全 {len(_edit_imgs)} 枚）")
                    _sub_cols = st.columns(min(len(_edit_imgs) - 1, 3))
                    for _gi, _gurl in enumerate(_edit_imgs[1:9], 1):
                        with _sub_cols[(_gi - 1) % 3]:
                            try:
                                st.image(_gurl, use_container_width=True)
                            except Exception:
                                pass
                            _g_dom = (
                                "🇺🇸 US" if "amazon.com/" in _gurl and ".co.jp" not in _gurl
                                else "🇯🇵 JP" if "amazon" in _gurl
                                else "🌐"
                            )
                            st.caption(f"{_g_dom}  {_gi + 1}枚目")
    else:
        st.caption("📷 画像未登録（商品登録画面でASINから取得できます）")

    # ── タブ: 基本情報 / 価格・在庫 / 出品設定 / 危険操作 ──
    et1, et2, et3, et4, et5 = st.tabs(["📋 基本情報", "💴 価格・在庫", "🌍 出品設定", "🖼️ 画像管理", "⚠️ 危険操作"])

    # ─── 基本情報 ───
    with et1:
        with st.form("edit_basic_form"):
            ec1, ec2 = st.columns(2)
            new_name = ec1.text_input("商品名 *", value=ep.name)
            new_sku  = ec2.text_input("SKU *", value=ep.sku)

            ec3, ec4 = st.columns(2)
            new_source = ec3.selectbox(
                "仕入れ元",
                [s.value for s in SourceSite],
                format_func=lambda v: SOURCE_LABELS.get(SourceSite(v), v),
                index=[s.value for s in SourceSite].index(
                    ep.source_site.value if hasattr(ep.source_site, "value") else ep.source_site
                ),
            )
            new_status = ec4.selectbox(
                "ステータス",
                [s.value for s in ProductStatus],
                format_func=lambda v: STATUS_LABELS.get(ProductStatus(v), v),
                index=[s.value for s in ProductStatus].index(
                    ep.status.value if hasattr(ep.status, "value") else ep.status
                ),
            )
            new_url = st.text_input("仕入れ元URL", value=ep.source_url or "")

            st.markdown("**識別番号**")
            id1, id2 = st.columns(2)
            new_asin    = id1.text_input("ASIN",     value=ep.asin or "")
            new_jan     = id2.text_input("JANコード", value=ep.jan_code or "")
            new_upc     = id1.text_input("UPCコード", value=ep.upc_code or "")
            new_rak     = id2.text_input("楽天商品コード", value=ep.rakuten_item_code or "")

            st.markdown("**カテゴリ・サイズ**")
            cat_c1, cat_c2 = st.columns(2)
            cat_list = [c.value for c in ProductCategory]
            new_cat = cat_c1.selectbox(
                "カテゴリ",
                cat_list,
                format_func=lambda v: CATEGORY_LABELS.get(ProductCategory(v), v),
                index=cat_list.index(
                    ep.product_category.value if ep.product_category else "other"
                ),
            )
            new_hs = cat_c2.text_input("HSコード", value=ep.hs_code or "")
            w1, w2, w3, w4 = st.columns(4)
            new_wt = w1.number_input("重量(g)", value=float(ep.weight_g or 0), step=10.0)
            new_sl = w2.number_input("縦(cm)", value=float(ep.size_cm_l or 0), step=1.0)
            new_sw = w3.number_input("横(cm)", value=float(ep.size_cm_w or 0), step=1.0)
            new_sh = w4.number_input("高さ(cm)", value=float(ep.size_cm_h or 0), step=1.0)

            st.markdown("**英語・出品用情報**")
            en1, en2 = st.columns(2)
            new_name_en = en1.text_input("英語商品名", value=ep.product_name_en or "")
            new_cond    = en2.selectbox(
                "コンディション",
                ["New","New (Open Box)","Like New","Very Good","Good","Acceptable"],
                index=["New","New (Open Box)","Like New","Very Good","Good","Acceptable"].index(
                    ep.condition or "New"
                ) if ep.condition in ["New","New (Open Box)","Like New","Very Good","Good","Acceptable"] else 0,
            )
            new_desc_en = st.text_area("英語説明", value=ep.product_description_en or "", height=80)
            new_desc_ja = st.text_area("日本語説明", value=ep.description or "", height=60)
            new_notes   = st.text_area("内部メモ", value=ep.notes or "", height=60)

            if st.form_submit_button("💾 基本情報を保存", type="primary", use_container_width=True):
                with get_session() as s:
                    p = s.query(Product).filter(Product.id == edit_id).first()
                    p.name = new_name
                    p.sku  = new_sku
                    p.source_site = SourceSite(new_source)
                    p.status      = ProductStatus(new_status)
                    p.source_url  = new_url or None
                    p.asin        = new_asin or None
                    p.jan_code    = new_jan or None
                    p.upc_code    = new_upc or None
                    p.rakuten_item_code = new_rak or None
                    p.product_category  = ProductCategory(new_cat)
                    p.hs_code     = new_hs or None
                    p.weight_g    = new_wt if new_wt > 0 else None
                    p.size_cm_l   = new_sl if new_sl > 0 else None
                    p.size_cm_w   = new_sw if new_sw > 0 else None
                    p.size_cm_h   = new_sh if new_sh > 0 else None
                    p.product_name_en          = new_name_en or None
                    p.condition                = new_cond
                    p.product_description_en   = new_desc_en or None
                    p.description              = new_desc_ja or None
                    p.notes                    = new_notes or None
                    p.updated_at               = datetime.utcnow()
                    s.commit()
                st.success("✅ 基本情報を保存しました")
                st.rerun()

    # ─── 価格・在庫 ───
    with et2:
        with st.form("edit_price_form"):
            pc1, pc2 = st.columns(2)
            new_cost  = pc1.number_input("仕入れ値（円）", value=float(ep.cost_price or 0), step=100.0)
            new_stock = pc2.number_input("在庫数", value=int(ep.current_stock or 0), step=1)
            new_alert = pc2.number_input("在庫アラート閾値", value=int(ep.min_stock_alert or 1), step=1)

            st.markdown("**推奨価格（手動上書き可）**")
            pr1, pr2, pr3 = st.columns(3)
            new_usd = pr1.number_input("推奨 USD", value=float(ep.calc_selling_price_usd or 0), step=0.5)
            new_sgd = pr2.number_input("推奨 SGD", value=float(ep.calc_selling_price_sgd or 0), step=0.5)
            new_twd = pr3.number_input("推奨 TWD", value=float(ep.calc_selling_price_twd or 0), step=1.0)
            pr4, pr5 = st.columns(2)
            new_myr = pr4.number_input("推奨 MYR", value=float(ep.calc_selling_price_myr or 0), step=0.5)
            new_php = pr5.number_input("推奨 PHP", value=float(ep.calc_selling_price_php or 0), step=1.0)

            st.markdown("**利益率**")
            cur_profit = float(ep.target_profit_rate or 0)
            new_profit = st.slider(
                "個別利益率（0%=カテゴリ設定を使用）",
                0, 60, int(cur_profit * 100), step=5, format="%d%%",
            ) / 100

            recalc = st.checkbox("✨ 仕入れ値から推奨価格を再計算する", value=False)

            if st.form_submit_button("💾 価格・在庫を保存", type="primary", use_container_width=True):
                with get_session() as s:
                    p = s.query(Product).filter(Product.id == edit_id).first()
                    p.cost_price      = new_cost
                    p.current_stock   = new_stock
                    p.min_stock_alert = new_alert
                    p.target_profit_rate = new_profit if new_profit > 0 else None

                    if recalc and new_cost > 0:
                        # 推奨価格を再計算
                        cat_val = p.product_category.value if p.product_category else "other"
                        pr = new_profit if new_profit > 0 else get_profit_rate(cat_val)
                        ebay_total  = ebay_fee + payment_fee + fx_fee
                        shop_total  = shopee_fee + payment_fee + fx_fee
                        dom_ship    = float(p.domestic_shipping_cost or 0)
                        total_cost  = new_cost + dom_ship
                        denom_e = 1 - ebay_total - pr
                        denom_s = 1 - shop_total - pr
                        price_jpy_e = total_cost / denom_e if denom_e > 0 else total_cost * 2
                        price_jpy_s = total_cost / denom_s if denom_s > 0 else total_cost * 2
                        p.calc_selling_price_usd = round(price_jpy_e / rates["USD"], 2)
                        p.calc_selling_price_sgd = round(price_jpy_s / rates["SGD"], 2)
                        p.calc_selling_price_twd = round(price_jpy_s / rates["TWD"], 0)
                        p.calc_selling_price_myr = round(price_jpy_s / rates["MYR"], 2)
                        p.calc_selling_price_php = round(price_jpy_s / rates["PHP"], 0)
                    else:
                        p.calc_selling_price_usd = new_usd if new_usd > 0 else None
                        p.calc_selling_price_sgd = new_sgd if new_sgd > 0 else None
                        p.calc_selling_price_twd = new_twd if new_twd > 0 else None
                        p.calc_selling_price_myr = new_myr if new_myr > 0 else None
                        p.calc_selling_price_php = new_php if new_php > 0 else None

                    p.updated_at = datetime.utcnow()
                    s.commit()
                st.success("✅ 価格・在庫を保存しました")
                st.rerun()

    # ─── 出品設定 ───
    with et3:
        with st.form("edit_listing_form"):
            ml1, ml2 = st.columns(2)
            new_ebay_flag   = ml1.checkbox("eBay に出品する",   value=bool(ep.target_ebay))
            new_shopee_flag = ml2.checkbox("Shopee に出品する", value=bool(ep.target_shopee))

            cur_countries = ep.target_countries or []
            new_countries = st.multiselect(
                "販売先国",
                options=list(COUNTRY_OPTIONS.keys()),
                default=[c for c in cur_countries if c in COUNTRY_OPTIONS],
                format_func=lambda v: COUNTRY_OPTIONS.get(v, v),
            )

            st.markdown("**出品中情報（手動更新）**")
            lc1, lc2 = st.columns(2)
            new_ebay_id   = lc1.text_input("eBay listing_id", value=ep.ebay_listing_id or "")
            new_shopee_id = lc2.text_input("Shopee item_id",  value=ep.shopee_item_id or "")

            # Shopee カテゴリ
            _shopee_opts = list(SHOPEE_CATEGORIES.keys())
            _cur_shopee_idx = 0
            if ep.shopee_category_id:
                for _i, (_k, _v) in enumerate(SHOPEE_CATEGORIES.items()):
                    if _v == ep.shopee_category_id:
                        _cur_shopee_idx = _i; break
            new_shopee_cat_name = st.selectbox("Shopee カテゴリ", _shopee_opts, index=_cur_shopee_idx)
            new_shopee_cat_id   = SHOPEE_CATEGORIES.get(new_shopee_cat_name, 100599)

            if st.form_submit_button("💾 出品設定を保存", type="primary", use_container_width=True):
                with get_session() as s:
                    p = s.query(Product).filter(Product.id == edit_id).first()
                    p.target_ebay      = new_ebay_flag
                    p.target_shopee    = new_shopee_flag
                    p.target_countries = new_countries or None
                    p.ebay_listing_id  = new_ebay_id or None
                    p.shopee_item_id   = new_shopee_id or None
                    p.shopee_category_id = new_shopee_cat_id
                    p.updated_at = datetime.utcnow()
                    s.commit()
                st.success("✅ 出品設定を保存しました")
                st.rerun()

    # ─── 画像管理 ───
    with et4:
        st.subheader("🖼️ 商品画像の管理")
        _cur_imgs = get_product_all_images(ep)

        # 現在の画像グリッド表示
        if _cur_imgs:
            st.caption(f"現在登録中: {len(_cur_imgs)} 枚  |  1枚目がメイン画像")
            _img_mgmt_cols = st.columns(min(len(_cur_imgs), 3))
            for _mi, _murl in enumerate(_cur_imgs):
                with _img_mgmt_cols[_mi % 3]:
                    try:
                        st.image(_murl, use_container_width=True)
                    except Exception:
                        st.caption("(表示不可)")
                    _m_dom = (
                        "🇺🇸 US" if "amazon.com/" in _murl and ".co.jp" not in _murl
                        else "🇯🇵 JP" if "amazon" in _murl
                        else "🌐"
                    )
                    st.caption(f"{_m_dom}  {'📌 メイン' if _mi == 0 else f'{_mi+1}枚目'}")
        else:
            st.info("画像が登録されていません")

        st.divider()

        # 画像URLリスト編集
        with st.form("edit_images_form"):
            st.markdown("**画像URLを編集（1行1URL、最大9枚、1枚目がメイン画像）**")
            _cur_txt = "\n".join(_cur_imgs)
            new_img_txt = st.text_area(
                "画像URLリスト",
                value=_cur_txt,
                height=200,
                key="edit_img_urls",
                help="URLを1行に1つ入力。順番が表示順になります。",
            )
            # JP/USプレビュー
            _new_img_list = [u.strip() for u in new_img_txt.strip().split("\n") if u.strip()][:9]
            st.caption(f"入力中: {len(_new_img_list)} 枚")

            _ei_c1, _ei_c2 = st.columns(2)
            if _ei_c1.form_submit_button("💾 画像リストを保存", type="primary", use_container_width=True):
                with get_session() as s:
                    _ep2 = s.query(Product).filter(Product.id == edit_id).first()
                    _ep2.image_urls = _new_img_list if _new_img_list else None
                    _ep2.image_url  = _new_img_list[0] if _new_img_list else None
                    _ep2.updated_at = datetime.utcnow()
                    s.commit()
                st.success(f"✅ 画像 {len(_new_img_list)} 枚を保存しました")
                st.rerun()
            if _ei_c2.form_submit_button("🗑️ 画像をすべて削除", use_container_width=True):
                with get_session() as s:
                    _ep2 = s.query(Product).filter(Product.id == edit_id).first()
                    _ep2.image_urls = None
                    _ep2.image_url  = None
                    _ep2.updated_at = datetime.utcnow()
                    s.commit()
                st.success("✅ 画像をすべて削除しました")
                st.rerun()

        # 入力中プレビュー（フォーム外）
        if _new_img_list if 'new_img_txt' in dir() else _cur_imgs:
            _preview_list = [u.strip() for u in st.session_state.get("edit_img_urls", "").split("\n") if u.strip()][:9] or _cur_imgs
            if _preview_list:
                with st.expander("🔍 入力中の画像プレビュー", expanded=False):
                    _prv_c = st.columns(min(len(_preview_list), 3))
                    for _pi, _pu in enumerate(_preview_list):
                        with _prv_c[_pi % 3]:
                            try:
                                st.image(_pu, use_container_width=True)
                                st.caption(f"{'📌 メイン' if _pi == 0 else f'{_pi+1}枚目'}")
                            except Exception:
                                st.caption(f"画像{_pi+1}: 表示不可")

    # ─── 危険操作 ───
    with et5:
        st.warning("⚠️ この操作は取り消せません")

        # eBay 出品停止
        if ep.ebay_listing_id:
            st.markdown(f"**eBay 出品中** (ID: `{ep.ebay_listing_id}`)")
            if st.button("🛑 eBay 出品を停止する", key="edit_end_ebay"):
                import backend.marketplaces.ebay as _em
                importlib.reload(_em)
                client = _em.EbayClient()
                if client.is_configured():
                    r = client.end_listing(ep.ebay_listing_id)
                    if r.success:
                        with get_session() as s:
                            p = s.query(Product).filter(Product.id == edit_id).first()
                            p.ebay_listing_id = None
                            p.status = ProductStatus.DRAFT
                            s.commit()
                        st.success("✅ eBay 出品を停止しました")
                        st.rerun()
                    else:
                        st.error(f"停止失敗: {r.error}")
                else:
                    st.error("eBay APIキーが未設定です")

        st.divider()

        # 商品削除
        st.markdown("#### 🗑️ 商品を削除する")
        st.caption(f"**{ep.name}** ({ep.sku}) をDBから完全に削除します。")
        confirm_text = st.text_input(
            f'確認のため SKU「{ep.sku}」を入力してください',
            placeholder=ep.sku, key="delete_confirm_input",
        )
        if st.button("🗑️ 完全に削除する", type="primary", key="delete_product_btn",
                     disabled=(confirm_text != ep.sku)):
            with get_session() as s:
                p = s.query(Product).filter(Product.id == edit_id).first()
                if p:
                    s.delete(p)
                    s.commit()
            st.success(f"✅ 「{ep.name}」を削除しました")
            st.session_state.pop("edit_product_select", None)
            st.rerun()


# ══════════════════════════════════════════════════════════════════
#  PAGE: バルク CSV インポート
# ══════════════════════════════════════════════════════════════════
elif page == "📥 インポート":
    st.title("📥 商品インポート")
    st.caption("CSVファイルで商品を一括登録できます。")

    rates = get_all_rates()
    ebay_fee    = float(get_env("EBAY_FEE_RATE", "0.13"))
    shopee_fee  = float(get_env("SHOPEE_FEE_RATE", "0.06"))
    payment_fee = float(get_env("PAYMENT_FEE_RATE", "0.044"))
    fx_fee      = float(get_env("FX_FEE_RATE", "0.02"))

    imp_tab1, imp_tab2, imp_tab3 = st.tabs(
        ["📤 CSVアップロード", "🔍 仕入れ元検索", "📄 テンプレート"]
    )

    # ─── CSVアップロード ───
    with imp_tab1:
        st.subheader("CSV一括インポート")

        uploaded = st.file_uploader(
            "CSVファイルを選択（UTF-8 / UTF-8 BOM / Shift_JIS対応）",
            type=["csv"], key="import_csv",
        )

        if uploaded:
            # エンコーディング自動検出
            raw = uploaded.read()
            for enc in ["utf-8-sig", "utf-8", "shift_jis", "cp932"]:
                try:
                    df_preview = pd.read_csv(
                        __import__("io").BytesIO(raw), encoding=enc, dtype=str
                    )
                    st.success(f"✅ 読み込み成功（{enc}、{len(df_preview)} 行）")
                    break
                except Exception:
                    continue
            else:
                st.error("❌ ファイルの文字コードを判別できませんでした")
                st.stop()

            # 列名マッピング（日英両対応）
            COL_MAP = {
                "商品名": "name", "name": "name",
                "SKU": "sku", "sku": "sku",
                "仕入れ値": "cost_price", "cost_price": "cost_price",
                "仕入れ元": "source_site", "source_site": "source_site",
                "在庫数": "current_stock", "current_stock": "current_stock",
                "ASIN": "asin", "asin": "asin",
                "JANコード": "jan_code", "jan_code": "jan_code",
                "JAN": "jan_code",
                "UPCコード": "upc_code", "upc_code": "upc_code",
                "UPC": "upc_code",
                "楽天商品コード": "rakuten_item_code",
                "英語商品名": "product_name_en", "product_name_en": "product_name_en",
                "カテゴリ": "product_category", "category": "product_category",
                "重量(g)": "weight_g", "weight_g": "weight_g",
                "仕入れ元URL": "source_url", "source_url": "source_url",
                "eBay出品": "target_ebay", "target_ebay": "target_ebay",
                "Shopee出品": "target_shopee", "target_shopee": "target_shopee",
                "メモ": "notes", "notes": "notes",
            }
            df_preview.rename(
                columns={c: COL_MAP[c] for c in df_preview.columns if c in COL_MAP},
                inplace=True,
            )

            required = ["name", "sku"]
            missing_req = [c for c in required if c not in df_preview.columns]
            if missing_req:
                st.error(f"❌ 必須列が不足: {missing_req}")
                st.stop()

            # プレビュー
            st.markdown("**プレビュー（先頭5行）**")
            st.dataframe(df_preview.head(), use_container_width=True, hide_index=True)

            dup_skus = []
            with get_session() as s:
                existing_skus = {r[0] for r in s.query(Product.sku).all()}

            new_rows = []
            skip_rows = []
            for _, row in df_preview.iterrows():
                sku_val = str(row.get("sku", "")).strip()
                if not sku_val or sku_val in existing_skus:
                    skip_rows.append(sku_val)
                else:
                    new_rows.append(row)

            st.info(f"登録可能: **{len(new_rows)} 件**  /  スキップ（SKU重複・空）: {len(skip_rows)} 件")

            if new_rows and st.button(f"✅ {len(new_rows)} 件をインポート", type="primary"):
                success_n = 0
                error_msgs = []

                for row in new_rows:
                    try:
                        name_val  = str(row.get("name", "")).strip()
                        sku_val   = str(row.get("sku", "")).strip()
                        cost_val  = float(str(row.get("cost_price", 0)).replace(",", "") or 0)
                        stock_val = int(str(row.get("current_stock", 0)).replace(",", "") or 0)
                        src_raw   = str(row.get("source_site", "manual")).strip().lower()
                        src_val   = src_raw if src_raw in [s.value for s in SourceSite] else "manual"

                        # カテゴリ
                        cat_raw = str(row.get("product_category", "other")).strip().lower()
                        cat_val = cat_raw if cat_raw in [c.value for c in ProductCategory] else "other"

                        # eBay/Shopee
                        def _bool(v):
                            return str(v).strip().lower() in ("1","true","yes","はい","○")

                        target_ebay   = _bool(row.get("target_ebay",   ""))
                        target_shopee = _bool(row.get("target_shopee", ""))

                        # 推奨価格計算
                        pr = get_profit_rate(cat_val)
                        avg_usd = avg_sgd = avg_twd = avg_myr = avg_php = None
                        if cost_val > 0:
                            ebay_total = ebay_fee + payment_fee + fx_fee
                            shop_total = shopee_fee + payment_fee + fx_fee
                            denom_e = 1 - ebay_total - pr
                            denom_s = 1 - shop_total - pr
                            pjpy_e = cost_val / denom_e if denom_e > 0 else cost_val * 2
                            pjpy_s = cost_val / denom_s if denom_s > 0 else cost_val * 2
                            avg_usd = round(pjpy_e / rates["USD"], 2)
                            avg_sgd = round(pjpy_s / rates["SGD"], 2)
                            avg_twd = round(pjpy_s / rates["TWD"], 0)
                            avg_myr = round(pjpy_s / rates["MYR"], 2)
                            avg_php = round(pjpy_s / rates["PHP"], 0)

                        with get_session() as s:
                            prod = Product(
                                name=name_val, sku=sku_val,
                                source_site=SourceSite(src_val),
                                source_url=str(row.get("source_url","")) or None,
                                asin=str(row.get("asin","")).strip() or None,
                                jan_code=str(row.get("jan_code","")).strip() or None,
                                upc_code=str(row.get("upc_code","")).strip() or None,
                                rakuten_item_code=str(row.get("rakuten_item_code","")).strip() or None,
                                cost_price=cost_val,
                                current_stock=stock_val,
                                product_category=ProductCategory(cat_val),
                                weight_g=float(str(row.get("weight_g",0)).replace(",","") or 0) or None,
                                product_name_en=str(row.get("product_name_en","")).strip() or None,
                                notes=str(row.get("notes","")).strip() or None,
                                target_ebay=target_ebay,
                                target_shopee=target_shopee,
                                calc_selling_price_usd=avg_usd,
                                calc_selling_price_sgd=avg_sgd,
                                calc_selling_price_twd=avg_twd,
                                calc_selling_price_myr=avg_myr,
                                calc_selling_price_php=avg_php,
                                status=ProductStatus.DRAFT,
                            )
                            s.add(prod)
                            s.commit()
                        success_n += 1

                    except Exception as e:
                        error_msgs.append(f"SKU:{sku_val} → {e}")

                if success_n:
                    st.success(f"✅ {success_n} 件をインポートしました！")
                    st.balloons()
                for msg in error_msgs[:10]:
                    st.error(msg)

    # ─── 仕入れ元検索 ───
    with imp_tab2:
        st.subheader("🔍 仕入れ元から商品を検索してインポート")

        src_choice = st.radio(
            "検索先", ["🛒 Amazon", "🔴 楽天", "🟣 Yahoo"],
            horizontal=True, key="import_src_radio",
        )
        search_kw = st.text_input("キーワード / 商品コード", placeholder="例: AirPods Pro / B09G9HD6PD",
                                   key="import_search_kw")

        if st.button("🔍 検索", key="import_search_btn", type="primary"):
            if not search_kw:
                st.warning("キーワードを入力してください")
            else:
                with st.spinner("検索中..."):
                    results = []
                    err_msg = None
                    try:
                        if "Amazon" in src_choice:
                            from backend.scrapers.amazon import fetch_product_by_asin, search_products as amz_search
                            # ASIN っぽければ直接取得
                            if len(search_kw) == 10 and search_kw.startswith("B"):
                                r = fetch_product_by_asin(search_kw)
                                results = [r] if not r.get("error") else []
                                err_msg = r.get("error")
                            else:
                                results = amz_search(search_kw, limit=5)
                        elif "楽天" in src_choice:
                            from backend.scrapers.rakuten import search_products as rak_search
                            results = rak_search(search_kw, limit=5)
                        else:
                            from backend.scrapers.yahoo import search_products as yah_search
                            results = yah_search(search_kw, limit=5)
                    except Exception as e:
                        err_msg = str(e)

                if err_msg:
                    st.error(f"❌ {err_msg}")
                elif not results:
                    st.warning("検索結果がありませんでした")
                else:
                    st.session_state["import_search_results"] = results

        if st.session_state.get("import_search_results"):
            results = st.session_state["import_search_results"]
            st.markdown(f"**{len(results)} 件の結果**")

            for i, r in enumerate(results):
                with st.container(border=True):
                    r_c1, r_c2 = st.columns([3, 1])
                    with r_c1:
                        st.markdown(f"**{r.get('name','')[:60]}**")
                        price_txt = f"¥{r['price']:,.0f}" if r.get("price") else "価格不明"
                        st.caption(f"{price_txt}  |  {r.get('url','')[:50]}")
                        if r.get("images"):
                            st.image(r["images"][0], width=80)
                    with r_c2:
                        if st.button("➕ インポート", key=f"import_result_{i}", type="primary"):
                            st.session_state["import_prefill"] = r
                            st.session_state["import_prefill_idx"] = i
                            st.rerun()

            # インポート確認フォーム
            if st.session_state.get("import_prefill"):
                pf = st.session_state["import_prefill"]
                st.divider()
                st.markdown("### 📋 インポート確認")
                with st.form("import_confirm_form"):
                    ic1, ic2 = st.columns(2)
                    imp_name  = ic1.text_input("商品名", value=pf.get("name","")[:100])
                    imp_sku   = ic2.text_input("SKU *", placeholder="自動生成推奨: AUTO-001")
                    imp_cost  = ic1.number_input("仕入れ値（円）", value=float(pf.get("price") or 0), step=100.0)
                    imp_stock = ic2.number_input("在庫数", value=1, step=1)

                    cat_l = [c.value for c in ProductCategory]
                    cat_d = pf.get("category", "other")
                    imp_cat = st.selectbox(
                        "カテゴリ", cat_l,
                        index=cat_l.index(cat_d) if cat_d in cat_l else 0,
                        format_func=lambda v: CATEGORY_LABELS.get(ProductCategory(v), v),
                    )

                    src_map = {"Amazon": "amazon", "楽天": "rakuten", "Yahoo": "yahoo"}
                    src_key = [k for k in src_map if k in src_choice]
                    imp_src = src_map.get(src_key[0] if src_key else "Amazon", "amazon")

                    if st.form_submit_button("✅ インポート確定", type="primary", use_container_width=True):
                        if not imp_sku:
                            st.error("SKUを入力してください")
                        else:
                            try:
                                pr  = get_profit_rate(imp_cat)
                                ebay_total = ebay_fee + payment_fee + fx_fee
                                shop_total = shopee_fee + payment_fee + fx_fee
                                denom_e = 1 - ebay_total - pr
                                denom_s = 1 - shop_total - pr
                                pjpy_e = imp_cost / denom_e if denom_e > 0 and imp_cost > 0 else 0
                                pjpy_s = imp_cost / denom_s if denom_s > 0 and imp_cost > 0 else 0

                                with get_session() as s:
                                    prod = Product(
                                        name=imp_name, sku=imp_sku,
                                        source_site=SourceSite(imp_src),
                                        source_url=pf.get("url") or None,
                                        asin=pf.get("asin") or None,
                                        jan_code=pf.get("jan_code") or None,
                                        cost_price=imp_cost,
                                        current_stock=imp_stock,
                                        product_category=ProductCategory(imp_cat),
                                        weight_g=pf.get("weight_g"),
                                        image_url=pf["images"][0] if pf.get("images") else None,
                                        image_urls=pf.get("images") or None,
                                        calc_selling_price_usd=round(pjpy_e/rates["USD"],2) if pjpy_e else None,
                                        calc_selling_price_sgd=round(pjpy_s/rates["SGD"],2) if pjpy_s else None,
                                        calc_selling_price_twd=round(pjpy_s/rates["TWD"],0) if pjpy_s else None,
                                        calc_selling_price_myr=round(pjpy_s/rates["MYR"],2) if pjpy_s else None,
                                        calc_selling_price_php=round(pjpy_s/rates["PHP"],0) if pjpy_s else None,
                                        status=ProductStatus.DRAFT,
                                    )
                                    s.add(prod); s.commit()
                                st.success(f"✅ 「{imp_name[:40]}」をインポートしました！")
                                st.session_state["import_prefill"] = None
                                st.session_state["import_search_results"] = []
                                st.balloons()
                            except Exception as e:
                                if "UNIQUE constraint" in str(e):
                                    st.error(f"SKU「{imp_sku}」は既に使用されています")
                                else:
                                    st.error(f"エラー: {e}")

    # ─── テンプレート ───
    with imp_tab3:
        st.subheader("📄 インポート用 CSV テンプレート")
        st.caption("このテンプレートをダウンロードして商品情報を記入し、「CSVアップロード」でインポートしてください。")

        template_cols = [
            "name", "sku", "cost_price", "source_site", "current_stock",
            "asin", "jan_code", "upc_code", "rakuten_item_code",
            "product_category", "weight_g", "product_name_en",
            "source_url", "target_ebay", "target_shopee", "notes",
        ]
        sample_data = [
            {
                "name": "Apple AirPods Pro 第2世代",
                "sku": "APPLE-APP-001",
                "cost_price": 28000,
                "source_site": "amazon",
                "current_stock": 5,
                "asin": "B0BDHWDR12",
                "jan_code": "4549995357929",
                "upc_code": "",
                "rakuten_item_code": "",
                "product_category": "electronics",
                "weight_g": 56,
                "product_name_en": "Apple AirPods Pro 2nd Gen",
                "source_url": "https://www.amazon.co.jp/dp/B0BDHWDR12",
                "target_ebay": "true",
                "target_shopee": "false",
                "notes": "サンプルデータ",
            },
            {
                "name": "サンプル商品2",
                "sku": "SAMPLE-002",
                "cost_price": 5000,
                "source_site": "manual",
                "current_stock": 10,
                "asin": "",
                "jan_code": "",
                "upc_code": "",
                "rakuten_item_code": "",
                "product_category": "other",
                "weight_g": 200,
                "product_name_en": "Sample Product 2",
                "source_url": "",
                "target_ebay": "false",
                "target_shopee": "true",
                "notes": "",
            },
        ]

        import io as _io
        _tmpl_buf = _io.StringIO()
        _tmpl_writer = __import__("csv").DictWriter(_tmpl_buf, fieldnames=template_cols,
                                                     lineterminator="\r\n")
        _tmpl_writer.writeheader()
        for _row in sample_data:
            _tmpl_writer.writerow(_row)
        _tmpl_bytes = ("\ufeff" + _tmpl_buf.getvalue()).encode("utf-8")

        st.download_button(
            "⬇️ CSVテンプレートをダウンロード",
            data=_tmpl_bytes,
            file_name="globalbiz_import_template.csv",
            mime="text/csv",
            type="primary", use_container_width=True,
        )

        st.markdown("**カラム説明**")
        col_desc = {
            "name": "商品名（必須）",
            "sku": "SKU（必須・ユニーク）",
            "cost_price": "仕入れ値（円）",
            "source_site": "amazon / rakuten / yahoo / netsea / manual",
            "current_stock": "在庫数（整数）",
            "asin": "Amazon ASIN（B0XXXXXXXX）",
            "jan_code": "JANコード（13桁）",
            "product_category": "electronics / clothing / cosmetics / home / toys / food / health / sports / books / auto / other",
            "weight_g": "重量（グラム）",
            "target_ebay": "true / false",
            "target_shopee": "true / false",
        }
        st.dataframe(
            pd.DataFrame([{"カラム名": k, "説明": v} for k, v in col_desc.items()]),
            use_container_width=True, hide_index=True,
        )


# ══════════════════════════════════════════════════════════════════
#  PAGE: 監視・スケジューラ
# ══════════════════════════════════════════════════════════════════
elif page == "📊 監視・スケジューラ":
    st.title("📊 監視・スケジューラ")

    # スケジューラ取得（@st.cache_resource でシングルトン化）
    @st.cache_resource
    def _get_cached_scheduler():
        from backend.scheduler.monitor import get_scheduler
        return get_scheduler()

    scheduler = _get_cached_scheduler()

    from backend.scheduler.monitor import (
        get_scheduler_status, get_unread_alerts, mark_alerts_read,
        trigger_job_now, ALERTS, JOB_HISTORY,
    )

    # ── ステータスサマリ ──
    unread = get_unread_alerts()
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("スケジューラ状態", "🟢 稼働中" if (scheduler and scheduler.running) else "🔴 停止")
    sc2.metric("未読アラート", len(unread),
               delta=f"+{len(unread)}" if unread else None, delta_color="inverse")
    sc3.metric("実行履歴", len(JOB_HISTORY))

    st.divider()

    mon_tab1, mon_tab2, mon_tab3, mon_tab4 = st.tabs(
        ["⚙️ ジョブ管理", "🔔 アラート", "📜 実行履歴", "🔍 仕入れ元検索"]
    )

    # ─── ジョブ管理 ───
    with mon_tab1:
        st.subheader("定期実行ジョブ")

        if not scheduler or not scheduler.running:
            st.error("スケジューラが起動していません。アプリを再起動してください。")
        else:
            job_status = get_scheduler_status()
            if job_status:
                status_icon = {"success": "✅", "warning": "⚠️", "error": "❌", "未実行": "⏸"}
                rows = []
                for j in job_status:
                    icon = status_icon.get(j["last_status"], "⏸")
                    rows.append({
                        "ジョブ名": j["name"],
                        "実行間隔": j["interval"],
                        "次回実行": j["next_run"],
                        "最終結果": f"{icon} {j['last_status']}",
                        "最終実行": j["last_time"],
                        "最終メッセージ": j["last_message"],
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("**今すぐ実行**")
            jc1, jc2, jc3, jc4 = st.columns(4)
            if jc1.button("在庫確認", use_container_width=True, key="run_stock"):
                from backend.scheduler.monitor import check_stock_levels
                with st.spinner("実行中..."):
                    check_stock_levels()
                st.success("✅ 在庫確認完了"); st.rerun()
            if jc2.button("価格チェック", use_container_width=True, key="run_price"):
                from backend.scheduler.monitor import check_price_changes
                with st.spinner("実行中（時間がかかる場合があります）..."):
                    check_price_changes()
                st.success("✅ 価格チェック完了"); st.rerun()
            if jc3.button("eBay同期", use_container_width=True, key="run_ebay_sync"):
                from backend.scheduler.monitor import sync_ebay_inventory
                with st.spinner("eBay API に問い合わせ中..."):
                    sync_ebay_inventory()
                st.success("✅ eBay同期完了"); st.rerun()
            if jc4.button("日次レポート", use_container_width=True, key="run_daily"):
                from backend.scheduler.monitor import daily_report
                daily_report()
                st.success("✅ 日次レポート生成"); st.rerun()

            # 在庫状況サマリ
            st.divider()
            st.subheader("📦 在庫状況サマリ")
            with get_session() as s:
                all_prods = s.query(Product).all()

            stock_rows = []
            for p in all_prods:
                stock = int(p.current_stock or 0)
                alert = int(p.min_stock_alert or 1)
                flag  = "🔴 ゼロ" if stock == 0 else ("⚠️ 少" if stock <= alert else "✅ 正常")
                stock_rows.append({
                    "SKU": p.sku,
                    "商品名": p.name[:35],
                    "在庫数": stock,
                    "アラート閾値": alert,
                    "状態": flag,
                    "eBay": "🟢" if p.ebay_listing_id else "—",
                    "Shopee": "🟢" if p.shopee_item_id else "—",
                })
            if stock_rows:
                st.dataframe(pd.DataFrame(stock_rows), use_container_width=True, hide_index=True)

    # ─── アラート ───
    with mon_tab2:
        st.subheader("🔔 アラート一覧")

        col_a, col_b = st.columns([3, 1])
        col_a.caption(f"未読: {len(unread)} 件 / 全件: {len(ALERTS)} 件")
        if col_b.button("✅ 全て既読にする", key="mark_read"):
            mark_alerts_read(); st.rerun()

        if not ALERTS:
            st.info("アラートはありません")
        else:
            level_icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}
            for alert in reversed(ALERTS):
                icon = level_icon.get(alert["level"], "⚪")
                read_mark = "" if alert["read"] else "🆕 "
                with st.container(border=True):
                    st.markdown(
                        f"{read_mark}{icon} **{alert['product_name'] or '（全体）'}**  \n"
                        f"{alert['message']}  \n"
                        f"<small style='color:gray'>{alert['timestamp']}</small>",
                        unsafe_allow_html=True,
                    )

    # ─── 実行履歴 ───
    with mon_tab3:
        st.subheader("📜 実行履歴")
        if not JOB_HISTORY:
            st.info("実行履歴がありません。ジョブを実行してください。")
        else:
            status_icon = {"success": "✅", "warning": "⚠️", "error": "❌"}
            hist_rows = [
                {
                    "日時": h["timestamp"],
                    "ジョブ": h["job"],
                    "結果": f"{status_icon.get(h['status'],'⏸')} {h['status']}",
                    "メッセージ": h["message"][:80],
                }
                for h in reversed(JOB_HISTORY)
            ]
            st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)

    # ─── 仕入れ元検索（監視ページ版） ───
    with mon_tab4:
        st.subheader("🔍 価格チェック（手動）")
        st.caption("特定商品の現在の仕入れ価格を今すぐ確認します")

        with get_session() as s:
            check_products = s.query(Product).filter(
                Product.source_url.isnot(None)
            ).order_by(Product.updated_at.desc()).all()

        if not check_products:
            st.info("仕入れ元URLが設定された商品がありません。商品編集で source_url を設定してください。")
        else:
            chk_opts = {f"[{p.id}] {p.sku} – {p.name[:35]}": p for p in check_products}
            chk_sel  = st.selectbox("商品を選択", list(chk_opts.keys()), key="price_check_sel")
            chk_p    = chk_opts[chk_sel]

            col_x, col_y = st.columns(2)
            col_x.metric("現在の仕入れ値", f"¥{chk_p.cost_price:,.0f}")
            col_y.metric("最終確認", str(chk_p.last_checked_at)[:16] if chk_p.last_checked_at else "未確認")

            if st.button("🔍 今すぐ価格確認", type="primary", key="manual_price_check"):
                from backend.scheduler.monitor import _fetch_current_price
                with st.spinner(f"「{chk_p.name[:30]}」の価格を確認中..."):
                    new_price = _fetch_current_price(chk_p)

                if new_price is None:
                    st.warning("価格を取得できませんでした（スクレイピング失敗またはAPIキー未設定）")
                else:
                    old_price = float(chk_p.cost_price or 0)
                    diff = new_price - old_price
                    diff_pct = diff / old_price * 100 if old_price > 0 else 0
                    st.metric("取得した現在価格", f"¥{new_price:,.0f}",
                              delta=f"{diff:+,.0f} ({diff_pct:+.1f}%)" if old_price > 0 else None,
                              delta_color="inverse" if diff > 0 else "normal")

                    if st.button("💾 仕入れ値を更新する", key="update_cost_btn", type="primary"):
                        with get_session() as s:
                            p = s.query(Product).filter(Product.id == chk_p.id).first()
                            p.cost_price = new_price
                            p.last_checked_at = datetime.utcnow()
                            s.commit()
                        st.success(f"✅ 仕入れ値を ¥{new_price:,.0f} に更新しました")
                        st.rerun()
