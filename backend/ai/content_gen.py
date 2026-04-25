"""
eBay/Shopee 向け英語コンテンツ自動生成

2モード:
  1. Claude API（ANTHROPIC_API_KEY 設定済み）→ 高品質・自然な英語
  2. テンプレートベース（APIキーなし）→ カテゴリ別テンプレート＋DeepL/Google翻訳

必要: pip install anthropic  ← モード1のみ
"""

import os
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ── カテゴリ別「日本製品アピール」キーワード ──────────────────────────
_CATEGORY_APPEAL: Dict[str, Dict[str, str]] = {
    "electronics": {
        "headline":  "Japanese Technology",
        "keywords":  "Japanese Technology, High Performance, Precision Engineering",
        "tone":      "technical and reliable",
    },
    "cosmetics": {
        "headline":  "Japanese Beauty Secret",
        "keywords":  "Japanese Beauty, Skin-Safe Formula, J-Beauty",
        "tone":      "gentle, luxurious, skin-focused",
    },
    "health": {
        "headline":  "Japanese Wellness",
        "keywords":  "Japanese Quality, Health & Wellness, Natural Formula",
        "tone":      "trustworthy and health-focused",
    },
    "home": {
        "headline":  "Japanese Craftsmanship",
        "keywords":  "Japanese Craftsmanship, Premium Quality, Minimalist Design",
        "tone":      "elegant and functional",
    },
    "food": {
        "headline":  "Authentic Japanese Taste",
        "keywords":  "Authentic Japanese, Premium Ingredients, Traditional Recipe",
        "tone":      "authentic and appetizing",
    },
    "sports": {
        "headline":  "Japanese Precision",
        "keywords":  "Japanese Precision, Durable Build, Professional Grade",
        "tone":      "energetic and performance-focused",
    },
    "tools": {
        "headline":  "Japanese Craftsmanship",
        "keywords":  "Japanese Tools, Precision Made, Professional Grade",
        "tone":      "precise and reliable",
    },
    "toys": {
        "headline":  "Japanese Quality Toy",
        "keywords":  "Japanese Toy, Safe Materials, Fun & Educational",
        "tone":      "fun and reassuring for parents",
    },
    "clothing": {
        "headline":  "Japanese Fashion",
        "keywords":  "Japanese Fashion, Quality Fabric, Stylish Design",
        "tone":      "stylish and quality-focused",
    },
    "accessories": {
        "headline":  "Japanese Design",
        "keywords":  "Japanese Design, Premium Quality, Elegant Style",
        "tone":      "elegant and refined",
    },
    "books": {
        "headline":  "Japanese Culture",
        "keywords":  "Made in Japan, Authentic Japanese, Cultural Product",
        "tone":      "informative and cultural",
    },
    "hobby": {
        "headline":  "Japanese Quality Collectible",
        "keywords":  "Japanese Collectible, High Detail, Authentic",
        "tone":      "enthusiastic and detail-focused",
    },
    "pets": {
        "headline":  "Japanese Pet Care",
        "keywords":  "Japanese Pet Care, Safe Materials, Premium Quality",
        "tone":      "caring and pet-safety focused",
    },
    "auto": {
        "headline":  "Japanese Auto Parts",
        "keywords":  "Japanese Automotive, OEM Quality, Precision Fit",
        "tone":      "technical and reliable",
    },
    "other": {
        "headline":  "Premium Japanese Quality",
        "keywords":  "Made in Japan, Premium Quality, Authentic Japanese",
        "tone":      "professional and trustworthy",
    },
}


def generate_listing_content(
    product_name_ja: str,
    description_ja: str = "",
    category: str = "other",
    product_name_en: str = "",
) -> Dict[str, Any]:
    """
    eBay/Shopee 用英語コンテンツを生成する。

    Claude API が設定されていれば高品質生成、
    なければテンプレート＋翻訳のフォールバックを使用。

    Returns:
        {
            "ebay_title":         str,
            "shopee_title":       str,
            "description_html":   str,   # HTML形式（eBay用）
            "description_plain":  str,   # プレーンテキスト（Shopee用）
            "features":           List[str],
            "source":             str,   # "claude" / "template"
            "error":              Optional[str],
        }
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    if api_key:
        result = _generate_with_claude(api_key, product_name_ja, description_ja,
                                       category, product_name_en)
        if not result.get("error"):
            result["source"] = "claude"
            return result
        # Claude失敗時はテンプレートにフォールバック
        logger.warning("Claude API failed, falling back to template: %s", result["error"])

    result = _generate_with_template(product_name_ja, description_ja, category,
                                     product_name_en)
    result["source"] = "template"
    return result


# ════════════════════════════════════════════════════════════════
#  Claude API 生成
# ════════════════════════════════════════════════════════════════

def _generate_with_claude(
    api_key: str,
    product_name_ja: str,
    description_ja: str,
    category: str,
    product_name_en: str,
) -> Dict[str, Any]:
    appeal = _CATEGORY_APPEAL.get(category, _CATEGORY_APPEAL["other"])

    try:
        import anthropic
        model = os.getenv("AI_MODEL", "claude-sonnet-4-5")
        client = anthropic.Anthropic(api_key=api_key)

        en_hint = f"\nEnglish Name: {product_name_en}" if product_name_en else ""
        prompt = f"""You are an expert cross-border e-commerce copywriter specializing in Japanese products.
Appeal angle: "{appeal['headline']}" — Use keywords: {appeal['keywords']}
Writing tone: {appeal['tone']}

Product Name (Japanese): {product_name_ja}
Product Description (Japanese): {description_ja or "N/A"}
Category: {category}{en_hint}

Generate the following in English to sell this Japanese product on eBay and Shopee:

1. EBAY_TITLE: Max 80 characters. Format: "Japanese [Product] - [Key Feature]"
2. SHOPEE_TITLE: Max 120 chars. Include 2-3 relevant emojis (🇯🇵 ✅ ⭐ etc.)
3. DESCRIPTION_HTML: Rich HTML for eBay using this structure:
   - <h2>🇯🇵 {appeal['headline']}</h2>
   - <ul> with 5 key features using ✅ icons
   - <h3>Product Specifications</h3> with <table>
   - <h3>Why Buy Japanese?</h3> with 2-3 selling points
   - <p>Shipping & Guarantee info</p>
4. DESCRIPTION_PLAIN: Shopee version with ★ section dividers and emojis (150-200 words)
5. FEATURES: Exactly 5 key selling points (start each with ✓)

Format EXACTLY as:
EBAY_TITLE: [title]
SHOPEE_TITLE: [title]
DESCRIPTION_HTML:
[html]
DESCRIPTION_PLAIN:
[plain text]
FEATURES:
✓ [feature 1]
✓ [feature 2]
✓ [feature 3]
✓ [feature 4]
✓ [feature 5]"""

        message = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_response(message.content[0].text)

    except ImportError:
        return {
            "ebay_title": "", "shopee_title": "",
            "description_html": "", "description_plain": "",
            "features": [],
            "error": "anthropicパッケージが未インストールです: pip install anthropic",
        }
    except Exception as e:
        logger.exception("Claude API error: %s", e)
        return {
            "ebay_title": "", "shopee_title": "",
            "description_html": "", "description_plain": "",
            "features": [],
            "error": f"Claude APIエラー: {e}",
        }


# ════════════════════════════════════════════════════════════════
#  テンプレートベース生成（APIキーなし時のフォールバック）
# ════════════════════════════════════════════════════════════════

def _generate_with_template(
    product_name_ja: str,
    description_ja: str,
    category: str,
    product_name_en: str,
) -> Dict[str, Any]:
    """テンプレート＋翻訳APIでコンテンツを生成する。"""
    appeal = _CATEGORY_APPEAL.get(category, _CATEGORY_APPEAL["other"])

    # 英語名の確定（渡されていれば使用、なければ翻訳）
    en_name = product_name_en
    if not en_name:
        try:
            from backend.translators.translate import translate_to_english
            en_name, _ = translate_to_english(product_name_ja)
        except Exception:
            en_name = product_name_ja

    en_desc = ""
    if description_ja:
        try:
            from backend.translators.translate import translate_to_english
            en_desc, _ = translate_to_english(description_ja)
        except Exception:
            en_desc = ""

    # タイトル生成
    ebay_title = f"Japanese {en_name} - {appeal['headline']}"[:80]
    shopee_title = f"🇯🇵 {en_name} ✅ {appeal['headline']} | Premium Japanese Quality"[:120]

    # 特徴リスト（テンプレート）
    features = _get_template_features(category, en_name)

    # HTML説明文（eBay用）
    desc_body = en_desc or f"High quality {en_name} from Japan. {appeal['keywords']}."
    features_html = "\n".join(f"  <li>✅ {f}</li>" for f in features)
    description_html = f"""<h2>🇯🇵 {appeal['headline']}</h2>
<p>{desc_body}</p>
<h3>Key Features</h3>
<ul>
{features_html}
</ul>
<h3>Why Buy Japanese?</h3>
<ul>
  <li>🏭 Manufactured to strict Japanese quality standards</li>
  <li>✅ Inspected and verified before shipping</li>
  <li>📦 Carefully packaged for safe international delivery</li>
</ul>
<h3>Shipping &amp; Guarantee</h3>
<p>📬 Ships from Japan via EMS/FedEx. Usually delivered within 7-14 business days.<br>
💯 100% authentic Japanese product. Contact us with any questions!</p>"""

    # プレーンテキスト（Shopee用）
    features_plain = "\n".join(f"✅ {f}" for f in features)
    description_plain = f"""🇯🇵 {appeal['headline']}

{desc_body}

★ KEY FEATURES ★
{features_plain}

★ WHY JAPANESE QUALITY? ★
🏭 Made with Japanese precision and craftsmanship
✅ Strict quality control — passed inspection before shipping
📦 Carefully packaged for safe delivery

★ SHIPPING ★
Ships from Japan 🇯🇵 | EMS / FedEx | 7-14 business days
💬 Any questions? Message us anytime!"""

    return {
        "ebay_title":        ebay_title,
        "shopee_title":      shopee_title,
        "description_html":  description_html,
        "description_plain": description_plain,
        "features":          features,
        "error":             None,
    }


def _get_template_features(category: str, product_name: str) -> List[str]:
    """カテゴリ別のデフォルト特徴リストを返す。"""
    templates: Dict[str, List[str]] = {
        "electronics": [
            f"Premium Japanese {product_name} with advanced technology",
            "High performance and reliability — built to Japanese standards",
            "Precision engineering for optimal performance",
            "Energy efficient design",
            "Easy to use with intuitive controls",
        ],
        "cosmetics": [
            f"Authentic Japanese {product_name} for beautiful skin",
            "Gentle, skin-safe formula tested by dermatologists",
            "Natural Japanese ingredients — no harsh chemicals",
            "Suitable for sensitive skin",
            "Visible results with regular use",
        ],
        "health": [
            f"Premium quality {product_name} from Japan",
            "Made with high-quality ingredients and strict quality control",
            "Effective and safe for daily use",
            "Trusted by Japanese consumers for years",
            "Easy to incorporate into your daily routine",
        ],
        "food": [
            f"Authentic Japanese {product_name} — genuine taste of Japan",
            "Premium ingredients sourced from Japan",
            "Traditional recipe preserving original flavor",
            "No artificial preservatives or additives",
            "Perfect as a gift or personal treat",
        ],
        "sports": [
            f"Professional-grade Japanese {product_name}",
            "Durable construction for long-lasting performance",
            "Japanese precision engineering for accuracy",
            "Lightweight yet sturdy design",
            "Trusted by athletes and sports enthusiasts",
        ],
        "home": [
            f"Elegantly designed {product_name} — Japanese minimalist style",
            "Premium materials for lasting quality",
            "Practical design optimized for everyday use",
            "Easy to clean and maintain",
            "Complements any home décor",
        ],
        "tools": [
            f"Professional Japanese {product_name}",
            "Precision-crafted for accurate results",
            "Durable high-grade materials",
            "Ergonomic design reduces fatigue",
            "Trusted by Japanese craftsmen",
        ],
        "hobby": [
            f"High-detail Japanese {product_name} for collectors",
            "Authentic Japanese design and craftsmanship",
            "Limited availability — genuine Japanese import",
            "Perfect for display or collection",
            "Great gift for enthusiasts",
        ],
        "pets": [
            f"Premium Japanese {product_name} for your beloved pet",
            "Safe, non-toxic materials — pet-tested",
            "Designed with pet comfort and safety in mind",
            "Durable construction withstands daily use",
            "Trusted by Japanese pet owners",
        ],
    }
    default = [
        f"Authentic Japanese {product_name} — premium quality",
        "Made in Japan with strict quality control",
        "Durable and reliable for everyday use",
        "Carefully inspected before shipping",
        "Perfect as a gift or personal use",
    ]
    return templates.get(category, default)


# ════════════════════════════════════════════════════════════════
#  レスポンスパーサー
# ════════════════════════════════════════════════════════════════

def _parse_response(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ebay_title": "",
        "shopee_title": "",
        "description_html": "",
        "description_plain": "",
        "features": [],
        "error": None,
    }

    lines = text.split("\n")
    current_section: Optional[str] = None
    buffer: List[str] = []

    for line in lines:
        if line.startswith("EBAY_TITLE:"):
            result["ebay_title"] = line.replace("EBAY_TITLE:", "").strip()
            current_section = None
        elif line.startswith("SHOPEE_TITLE:"):
            result["shopee_title"] = line.replace("SHOPEE_TITLE:", "").strip()
            current_section = None
        elif line.startswith("DESCRIPTION_HTML:"):
            _flush(result, current_section, buffer)
            current_section = "html"
            buffer = []
            inline = line.replace("DESCRIPTION_HTML:", "").strip()
            if inline:
                buffer.append(inline)
        elif line.startswith("DESCRIPTION_PLAIN:"):
            _flush(result, current_section, buffer)
            current_section = "plain"
            buffer = []
            inline = line.replace("DESCRIPTION_PLAIN:", "").strip()
            if inline:
                buffer.append(inline)
        elif line.startswith("FEATURES:"):
            _flush(result, current_section, buffer)
            current_section = "features"
            buffer = []
        elif current_section == "features" and line.strip().startswith("✓"):
            result["features"].append(line.strip()[1:].strip())
        elif current_section in ("html", "plain"):
            buffer.append(line)

    _flush(result, current_section, buffer)
    return result


def _flush(result: Dict[str, Any], section: Optional[str], buffer: List[str]) -> None:
    if section == "html" and buffer:
        result["description_html"] = "\n".join(buffer).strip()
    elif section == "plain" and buffer:
        result["description_plain"] = "\n".join(buffer).strip()


def is_configured() -> bool:
    """Anthropic APIキーが設定されているか確認"""
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
