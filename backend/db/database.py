import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from backend.db.models import Base

load_dotenv()

# プロジェクトルートに DB ファイルを置く
_project_root = Path(__file__).parent.parent.parent
_db_path = _project_root / "globalbiz.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_db_path}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite + マルチスレッド対応
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    """SQLite のパフォーマンス設定"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """テーブルを作成（存在しない場合のみ）"""
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """セッションを取得（使用後は必ず close すること）"""
    return SessionLocal()
