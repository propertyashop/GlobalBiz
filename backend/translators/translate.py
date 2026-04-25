"""
翻訳モジュール

優先順位:
  1. DeepL API（DEEPL_API_KEY が設定されている場合）
  2. Google翻訳（無料・APIキー不要）

対応翻訳:
  - 日本語 → 英語
  - 日本語 → 繁体字中国語（台湾 Shopee 用）
"""

import os
import logging
import requests
import urllib.parse
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_URL  = "https://api.deepl.com/v2/translate"


def _deepl_url() -> str:
    """DeepL API エンドポイントを返す（Free/Pro 自動判定）"""
    key = os.getenv("DEEPL_API_KEY", "")
    return DEEPL_FREE_URL if key.endswith(":fx") else DEEPL_PRO_URL


def _translate_deepl(
    text: str,
    target_lang: str,
    source_lang: str = "JA",
) -> Optional[str]:
    """DeepL API で翻訳する。失敗時は None を返す。"""
    api_key = os.getenv("DEEPL_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        resp = requests.post(
            _deepl_url(),
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
            json={
                "text": [text],
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["translations"][0]["text"]
    except Exception as e:
        logger.warning("DeepL translation failed: %s", e)
        return None


def _translate_google(
    text: str,
    target_lang: str,
    source_lang: str = "ja",
) -> Optional[str]:
    """Google翻訳の非公式 API（無料）。失敗時は None を返す。"""
    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={source_lang}&tl={target_lang}"
            f"&dt=t&q={urllib.parse.quote(text)}"
        )
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(chunk[0] for chunk in data[0] if chunk[0])
    except Exception as e:
        logger.warning("Google translate failed: %s", e)
        return None


def translate_to_english(text: str) -> Tuple[str, str]:
    """
    日本語テキストを英語に翻訳する。

    Returns:
        (translated_text, method)  method: "deepl" / "google" / "error"
    """
    if not text.strip():
        return "", "error"

    result = _translate_deepl(text, target_lang="EN-US", source_lang="JA")
    if result:
        return result, "deepl"

    result = _translate_google(text, target_lang="en", source_lang="ja")
    if result:
        return result, "google"

    return "", "error"


def translate_to_traditional_chinese(text: str) -> Tuple[str, str]:
    """
    日本語テキストを繁体字中国語に翻訳する（台湾 Shopee 用）。

    Returns:
        (translated_text, method)  method: "deepl" / "google" / "error"
    """
    if not text.strip():
        return "", "error"

    # DeepL: ZH（繁体字。簡体字は ZH-HANS）
    result = _translate_deepl(text, target_lang="ZH", source_lang="JA")
    if result:
        return result, "deepl"

    # Google: zh-TW（繁体字）
    result = _translate_google(text, target_lang="zh-TW", source_lang="ja")
    if result:
        return result, "google"

    return "", "error"


def is_deepl_configured() -> bool:
    """DeepL APIキーが設定されているか確認"""
    return bool(os.getenv("DEEPL_API_KEY", "").strip())
