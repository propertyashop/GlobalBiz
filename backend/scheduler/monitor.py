"""
在庫・価格監視スケジューラ (APScheduler)

ジョブ一覧:
  1. check_stock_levels()   — 30分ごと: 在庫アラート
  2. check_price_changes()  — 1時間ごと: 仕入れ元の価格変動チェック
  3. sync_ebay_inventory()  — 2時間ごと: eBay出品在庫と DB を同期
  4. daily_report()         — 毎朝9時: 日次レポートをログ出力

使い方 (Streamlit から):
    from backend.scheduler.monitor import get_scheduler, JOB_STATUS
    scheduler = get_scheduler()   # キャッシュ済みシングルトン
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ── ジョブ実行履歴（メモリ内） ──────────────────────────────────
JOB_HISTORY: List[Dict[str, Any]] = []   # 最大200件
MAX_HISTORY = 200

# ── アラート蓄積（Streamlit 画面で表示用） ─────────────────────
ALERTS: List[Dict[str, Any]] = []       # 最大100件
MAX_ALERTS = 100


def _add_history(job_name: str, status: str, message: str) -> None:
    JOB_HISTORY.append({
        "job":       job_name,
        "status":    status,      # "success" / "error" / "warning"
        "message":   message,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    if len(JOB_HISTORY) > MAX_HISTORY:
        JOB_HISTORY.pop(0)


def _add_alert(level: str, message: str, product_id: Optional[int] = None,
               product_name: str = "") -> None:
    ALERTS.append({
        "level":        level,   # "error" / "warning" / "info"
        "message":      message,
        "product_id":   product_id,
        "product_name": product_name,
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "read":         False,
    })
    if len(ALERTS) > MAX_ALERTS:
        ALERTS.pop(0)


# ══════════════════════════════════════════════════════════════════
#  JOB 1: 在庫レベル監視
# ══════════════════════════════════════════════════════════════════
def check_stock_levels() -> None:
    """
    全商品の在庫数を確認し、min_stock_alert 以下の商品をアラート。
    30分ごとに実行。
    """
    job_name = "check_stock_levels"
    logger.info("[%s] 開始", job_name)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from backend.db.database import get_session
        from backend.db.models import Product, ProductStatus

        with get_session() as s:
            products = s.query(Product).filter(
                Product.status == ProductStatus.ACTIVE
            ).all()

        alerts_added = 0
        for p in products:
            stock = int(p.current_stock or 0)
            threshold = int(p.min_stock_alert or 1)

            if stock == 0:
                _add_alert("error",
                           f"在庫ゼロ！ 補充が必要です。",
                           product_id=p.id,
                           product_name=p.name[:40])
                alerts_added += 1
                logger.warning("[%s] 在庫ゼロ: %s (id=%s)", job_name, p.name, p.id)

            elif stock <= threshold:
                _add_alert("warning",
                           f"在庫が少なくなっています（現在: {stock} / 閾値: {threshold}）",
                           product_id=p.id,
                           product_name=p.name[:40])
                alerts_added += 1
                logger.warning("[%s] 在庫低下: %s stock=%d", job_name, p.name, stock)

        msg = f"在庫チェック完了: {len(products)}件確認、{alerts_added}件アラート"
        _add_history(job_name, "success" if alerts_added == 0 else "warning", msg)
        logger.info("[%s] %s", job_name, msg)

    except Exception as e:
        msg = f"エラー: {e}"
        _add_history(job_name, "error", msg)
        logger.exception("[%s] %s", job_name, msg)


# ══════════════════════════════════════════════════════════════════
#  JOB 2: 仕入れ価格変動チェック
# ══════════════════════════════════════════════════════════════════
def check_price_changes() -> None:
    """
    Amazon / 楽天 / Yahoo の商品ページを確認し、仕入れ価格の変動を検出。
    1時間ごとに実行。
    """
    job_name = "check_price_changes"
    logger.info("[%s] 開始", job_name)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from backend.db.database import get_session
        from backend.db.models import Product, ProductStatus, SourceSite

        with get_session() as s:
            # ASIN / 楽天 / Yahoo コードを持つアクティブ商品
            products = s.query(Product).filter(
                Product.status == ProductStatus.ACTIVE,
                Product.source_url.isnot(None),
            ).all()

        checked = 0
        changed = 0

        for p in products:
            try:
                new_price = _fetch_current_price(p)
                if new_price is None:
                    continue

                checked += 1
                old_price = float(p.cost_price or 0)
                if old_price <= 0:
                    continue

                change_pct = (new_price - old_price) / old_price

                if abs(change_pct) >= 0.05:   # 5% 以上の変動
                    direction = "上昇" if change_pct > 0 else "下落"
                    level = "warning" if change_pct > 0 else "info"
                    _add_alert(
                        level,
                        f"仕入れ価格{direction}: ¥{old_price:,.0f} → ¥{new_price:,.0f} "
                        f"({change_pct*100:+.1f}%)",
                        product_id=p.id,
                        product_name=p.name[:40],
                    )
                    changed += 1
                    logger.info("[%s] 価格変動: %s ¥%d→¥%d (%+.1f%%)",
                                job_name, p.name[:30], old_price, new_price, change_pct*100)

            except Exception as inner_e:
                logger.debug("[%s] 商品 %s でエラー: %s", job_name, p.id, inner_e)

        msg = f"価格チェック完了: {checked}件確認、{changed}件変動検出"
        _add_history(job_name, "success" if changed == 0 else "warning", msg)
        logger.info("[%s] %s", job_name, msg)

    except Exception as e:
        msg = f"エラー: {e}"
        _add_history(job_name, "error", msg)
        logger.exception("[%s] %s", job_name, msg)


def _fetch_current_price(product) -> Optional[float]:
    """商品の現在仕入れ価格を取得（ソース別に対応）"""
    try:
        from backend.db.models import SourceSite

        if product.source_site == SourceSite.AMAZON and product.asin:
            from backend.scrapers.amazon import fetch_product_by_asin
            result = fetch_product_by_asin(product.asin, rate_limit=True)
            return result.get("price")

        elif product.source_site == SourceSite.RAKUTEN and product.rakuten_item_code:
            from backend.scrapers.rakuten import fetch_product_by_item_code
            result = fetch_product_by_item_code(product.rakuten_item_code)
            return result.get("price")

        elif product.source_site == SourceSite.YAHOO and product.yahoo_item_code:
            from backend.scrapers.yahoo import fetch_product_by_item_code
            result = fetch_product_by_item_code(product.yahoo_item_code)
            return result.get("price")

    except Exception as e:
        logger.debug("_fetch_current_price error: %s", e)
    return None


# ══════════════════════════════════════════════════════════════════
#  JOB 3: eBay 在庫同期
# ══════════════════════════════════════════════════════════════════
def sync_ebay_inventory() -> None:
    """
    eBay API の GetMyeBaySelling で出品中リストを取得し、
    DB の ebay_listing_id / status と突き合わせて同期。
    2時間ごとに実行。
    """
    job_name = "sync_ebay_inventory"
    logger.info("[%s] 開始", job_name)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from backend.db.database import get_session
        from backend.db.models import Product, ProductStatus
        import importlib
        import backend.marketplaces.ebay as ebay_mod
        importlib.reload(ebay_mod)
        client = ebay_mod.EbayClient()

        if not client.is_configured():
            _add_history(job_name, "warning", "eBay APIキー未設定のためスキップ")
            return

        live_listings, err = client.get_active_listings()
        if err:
            _add_history(job_name, "error", f"eBay API エラー: {err}")
            return

        live_ids = {item.item_id for item in live_listings}

        with get_session() as s:
            db_listed = s.query(Product).filter(
                Product.ebay_listing_id.isnot(None)
            ).all()

            ended_count = 0
            for p in db_listed:
                if p.ebay_listing_id not in live_ids:
                    # eBay 上で終了済み → DB を更新
                    p.ebay_listing_id = None
                    p.status = ProductStatus.DRAFT
                    ended_count += 1
                    logger.info("[%s] 出品終了を検出: %s", job_name, p.sku)
            s.commit()

        # 在庫更新
        with get_session() as s:
            for item in live_listings:
                p = s.query(Product).filter(
                    Product.ebay_listing_id == item.item_id
                ).first()
                if p and item.quantity != p.current_stock:
                    p.current_stock = item.quantity
            s.commit()

        msg = (f"eBay同期完了: 出品中 {len(live_listings)} 件, "
               f"終了検出 {ended_count} 件")
        _add_history(job_name, "success", msg)
        logger.info("[%s] %s", job_name, msg)

    except Exception as e:
        msg = f"エラー: {e}"
        _add_history(job_name, "error", msg)
        logger.exception("[%s] %s", job_name, msg)


# ══════════════════════════════════════════════════════════════════
#  JOB 4: 日次レポート
# ══════════════════════════════════════════════════════════════════
def daily_report() -> None:
    """毎朝9時に日次サマリをログ出力"""
    job_name = "daily_report"
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from backend.db.database import get_session
        from backend.db.models import Product, ProductStatus

        with get_session() as s:
            total    = s.query(Product).count()
            active   = s.query(Product).filter(Product.status == ProductStatus.ACTIVE).count()
            no_stock = s.query(Product).filter(Product.current_stock == 0).count()
            on_ebay  = s.query(Product).filter(Product.ebay_listing_id.isnot(None)).count()
            on_shopee = s.query(Product).filter(Product.shopee_item_id.isnot(None)).count()

        msg = (f"【日次レポート {datetime.now().strftime('%Y/%m/%d')}】 "
               f"総商品: {total}件 / 販売中: {active}件 / 在庫ゼロ: {no_stock}件 / "
               f"eBay出品: {on_ebay}件 / Shopee出品: {on_shopee}件")
        _add_history(job_name, "success", msg)
        logger.info(msg)

    except Exception as e:
        _add_history(job_name, "error", f"エラー: {e}")
        logger.exception("[%s] エラー", job_name)


# ══════════════════════════════════════════════════════════════════
#  スケジューラ シングルトン
# ══════════════════════════════════════════════════════════════════

_scheduler_instance = None


def get_scheduler():
    """
    BackgroundScheduler のシングルトンを返す。
    Streamlit の @st.cache_resource と組み合わせて使う。
    """
    global _scheduler_instance
    if _scheduler_instance is not None and _scheduler_instance.running:
        return _scheduler_instance

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
        import atexit

        scheduler = BackgroundScheduler(timezone="Asia/Tokyo")

        # ジョブ登録
        scheduler.add_job(
            check_stock_levels,
            trigger=IntervalTrigger(minutes=5),
            id="check_stock",
            name="在庫監視",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.add_job(
            check_price_changes,
            trigger=IntervalTrigger(minutes=15),
            id="check_price",
            name="価格変動チェック",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.add_job(
            sync_ebay_inventory,
            trigger=IntervalTrigger(minutes=15),
            id="sync_ebay",
            name="eBay在庫同期",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.add_job(
            daily_report,
            trigger=CronTrigger(hour=9, minute=0, timezone="Asia/Tokyo"),
            id="daily_report",
            name="日次レポート",
            replace_existing=True,
            max_instances=1,
        )

        scheduler.start()
        atexit.register(lambda: scheduler.shutdown(wait=False))
        _scheduler_instance = scheduler
        logger.info("スケジューラ起動完了（4ジョブ登録）")
        return scheduler

    except ImportError:
        logger.error("apscheduler が未インストールです: pip install apscheduler")
        return None
    except Exception as e:
        logger.exception("スケジューラ起動エラー: %s", e)
        return None


def get_scheduler_status() -> List[Dict[str, Any]]:
    """
    全ジョブのステータスを返す。

    Returns:
        [{"id", "name", "next_run", "last_status", "last_message"}]
    """
    global _scheduler_instance
    if _scheduler_instance is None or not _scheduler_instance.running:
        return []

    job_defs = {
        "check_stock":  {"interval": "5分ごと"},
        "check_price":  {"interval": "15分ごと"},
        "sync_ebay":    {"interval": "15分ごと"},
        "daily_report": {"interval": "毎朝9時"},
    }

    rows = []
    for job in _scheduler_instance.get_jobs():
        # 最新の履歴を探す
        history = [h for h in reversed(JOB_HISTORY) if h["job"] == job.func.__name__]
        last = history[0] if history else None

        next_run = job.next_run_time
        rows.append({
            "id":           job.id,
            "name":         job.name,
            "interval":     job_defs.get(job.id, {}).get("interval", "—"),
            "next_run":     next_run.strftime("%H:%M:%S") if next_run else "—",
            "last_status":  last["status"] if last else "未実行",
            "last_message": last["message"][:60] if last else "—",
            "last_time":    last["timestamp"] if last else "—",
        })
    return rows


def get_unread_alerts() -> List[Dict[str, Any]]:
    """未読アラートを返す"""
    return [a for a in ALERTS if not a["read"]]


def mark_alerts_read() -> None:
    """全アラートを既読にする"""
    for a in ALERTS:
        a["read"] = True


def trigger_job_now(job_id: str) -> bool:
    """指定ジョブを即時実行"""
    global _scheduler_instance
    if _scheduler_instance is None or not _scheduler_instance.running:
        return False
    try:
        _scheduler_instance.get_job(job_id).modify(next_run_time=datetime.now())
        return True
    except Exception as e:
        logger.error("trigger_job_now error: %s", e)
        return False
