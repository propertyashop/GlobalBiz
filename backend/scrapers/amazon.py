"""
Amazon.co.jp 商品スクレイパー

指定 ASIN の Amazon 商品ページから:
  - 商品名（日本語）
  - 価格（円）
  - 商品画像 URL（最大9枚）
  - カテゴリパンくず
  - 在庫状況
  - スペック（重量など）

使用方法:
    from backend.scrapers.amazon import fetch_product_by_asin
    result = fetch_product_by_asin("B0BDHWDR12")

注意:
  - Amazon は積極的にボット検出を行います。
  - CAPTCHA や 503 が返った場合は result["error"] に詳細が入ります。
  - 商用利用は Amazon の利用規約を確認してください。
"""

import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── User-Agent ローテーション ──────────────────────────────────────
_USER_AGENTS: List[str] = [
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

# ── カテゴリマッピング（Amazon パンくず → 内部カテゴリ）──────────
_CATEGORY_MAP: Dict[str, str] = {
    "家電": "electronics",
    "カメラ": "electronics",
    "電子": "electronics",
    "パソコン": "electronics",
    "ゲーム": "toys",
    "おもちゃ": "toys",
    "ホビー": "toys",
    "衣類": "clothing",
    "アパレル": "clothing",
    "ファッション": "clothing",
    "靴": "clothing",
    "シューズ": "clothing",
    "腕時計": "accessories",
    "ジュエリー": "accessories",
    "アクセサリ": "accessories",
    "食品": "food",
    "飲料": "food",
    "ドリンク": "food",
    "コスメ": "cosmetics",
    "美容": "cosmetics",
    "スキンケア": "cosmetics",
    "ヘルス": "health",
    "医薬品": "health",
    "サプリ": "health",
    "スポーツ": "sports",
    "アウトドア": "sports",
    "家具": "home",
    "インテリア": "home",
    "キッチン": "home",
    "生活用品": "home",
    "本": "books",
    "書籍": "books",
    "雑誌": "books",
    "CD": "books",
    "DVD": "books",
    "車": "auto",
    "バイク": "auto",
    "自動車": "auto",
}


def _get_headers(ua: Optional[str] = None) -> Dict[str, str]:
    """リクエストヘッダーを返す（UA ランダム or 指定）"""
    return {
        "User-Agent": ua or random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }


def _rate_limit() -> None:
    """1〜3秒のランダム待機（レート制限対応）"""
    wait = random.uniform(1.0, 3.0)
    logger.debug("Rate limit: sleeping %.1f sec", wait)
    time.sleep(wait)


def _extract_title(soup: BeautifulSoup) -> str:
    """商品タイトルを取得"""
    # メイン selector
    title_tag = soup.select_one("#productTitle")
    if title_tag:
        return title_tag.get_text(strip=True)
    # フォールバック
    for sel in ["#title", "h1.a-size-large", "h1[data-feature-name='title']"]:
        tag = soup.select_one(sel)
        if tag:
            return tag.get_text(strip=True)
    return ""


def _extract_price(soup: BeautifulSoup) -> Optional[float]:
    """現在価格を取得（円）"""
    # 新UIの価格 (.a-price .a-offscreen)
    price_tag = soup.select_one(".a-price .a-offscreen")
    if price_tag:
        return _parse_price(price_tag.get_text(strip=True))

    # 旧UI
    for sel in [
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#price_inside_buybox",
        ".a-price-whole",
        "#listPrice",
    ]:
        tag = soup.select_one(sel)
        if tag:
            val = _parse_price(tag.get_text(strip=True))
            if val:
                return val

    # apexPrice
    apex = soup.select_one("span[class*='apexPriceToPay'] .a-offscreen")
    if apex:
        return _parse_price(apex.get_text(strip=True))

    return None


def _parse_price(text: str) -> Optional[float]:
    """「¥1,234」→ 1234.0 に変換"""
    if not text:
        return None
    cleaned = re.sub(r"[¥￥,\s円]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_images(soup: BeautifulSoup) -> List[str]:
    """商品画像URLリストを最大9枚取得"""
    urls: List[str] = []

    # 方法1: script タグ内の JSON に含まれる高解像度画像
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "'colorImages'" in txt or '"colorImages"' in txt or "ImageBlockATF" in txt:
            # JSON-like パターンで大きい画像 URL を抽出
            matches = re.findall(r'"hiRes"\s*:\s*"(https://[^"]+)"', txt)
            for m in matches:
                if m not in urls:
                    urls.append(m)
            if not urls:
                matches = re.findall(r'"large"\s*:\s*"(https://[^"]+)"', txt)
                for m in matches:
                    if m not in urls:
                        urls.append(m)

    # 方法2: img タグ（サムネイルから高解像度URLを生成）
    if not urls:
        # メイン画像
        main_img = soup.select_one("#landingImage, #imgBlkFront, #main-image")
        if main_img:
            src = main_img.get("data-old-hires") or main_img.get("src", "")
            if src and src.startswith("http"):
                urls.append(_to_large_image(src))

        # サブ画像
        for img in soup.select("#altImages img, .imageThumbnail img"):
            src = img.get("src", "")
            if src and src.startswith("http") and "_SX" not in src and "_SS" not in src:
                large = _to_large_image(src)
                if large not in urls:
                    urls.append(large)

    # URL のクリーニング（不要なパラメータ除去）
    clean_urls: List[str] = []
    for u in urls:
        # Amazon CDN の画像URLパターンをクリーン
        u = re.sub(r"\._[A-Z0-9_,]+_\.", ".", u)
        # ._SX*_. → 削除して元URLに
        if u and u not in clean_urls and "amazon" in u:
            clean_urls.append(u)

    return clean_urls[:9]


def _to_large_image(url: str) -> str:
    """サムネイル URL → 大きい画像 URL に変換"""
    # ._SX150_.jpg → .jpg
    url = re.sub(r"\._[A-Z0-9_,]+_\.", ".", url)
    return url


def _extract_category(soup: BeautifulSoup) -> str:
    """パンくずリストからカテゴリを推定（内部カテゴリ値を返す）"""
    breadcrumb = soup.select("#wayfinding-breadcrumbs_feature_div a, .a-breadcrumb a")
    for tag in breadcrumb:
        text = tag.get_text(strip=True)
        for keyword, cat_val in _CATEGORY_MAP.items():
            if keyword in text:
                return cat_val
    return "other"


def _extract_availability(soup: BeautifulSoup) -> str:
    """在庫状況テキストを取得"""
    avail = soup.select_one("#availability span, #outOfStock span, #add-to-cart-button")
    if avail:
        text = avail.get_text(strip=True)
        if text:
            return text
    # カート追加ボタンの有無で判断
    cart_btn = soup.select_one("#add-to-cart-button, #buy-now-button")
    if cart_btn:
        return "在庫あり"
    return "不明"


def _extract_jan(soup: BeautifulSoup) -> Optional[str]:
    """JAN コードを技術仕様から取得"""
    # 技術仕様テーブル
    for row in soup.select("#productDetails_techSpec_section_1 tr, "
                            "#productDetails_detailBullets_sections1 tr"):
        th = row.select_one("th")
        td = row.select_one("td")
        if th and td:
            label = th.get_text(strip=True)
            if "JAN" in label or "EAN" in label or "バーコード" in label:
                val = td.get_text(strip=True)
                if re.match(r"^\d{13}$", val):
                    return val

    # リスト形式の詳細情報
    for li in soup.select("#detailBullets_feature_div li, #detail-bullets li"):
        text = li.get_text(" ", strip=True)
        m = re.search(r"(?:JAN|EAN)[：:\s]+(\d{13})", text)
        if m:
            return m.group(1)

    return None


def _extract_weight(soup: BeautifulSoup) -> Optional[float]:
    """商品重量をグラムで取得"""
    # 技術仕様テーブル
    for row in soup.select("#productDetails_techSpec_section_1 tr, "
                            "table.prodDetTable tr"):
        th = row.select_one("th, td:first-child")
        td = row.select_one("td:last-child")
        if th and td:
            label = th.get_text(strip=True)
            if "重量" in label or "Weight" in label:
                val = td.get_text(strip=True)
                return _parse_weight(val)
    return None


def _parse_weight(text: str) -> Optional[float]:
    """「500 g」「1.2 kg」→ グラムに変換"""
    m = re.search(r"([\d.]+)\s*(kg|g|Kg|KG|グラム|キログラム)", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).lower()
        if "kg" in unit or "キログラム" in unit:
            return val * 1000
        return val
    return None


def _extract_asin_from_page(soup: BeautifulSoup) -> Optional[str]:
    """ページから ASIN を確認（入力検証用）"""
    # 技術仕様の ASIN
    for li in soup.select("#detailBullets_feature_div li"):
        text = li.get_text(" ")
        if "ASIN" in text:
            m = re.search(r"B[A-Z0-9]{9}", text)
            if m:
                return m.group(0)
    return None


# ── ドメイン設定 ──────────────────────────────────────────────────
_REGION_CONFIG: Dict[str, Dict[str, str]] = {
    "jp": {
        "domain":    "amazon.co.jp",
        "lang":      "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "label":     "🇯🇵 JP",
    },
    "us": {
        "domain":    "amazon.com",
        "lang":      "en-US,en;q=0.9",
        "label":     "🇺🇸 US",
    },
}


def _get_headers_for_region(region: str = "jp") -> Dict[str, str]:
    cfg = _REGION_CONFIG.get(region, _REGION_CONFIG["jp"])
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": cfg["lang"],
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def _fetch_single_region(
    asin: str,
    region: str,
    rate_limit: bool,
) -> Dict[str, Any]:
    """指定リージョンの Amazon ページから商品データを取得する内部関数。"""
    cfg = _REGION_CONFIG.get(region, _REGION_CONFIG["jp"])
    url = f"https://www.{cfg['domain']}/dp/{asin}"
    logger.info("Amazon fetch [%s]: %s", region, url)

    if rate_limit:
        _rate_limit()

    try:
        session = requests.Session()
        session.headers.update(_get_headers_for_region(region))
        SCRAPER_API_KEY = "eebc37ad0fd5fe00854006b70ea2985"
        scraper_url = f"https://api.scraperapi.com/?api_key={SCRAPER_API_KEY}&url={url}"
        resp = session.get(scraper_url, timeout=60, allow_redirects=True)

        if resp.status_code == 503 or "captcha" in resp.url.lower():
            return _error_result(asin, url, f"[{region.upper()}] CAPTCHA が表示されました。")

        if resp.status_code == 404:
            return _error_result(asin, url, f"[{region.upper()}] ASIN {asin} のページが見つかりません（404）")

        if resp.status_code != 200:
            return _error_result(asin, url, f"[{region.upper()}] HTTP エラー: {resp.status_code}")

        soup = BeautifulSoup(resp.content, "lxml")

        if soup.find("form", {"action": "/errors/validateCaptcha"}):
            return _error_result(asin, url, f"[{region.upper()}] CAPTCHA が要求されました。")

        title    = _extract_title(soup)
        price    = _extract_price(soup)
        images   = _extract_images(soup)
        category = _extract_category(soup)
        avail    = _extract_availability(soup)
        jan      = _extract_jan(soup)
        weight   = _extract_weight(soup)

        return {
            "asin": asin,
            "name": title,
            "name_en": title if region == "us" else "",
            "price": price,
            "images": images,
            "category": category,
            "availability": avail,
            "jan_code": jan,
            "weight_g": weight,
            "url": url,
            "region": region,
            "error": None if title else f"[{region.upper()}] タイトルを取得できませんでした",
        }

    except requests.exceptions.Timeout:
        return _error_result(asin, url, f"[{region.upper()}] タイムアウト（15秒）")
    except requests.exceptions.ConnectionError as e:
        return _error_result(asin, url, f"[{region.upper()}] 接続エラー: {e}")
    except Exception as e:
        logger.exception("Unexpected error fetching ASIN %s [%s]", asin, region)
        return _error_result(asin, url, f"[{region.upper()}] 予期しないエラー: {e}")


# ── メイン関数 ─────────────────────────────────────────────────────

def fetch_product_by_asin(
    asin: str,
    rate_limit: bool = True,
    region: str = "both",
) -> Dict[str, Any]:
    """
    Amazon 商品ページから商品情報を取得する。

    Args:
        asin:       Amazon ASIN コード（例: B0BDHWDR12）
        rate_limit: True の場合 1〜3 秒の待機を行う
        region:     取得リージョン
                    "jp"   → amazon.co.jp のみ（日本語・価格）
                    "us"   → amazon.com のみ（英語画像）
                    "both" → 両方取得（価格はJP、画像はUS優先） ← デフォルト

    Returns:
        {
            "asin": str,
            "name": str,             # 商品名（日本語）
            "name_en": str,          # 商品名（英語 / US取得時）
            "price": float|None,     # 価格（円）
            "images": [str],         # 画像 URL リスト（最大9）
            "images_jp": [str],      # JP 画像（region="both" 時のみ）
            "images_us": [str],      # US 画像（region="both" 時のみ）
            "category": str,
            "availability": str,
            "jan_code": str|None,
            "weight_g": float|None,
            "url": str,
            "region": str,
            "error": str|None,
        }
    """
    asin = asin.strip().upper()

    # ASIN 形式チェック
    if not re.match(r"^B[A-Z0-9]{9}$", asin):
        return {
            "asin": asin, "name": "", "name_en": "", "price": None,
            "images": [], "images_jp": [], "images_us": [],
            "category": "other", "availability": "不明",
            "jan_code": None, "weight_g": None, "url": "", "region": region,
            "error": f"ASIN フォーマットエラー: 「{asin}」は B から始まる10桁である必要があります",
        }

    if region == "both":
        # JP と US を並列取得
        jp_result: Dict[str, Any] = {}
        us_result: Dict[str, Any] = {}

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_jp = executor.submit(_fetch_single_region, asin, "jp", rate_limit)
            fut_us = executor.submit(_fetch_single_region, asin, "us", rate_limit)
            jp_result = fut_jp.result()
            us_result = fut_us.result()

        images_jp = jp_result.get("images", [])
        images_us = us_result.get("images", [])
        # US 画像を優先。取得できなければ JP 画像
        images_primary = images_us if images_us else images_jp

        return {
            "asin":         asin,
            "name":         jp_result.get("name", ""),
            "name_en":      us_result.get("name", ""),  # US タイトル = 英語名
            "price":        jp_result.get("price"),
            "images":       images_primary,
            "images_jp":    images_jp,
            "images_us":    images_us,
            "category":     jp_result.get("category", "other"),
            "availability": jp_result.get("availability", "不明"),
            "jan_code":     jp_result.get("jan_code"),
            "weight_g":     jp_result.get("weight_g"),
            "url":          jp_result.get("url", ""),
            "url_us":       us_result.get("url", ""),
            "region":       "both",
            # JP が失敗した場合のみエラー扱い（US は任意）
            "error":        jp_result.get("error"),
            "error_us":     us_result.get("error"),
        }

    else:
        result = _fetch_single_region(asin, region, rate_limit)
        result["images_jp"] = result.get("images", []) if region == "jp" else []
        result["images_us"] = result.get("images", []) if region == "us" else []
        return result


def _error_result(asin: str, url: str, error: str) -> Dict[str, Any]:
    return {
        "asin": asin, "name": "", "name_en": "", "price": None,
        "images": [], "images_jp": [], "images_us": [],
        "category": "other", "availability": "不明",
        "jan_code": None, "weight_g": None, "url": url, "region": "jp",
        "error": error,
    }


def fetch_products_by_asins_parallel(
    asins: List[str],
    max_workers: int = 5,
    rate_limit: bool = True,
) -> List[Dict[str, Any]]:
    """
    複数 ASIN を並列取得する（最大 max_workers 同時実行）。

    1000 件でも max_workers=5・rate_limit=True の場合:
      約 1000 × (平均2秒) / 5 並列 ≈ 400 秒（7分弱）で完了。

    Args:
        asins:       ASIN リスト
        max_workers: 最大並列数（デフォルト 5）
        rate_limit:  各リクエストで 1〜3 秒待機するか

    Returns:
        各 ASIN の fetch_product_by_asin() 結果リスト（入力順）
    """
    results: Dict[str, Dict[str, Any]] = {}

    def _fetch(asin: str) -> tuple:
        result = fetch_product_by_asin(asin, rate_limit=rate_limit)
        return asin, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch, a): a for a in asins}
        for future in as_completed(futures):
            asin, result = future.result()
            results[asin] = result
            logger.info("並列取得完了: %s (エラー: %s)", asin, result.get("error"))

    # 入力順に並び替えて返す
    return [results[a] for a in asins if a in results]


# ── テスト実行 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    test_asin = sys.argv[1] if len(sys.argv) > 1 else "B09G9HD6PD"  # AirPods
    print(f"\n🔍 ASIN {test_asin} を取得中...")
    result = fetch_product_by_asin(test_asin)

    if result["error"]:
        print(f"❌ エラー: {result['error']}")
    else:
        print(f"✅ 商品名: {result['name']}")
        print(f"   価格: ¥{result['price']:,.0f}" if result["price"] else "   価格: 不明")
        print(f"   カテゴリ: {result['category']}")
        print(f"   在庫: {result['availability']}")
        print(f"   JAN: {result['jan_code'] or '—'}")
        print(f"   重量: {result['weight_g']}g" if result["weight_g"] else "   重量: 不明")
        print(f"   画像数: {len(result['images'])} 枚")
        for i, img in enumerate(result["images"][:3], 1):
            print(f"   画像{i}: {img[:80]}...")
