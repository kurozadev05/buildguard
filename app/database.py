from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_url = settings.db_url
is_sqlite = _url.startswith("sqlite")

_kw: dict = {"pool_pre_ping": True, "pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow,
             "pool_timeout": settings.db_pool_timeout, "pool_recycle": settings.db_pool_recycle}
if is_sqlite:
    _kw["connect_args"] = {"check_same_thread": False}
else:
    # Server-side cap so one slow query cannot hold a pooled connection forever.
    _kw["connect_args"] = {"options": f"-c statement_timeout={settings.db_statement_timeout_ms}"}

engine = create_engine(_url, **_kw)

if is_sqlite:
    # Recipe from the SQLAlchemy docs so SAVEPOINTs (used by offline sync) work with pysqlite.
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_con, _rec):
        dbapi_con.isolation_level = None
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
