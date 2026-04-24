"""
DBマイグレーション: 既存データを保持しながら新カラムを追加する。
SQLite は ALTER TABLE で一度に複数カラムを追加できないため、1本ずつ実行する。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from backend.db.database import _db_path, engine, init_db
from backend.db.models import Base

DB_PATH = str(_db_path)


def get_existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


NEW_COLUMNS: list[tuple[str, str]] = [
    # (カラム名, SQLite型定義)
    # ── 識別番号 ──
    ("asin",                "VARCHAR(20)"),
    ("rakuten_item_code",   "VARCHAR(200)"),
    ("yahoo_item_code",     "VARCHAR(200)"),
    ("netsea_product_id",   "VARCHAR(100)"),
    ("jan_code",            "VARCHAR(13)"),
    ("upc_code",            "VARCHAR(12)"),
    # ── 利益計算 ──
    ("tariff_amount",       "FLOAT"),
    ("ebay_fee_rate",       "FLOAT DEFAULT 0.13"),
    ("shopee_fee_rate",     "FLOAT DEFAULT 0.06"),
    ("payment_fee_rate",    "FLOAT DEFAULT 0.044"),
    ("target_profit_rate",  "FLOAT"),
    ("calc_selling_price_usd", "FLOAT"),
    ("calc_selling_price_sgd", "FLOAT"),
    ("target_countries",    "JSON"),
    # ── カテゴリ・サイズ ──
    ("product_category",    "VARCHAR(50)"),
    ("size_class",          "VARCHAR(20)"),
    ("size_cm_l",           "FLOAT"),
    ("size_cm_w",           "FLOAT"),
    ("size_cm_h",           "FLOAT"),
    # ── 多言語・CSV出品用（v2追加）──
    ("product_name_en",         "VARCHAR(500)"),
    ("product_description_en",  "TEXT"),
    ("shopee_category_id",      "INTEGER"),
    ("image_urls",              "JSON"),
    ("condition",               "VARCHAR(20) DEFAULT 'New'"),
]


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # テーブルが存在しない場合は SQLAlchemy で作成
    init_db()

    existing = get_existing_columns(cursor, "products")
    print(f"既存カラム数: {len(existing)}")

    added = 0
    skipped = 0
    for col_name, col_def in NEW_COLUMNS:
        if col_name in existing:
            print(f"  SKIP  {col_name} （既存）")
            skipped += 1
        else:
            sql = f"ALTER TABLE products ADD COLUMN {col_name} {col_def}"
            cursor.execute(sql)
            print(f"  ADD   {col_name}")
            added += 1

    conn.commit()
    conn.close()

    print(f"\n完了: {added} カラム追加, {skipped} カラムスキップ")

    # 既存データの確認
    from backend.db.database import get_session
    from backend.db.models import Product
    with get_session() as s:
        products = s.query(Product).all()
        print(f"\n既存商品データ: {len(products)} 件")
        for p in products:
            print(f"  [{p.id}] {p.sku} - {p.name[:40]}")

    print("\nマイグレーション成功 ✅")


if __name__ == "__main__":
    migrate()
