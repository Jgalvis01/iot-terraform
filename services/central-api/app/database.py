import time
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from wcommon.idempotency import ensure_tables
from wcommon.logging import log

from app.config import get_settings
from app.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_db():
    get_engine()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(max_wait_seconds: int = 60) -> None:
    engine = get_engine()
    delay, waited = 1.0, 0.0
    while True:
        try:
            Base.metadata.create_all(bind=engine)
            ensure_tables(engine)
            return
        except Exception as exc:
            if waited >= max_wait_seconds:
                raise
            log("db_init_retry", level="WARNING", error=repr(exc), wait_seconds=delay)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 5)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = as_utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None
