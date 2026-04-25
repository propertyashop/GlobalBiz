"""
Claude API を使った出品用英語コンテンツ自動生成

必要: pip install anthropic
APIキー: ANTHROPIC_API_KEY 環境変数
"""

import os
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def generate_listing_content(
    product_name_ja: str,
    description_ja: str = "",
    category: str = "other",
    product_name_en: str = "",
) -> Dict[str, Any]:
    """
    Claude API を使って eBay/Shopee 用英語コンテンツを生成する。

    Returns:
        {
            "ebay_title":         str,   # 80文字以内 eBay タイトル
            "shopee_title":       str,   # 120文字以内 Shopee タイトル
            "description_html":   str,   # HTML形式説明文（eBay用）
            "description_plain":  str,   # プレーンテキスト説明文（Shopee用）
            "features":           List[str],  # 特徴5項目
            "error":              Optional[str],
        }
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {
            "ebay_title": "", "shopee_title": "",
            "description_html": "", "description_plain": "",
            "features": [],
            "error": "Anthropic APIキーが設定されていません。設定画面で入力してください。",
        }

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        en_hint = f"\nEnglish Name (if available): {product_name_en}" if product_name_en else ""
        prompt = f"""You are an expert cross-border e-commerce copywriter specializing in Japanese products.

Product Name (Japanese): {product_name_ja}
Product Description (Japanese): {description_ja or "N/A"}
Category: {category}{en_hint}

Generate the following content in English for selling this Japanese product on eBay and Shopee:

1. EBAY_TITLE: Compelling eBay product title (max 80 characters, include key specs/keywords)
2. SHOPEE_TITLE: Shopee product title (max 120 characters, slightly more descriptive)
3. DESCRIPTION_HTML: Product description in HTML for eBay (use <b>, <ul>, <li> tags, 150-250 words)
4. DESCRIPTION_PLAIN: Same description in plain text for Shopee (150-250 words)
5. FEATURES: Exactly 5 key selling points (each on new line, start with ✓)

Format EXACTLY as:
EBAY_TITLE: [title here]
SHOPEE_TITLE: [title here]
DESCRIPTION_HTML:
[html content here]
DESCRIPTION_PLAIN:
[plain text here]
FEATURES:
✓ [feature 1]
✓ [feature 2]
✓ [feature 3]
✓ [feature 4]
✓ [feature 5]"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
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
            "error": f"APIエラー: {e}",
        }


def _parse_response(text: str) -> Dict[str, Any]:
    """Claude の応答テキストをパースして辞書に変換する。"""
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
