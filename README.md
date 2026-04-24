# GlobalBiz - 越境EC運営ツール

Amazon/楽天/Yahoo/NETSEAから仕入れ、eBay/Shopeeへ販売する在庫・価格管理ツール。

## セットアップ

### 1. Python環境の準備

```bash
# 仮想環境を作成・有効化
python3.11 -m venv .venv
source .venv/bin/activate  # Mac/Linux

# 依存パッケージをインストール
pip install -r requirements.txt

# Playwright ブラウザをインストール（スクレイピング用）
playwright install chromium
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各APIキーを入力
```

### 3. 起動

```bash
# Streamlit 管理画面を起動（仮想環境）
streamlit run frontend/app.py

# システム Python を使う場合
python3 -m streamlit run frontend/app.py

# ブラウザで http://localhost:8501 を開く
```

## 機能一覧

| 機能 | 状態 |
|------|------|
| 商品登録・一覧 | ✅ |
| 在庫監視 (Amazon) | 🔧 実装中 |
| 在庫監視 (楽天) | 🔧 実装中 |
| 在庫監視 (Yahoo) | 🔧 実装中 |
| 在庫監視 (NETSEA) | 🔧 実装中 |
| eBay 出品 | 🔧 実装中 |
| Shopee 出品 | 🔧 実装中 |
| 価格自動計算 | 🔧 実装中 |
| 関税計算 | 🔧 実装中 |

## ディレクトリ構成

```
globalbiz/
├── frontend/
│   └── app.py          # Streamlit 管理画面
├── backend/
│   ├── db/
│   │   ├── database.py # SQLite 接続設定
│   │   └── models.py   # SQLAlchemy モデル
│   └── scrapers/       # 各サイトのスクレイパー（予定）
├── scripts/            # バッチ処理スクリプト
├── .env.example        # 環境変数サンプル
├── pyproject.toml
└── requirements.txt
```
